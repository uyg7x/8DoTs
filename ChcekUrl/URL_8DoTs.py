
import sys
import os
import time
import json
import io
import contextlib
import re
import argparse
from datetime import datetime
from typing import List, Dict, Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from playwright.sync_api import sync_playwright
    from dol8.rapl import RAPLReader, RaplUnavailable
except ImportError as e:
    print(f"[ERROR] Import Error: {e}")
    print("[TIP] Please run: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("[ERROR] Missing 'rich' library. Please run: pip install rich")
    sys.exit(1)

console = Console()

def strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

# ─── THE ENHANCED JS INJECTION (Now includes Network Metrics) ───
JS_INJECTION = """
document.addEventListener('click', (e) => {
    const el = e.target;
    const name = el.id || el.innerText || el.className || el.tagName;
    console.log('8DOTS_EVENT: CLICK -> ' + name.trim().substring(0, 30));
}, true);

const originalFetch = window.fetch;
window.fetch = async function(...args) {
    console.log('8DOTS_EVENT: FETCH -> ' + args[0]);
    return originalFetch.apply(this, args);
};

window.__8dots_get_network_metrics = function() {
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const resources = performance.getEntriesByType('resource');
    
    let totalTransferSize = 0;
    let scriptCount = 0;
    let imageCount = 0;

    resources.forEach(res => {
        totalTransferSize += res.transferSize || 0;
        if (res.initiatorType === 'script') scriptCount++;
        if (res.initiatorType === 'img' || res.initiatorType === 'css') imageCount++;
    });

    const durationSec = (nav.loadEventEnd - nav.startTime) / 1000 || 1;
    const speedMbps = ((totalTransferSize * 8) / (durationSec * 1000000)).toFixed(2);
    const sizeMB = (totalTransferSize / 1000000).toFixed(2);

    return {
        load_time_ms: Math.round(nav.loadEventEnd - nav.startTime) || 0,
        dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd - nav.startTime) || 0,
        total_payload_mb: sizeMB,
        estimated_speed_mbps: speedMbps,
        total_requests: resources.length,
        scripts_loaded: scriptCount,
        images_loaded: imageCount
    };
};

window.__8dots_analyze_page = function() {
    const images = document.querySelectorAll('img');
    let largeImages = 0, uncompressedImages = 0;
    images.forEach(img => {
        if (img.naturalWidth * img.naturalHeight > 2000000) largeImages++;
        if (img.src && (img.src.endsWith('.png') || img.src.endsWith('.bmp') || img.src.endsWith('.gif'))) uncompressedImages++;
    });
    
    let animationCount = 0;
    document.querySelectorAll('*').forEach(el => {
        const style = window.getComputedStyle(el);
        if (style.animationName && style.animationName !== 'none') animationCount++;
    });

    const scripts = document.querySelectorAll('script[src]');
    let thirdPartyScripts = 0;
    scripts.forEach(s => { if (!s.src.includes(window.location.hostname)) thirdPartyScripts++; });
    
    const analysis = {
        images_total: images.length, large_images: largeImages, uncompressed_images: uncompressedImages,
        css_animations: animationCount, dom_elements: document.querySelectorAll('*').length, 
        third_party_scripts: thirdPartyScripts, iframes: document.querySelectorAll('iframe').length,
        autoplay_videos: document.querySelectorAll('video[autoplay]').length,
        canvas_webgl: document.querySelectorAll('canvas').length
    };
    console.log('8DOTS_ANALYSIS: ' + JSON.stringify(analysis));
    return analysis;
};
"""

class AutoWebProfiler:
    def __init__(self):
        self.reader = RAPLReader()
        self._file_handles = {}
        self._ranges = {}
        self.actions: List[Dict[str, Any]] = []
        self.page_analyses: Dict[str, Dict] = {}
        self.last_readings: Dict[str, int] = {}
        self.last_time = 0.0
        self.session_start_time = 0.0
        self.network_metrics = {}

    def _init_rapl(self):
        if not self.reader.available():
            raise RaplUnavailable(self.reader.unavailable_reason)
        domains = self.reader.domains()
        self._ranges = self.reader.ranges()
        for domain, path_str in domains.items():
            self._file_handles[domain] = open(path_str, "r", encoding="ascii")
        self.last_readings = self._read_rapl_now()
        self.last_time = time.perf_counter()
        self.session_start_time = self.last_time
        console.print("[bold green][SUCCESS][/bold green] RAPL initialized with zero-overhead cached handles.")

    def _read_rapl_now(self) -> Dict[str, int]:
        readings = {}
        for domain, handle in self._file_handles.items():
            handle.seek(0)
            readings[domain] = int(handle.read().strip())
        return readings

    def _record_event(self, event_name: str):
        current_time = time.perf_counter()
        current_readings = self._read_rapl_now()
        delta = self.reader.delta(self.last_readings, current_readings, self._ranges)
        energy_joules = sum(delta.values())
        duration_s = current_time - self.last_time
        avg_watts = energy_joules / duration_s if duration_s > 0 else 0.0

        self.actions.append({
            "event": event_name,
            "duration_s": round(duration_s, 4),
            "energy_j": round(energy_joules, 4),
            "avg_watts": round(avg_watts, 3),
            "timestamp": round(current_time - self.session_start_time, 3)
        })
        self.last_readings = current_readings
        self.last_time = current_time

    def _store_analysis(self, tab_name: str, analysis: Dict):
        self.page_analyses[tab_name] = analysis

    def _cleanup_rapl(self):
        for handle in self._file_handles.values():
            try: handle.close()
            except Exception: pass
        self._file_handles = {}

    def _generate_recommendations(self, analysis: Dict) -> List[str]:
        recs = []
        if analysis.get("large_images", 0) > 0:
            recs.append(f"[blue]*[/blue] {analysis['large_images']} large image(s) found. Use responsive images (srcset) or lazy loading.")
        if analysis.get("uncompressed_images", 0) > 0:
            recs.append(f"[yellow]*[/yellow] {analysis['uncompressed_images']} PNG/BMP/GIF image(s). Convert to WebP/AVIF for 30-50% smaller size.")
        if analysis.get("css_animations", 0) > 3:
            recs.append(f"[magenta]*[/magenta] {analysis['css_animations']} CSS animations running. Reduce or use 'prefers-reduced-motion'.")
        if analysis.get("dom_elements", 0) > 1500:
            recs.append(f"[green]*[/green] {analysis['dom_elements']} DOM elements. Simplify HTML structure to reduce rendering cost.")
        if analysis.get("third_party_scripts", 0) > 2:
            recs.append(f"[cyan]*[/cyan] {analysis['third_party_scripts']} third-party scripts. Defer or async load non-critical ones.")
        if analysis.get("iframes", 0) > 0:
            recs.append(f"[blue]*[/blue] {analysis['iframes']} iframe(s). Lazy-load iframes that are below the fold.")
        if analysis.get("autoplay_videos", 0) > 0:
            recs.append(f"[red]*[/red] {analysis['autoplay_videos']} autoplay video(s). Remove autoplay or use a poster image.")
        if analysis.get("canvas_webgl", 0) > 0:
            recs.append(f"[magenta]*[/magenta] {analysis['canvas_webgl']} canvas/WebGL element(s). GPU-intensive. Optimize render loops.")
        if not recs:
            recs.append("[green]*[/green] This page looks well-optimized! No major energy concerns detected.")
        return recs

    def _print_report(self, url: str):
        if not self.actions:
            console.print("\n[yellow][WARNING][/yellow] No events were captured.")
            return

        total_time = round(time.perf_counter() - self.session_start_time, 2)
        total_energy_j = sum(a['energy_j'] for a in self.actions)
        total_kwh = total_energy_j / 3_600_000.0
        estimated_cost = total_kwh * 0.15
        estimated_co2_grams = total_kwh * 450.0

        max_energy_action = max(self.actions, key=lambda x: x["energy_j"])
        max_time_action = max(self.actions, key=lambda x: x["duration_s"])

        console.print("\n" + "="*90)
        console.print(Panel.fit("[bold cyan]8DoTs AUTOMATED WEB ENERGY REPORT + INTELLIGENT ADVISOR[/bold cyan]", border_style="cyan"))
        console.print(f" [white]Target URL       :[/white] {url}")
        
        # NEW: Network Metrics Display
        if self.network_metrics:
            console.print(f" [white]Payload Size     :[/white] {self.network_metrics.get('total_payload_mb', 'N/A')} MB")
            console.print(f" [white]Load Time        :[/white] {self.network_metrics.get('load_time_ms', 'N/A')} ms")
            console.print(f" [white]Est. Speed       :[/white] {self.network_metrics.get('estimated_speed_mbps', 'N/A')} Mbps")
            
        console.print(f" [white]Total Session    :[/white] {total_time} seconds")
        console.print(f" [white]Total Energy     :[/white] {total_energy_j:.4f} Joules ({total_kwh:.8f} kWh)")
        console.print(f" [green][COST][/green] Est. Cloud Cost: ${estimated_cost:.8f} (at $0.15/kWh)")
        console.print(f" [green][CO2][/green]  Est. CO2 Emit  : {estimated_co2_grams:.4f} grams (at 450g/kWh)")
        console.print(f" [white]Tabs/Events      :[/white] {len(self.actions)}")
        console.print("="*90)

        console.print("\n[bold blue][BREAKDOWN] ENERGY PER TAB/ACTION:[/bold blue]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", width=4)
        table.add_column("Tab/Action", width=35)
        table.add_column("Time (s)", justify="right")
        table.add_column("Energy (J)", justify="right")
        table.add_column("Avg Watts", justify="right")

        for i, a in enumerate(self.actions, 1):
            marker = " [red][HIGH][/red]" if a == max_energy_action else ""
            table.add_row(str(i), a['event'] + marker, f"{a['duration_s']:.3f}", f"{a['energy_j']:.4f}", f"{a['avg_watts']:.3f}")
        console.print(table)

        console.print("\n[bold cyan][INSIGHT] WHY DOES EACH TAB USE ENERGY? + WHAT TO IMPROVE:[/bold cyan]")
        for tab_name, analysis in self.page_analyses.items():
            tab_action = next((a for a in self.actions if tab_name in a["event"]), None)
            energy_str = f"{tab_action['energy_j']} J" if tab_action else "N/A"

            console.print(f"\n[yellow][TAB][/yellow] '{tab_name}' [white](Energy:[/white] {energy_str}[white])[/white]")
            console.print(f"   [white]Page Metrics:[/white]")
            console.print(f"     - Images: {analysis.get('images_total', 0)} total, {analysis.get('large_images', 0)} large, {analysis.get('uncompressed_images', 0)} uncompressed")
            console.print(f"     - CSS Animations: {analysis.get('css_animations', 0)}")
            console.print(f"     - DOM Elements: {analysis.get('dom_elements', 0)}")
            console.print(f"     - Third-party Scripts: {analysis.get('third_party_scripts', 0)}")
            
            recs = self._generate_recommendations(analysis)
            console.print(f"   [green]Recommendations:[/green]")
            for rec in recs:
                console.print(f"     {rec}")

        console.print("\n" + "="*90)
        console.print("[bold yellow][SUMMARY] OVERALL INSIGHTS:[/bold yellow]")
        console.print(f"   [red]*[/red] Most energy-intensive : '{max_energy_action['event']}' ({max_energy_action['energy_j']} J)")
        console.print(f"   [cyan]*[/cyan] Slowest loading       : '{max_time_action['event']}' ({max_time_action['duration_s']} s)")
        console.print("="*90 + "\n")


def save_reports(url: str, report_text: str):
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')
    os.makedirs(report_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_url = url.replace("http://", "").replace("https://", "").replace("/", "_").replace(":", "").replace("#", "")[:30]
    base_name = f"8DoTs_Report_{safe_url}_{timestamp}"
    
    clean_text = strip_ansi(report_text) # Note: rich markup isn't ANSI, but this keeps it safe if any slipped in
    
    txt_path = os.path.join(report_dir, f"{base_name}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        # Strip rich markup for plain text
        from rich.text import Text
        f.write(Text.from_ansi(clean_text).plain)
        
    md_path = os.path.join(report_dir, f"{base_name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        from rich.text import Text
        f.write(f"```text\n{Text.from_ansi(clean_text).plain}\n```")
        
    pdf_status = ""
    if FPDF:
        pdf_path = os.path.join(report_dir, f"{base_name}.pdf")
        try:
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Courier", size=8)
            from rich.text import Text
            for line in Text.from_ansi(clean_text).plain.split('\n'):
                pdf.multi_cell(0, 4, line)
            pdf.output(pdf_path)
            pdf_status = f"[green][SUCCESS][/green] PDF: {os.path.basename(pdf_path)}"
        except Exception as e:
            pdf_status = f"[red][ERROR][/red] PDF Generation Failed: {e}"
    else:
        pdf_status = "[yellow][WARNING][/yellow] PDF skipped (run: pip install fpdf2)"

    console.print("\n" + "="*90)
    console.print("[bold green][EXPORT] REPORTS SAVED SUCCESSFULLY![/bold green]")
    console.print("="*90)
    console.print(f" [white]Folder :[/white] {os.path.abspath(report_dir)}")
    console.print(f" [green]*[/green] Text     : {os.path.basename(txt_path)}")
    console.print(f" [green]*[/green] Markdown : {os.path.basename(md_path)}")
    console.print(f" {pdf_status}")
    console.print("="*90 + "\n")


def run_profiler(url: str, mobile: bool = False):
    profiler = AutoWebProfiler()
    try:
        profiler._init_rapl()
    except RaplUnavailable as e:
        console.print(f"[red][ERROR][/red] Hardware Error: {e}")
        sys.exit(1)

    console.print(f"\n[blue][NETWORK][/blue] Launching Headless Chromium to monitor: {url}")
    if mobile:
        console.print("[cyan][MOBILE][/cyan] Emulating iPhone 12 Pro viewport...")

    with console.status("[bold green]Injecting JS Event Listeners + Page Analyzer...", spinner="dots"):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            
            # NEW: Mobile Emulation Support
            if mobile:
                context = browser.new_context(
                    viewport={'width': 390, 'height': 844},
                    user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1'
                )
                page = context.new_page()
            else:
                page = browser.new_page()
                
            page.add_init_script(JS_INJECTION)

            def handle_console(msg):
                text = msg.text
                if text.startswith("8DOTS_EVENT:"):
                    event_name = text.replace("8DOTS_EVENT: ", "").strip()
                    console.print(f"  [yellow][EVENT][/yellow] {event_name}")
                    profiler._record_event(event_name)
                elif text.startswith("8DOTS_ANALYSIS:"):
                    json_str = text.replace("8DOTS_ANALYSIS: ", "").strip()
                    try:
                        analysis = json.loads(json_str)
                        if profiler.actions:
                            last_event = profiler.actions[-1]["event"]
                            profiler._store_analysis(last_event, analysis)
                            console.print(f"  [magenta][ANALYZE][/magenta] Page analyzed for: '{last_event}'")
                    except json.JSONDecodeError:
                        pass

            page.on("console", handle_console)

            console.print("[blue][LOAD][/blue] Loading initial page...")
            page.goto(url, wait_until="networkidle")
            
            # NEW: Capture Network Metrics
            profiler.network_metrics = page.evaluate("window.__8dots_get_network_metrics()")
            
            profiler._record_event("Initial Page Load")
            time.sleep(0.5)
            page.evaluate("window.__8dots_analyze_page()")
            time.sleep(0.5)

            console.print(f"\n[cyan][AUTO][/cyan] Auto-Explorer activated. Scanning for interactive elements...")
            interactive_elements = page.locator('a, button, [role="tab"], [role="button"]').all()
            
            clicked_count = 0
            max_clicks = 10
            
            for el in interactive_elements:
                if clicked_count >= max_clicks:
                    console.print(f"  [red][LIMIT][/red] Reached safety limit ({max_clicks} clicks). Stopping auto-explorer.")
                    break
                    
                try:
                    if el.is_visible(timeout=1000):
                        el_text = el.inner_text(timeout=1000).strip()
                        if not el_text:
                            el_text = el.get_attribute('aria-label') or el.get_attribute('title') or "Element"
                        
                        el_text = " ".join(el_text.split())[:30]
                        console.print(f"  [green][CLICK][/green] Discovered & clicking: '{el_text}'")
                        
                        el.click(timeout=3000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=3000)
                        except Exception:
                            pass
                        
                        time.sleep(1.0)
                        page.evaluate("window.__8dots_analyze_page()")
                        
                        # NEW: Auto-Screenshot on High Energy Spike (> 2.0 Joules)
                        if profiler.actions:
                            last_energy = profiler.actions[-1]['energy_j']
                            if last_energy > 2.0:
                                report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')
                                os.makedirs(report_dir, exist_ok=True)
                                safe_name = "".join(x for x in el_text if x.isalnum())
                                screenshot_path = os.path.join(report_dir, f"high_energy_spike_{clicked_count}_{safe_name}.png")
                                page.screenshot(path=screenshot_path)
                                console.print(f"  [bold red][ALERT][/bold red] High energy spike ({last_energy}J)! Screenshot saved: {screenshot_path}")
                        
                        time.sleep(0.5)
                        clicked_count += 1
                except Exception:
                    pass

            if clicked_count == 0:
                console.print(f"  [yellow][WARNING][/yellow] No clickable tabs or buttons found on this page.")

            console.print(f"\n[green][COMPLETE][/green] Test finished. Generating report...\n")
            browser.close()

    profiler._cleanup_rapl()
    
    output_buffer = io.StringIO()
    with contextlib.redirect_stdout(output_buffer):
        profiler._print_report(url)
    
    report_text = output_buffer.getvalue()
    console.print(report_text)
    save_reports(url, report_text)


def main():
    parser = argparse.ArgumentParser(description="8DoTs Automated Web Energy Profiler")
    parser.add_argument("url", nargs="?", help="The URL to profile")
    parser.add_argument("--mobile", action="store_true", help="Emulate a mobile device (iPhone)")
    
    args = parser.parse_args()
    
    console.print(Panel.fit("[bold cyan]8DoTs Automated Web Profiler + Energy Advisor[/bold cyan]", border_style="cyan"))
    console.print("Measures energy per tab AND tells you WHY + WHAT to fix.\n")
    
    url = args.url
    if not url:
        url = input("[white]Enter your localhost or live URL:[/white] ").strip()
    
    url = url.rstrip('\\').rstrip('/').strip()
    if not url.startswith("http"):
        url = "http://" + url
        
    run_profiler(url, mobile=args.mobile)


if __name__ == "__main__":
    main()