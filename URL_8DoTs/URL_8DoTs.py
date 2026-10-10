#!/usr/bin/env python3
"""
8DoTs Automated Web Energy Profiler with Intelligent Energy Advisor
Features: Color-coded terminal output, PDF/TXT export, and zero-overhead RAPL reading.
"""

import sys
import os
import time
import json
import io
import contextlib
import re
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
    FPDF = None  # Will handle gracefully if not installed


# ─── ANSI COLOR CODES FOR PROFESSIONAL TERMINAL OUTPUT ───
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'


def strip_ansi(text: str) -> str:
    """Removes ANSI color codes for clean TXT/PDF exports."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


# ─── THE ENHANCED JS INJECTION ───
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

window.__8dots_analyze_page = function() {
    const images = document.querySelectorAll('img');
    let largeImages = 0, uncompressedImages = 0;
    images.forEach(img => {
        if (img.naturalWidth * img.naturalHeight > 2000000) largeImages++;
        if (img.src && (img.src.endsWith('.png') || img.src.endsWith('.bmp'))) uncompressedImages++;
    });
    let animationCount = 0;
    const allElements = document.querySelectorAll('*');
    allElements.forEach(el => {
        const style = window.getComputedStyle(el);
        if (style.animationName && style.animationName !== 'none') animationCount++;
        if (style.transition && style.transition !== 'all 0s ease 0s') animationCount++;
    });
    const scripts = document.querySelectorAll('script[src]');
    let thirdPartyScripts = 0;
    scripts.forEach(s => { if (!s.src.includes(window.location.hostname)) thirdPartyScripts++; });
    
    const analysis = {
        images_total: images.length, large_images: largeImages, uncompressed_images: uncompressedImages,
        css_animations: animationCount, dom_elements: allElements.length, third_party_scripts: thirdPartyScripts,
        iframes: document.querySelectorAll('iframe').length,
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
        print(f"{Colors.GREEN}[SUCCESS]{Colors.RESET} RAPL initialized with zero-overhead cached handles.")

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
            recs.append(f"{Colors.BLUE}*{Colors.RESET} {analysis['large_images']} large image(s) found. Use responsive images (srcset) or lazy loading.")
        if analysis.get("uncompressed_images", 0) > 0:
            recs.append(f"{Colors.YELLOW}*{Colors.RESET} {analysis['uncompressed_images']} PNG/BMP image(s). Convert to WebP/AVIF for 30-50% smaller size.")
        if analysis.get("css_animations", 0) > 3:
            recs.append(f"{Colors.MAGENTA}*{Colors.RESET} {analysis['css_animations']} CSS animations/transitions running. Reduce or use 'prefers-reduced-motion'.")
        if analysis.get("dom_elements", 0) > 1500:
            recs.append(f"{Colors.GREEN}*{Colors.RESET} {analysis['dom_elements']} DOM elements. Simplify HTML structure to reduce rendering cost.")
        if analysis.get("third_party_scripts", 0) > 2:
            recs.append(f"{Colors.CYAN}*{Colors.RESET} {analysis['third_party_scripts']} third-party scripts. Defer or async load non-critical ones.")
        if analysis.get("iframes", 0) > 0:
            recs.append(f"{Colors.BLUE}*{Colors.RESET} {analysis['iframes']} iframe(s) (embeds). Lazy-load iframes that are below the fold.")
        if analysis.get("autoplay_videos", 0) > 0:
            recs.append(f"{Colors.RED}*{Colors.RESET} {analysis['autoplay_videos']} autoplay video(s). Remove autoplay or use a poster image instead.")
        if analysis.get("canvas_webgl", 0) > 0:
            recs.append(f"{Colors.MAGENTA}*{Colors.RESET} {analysis['canvas_webgl']} canvas/WebGL element(s). These are GPU-intensive. Optimize render loops.")
        if not recs:
            recs.append(f"{Colors.GREEN}*{Colors.RESET} This page looks well-optimized! No major energy concerns detected.")
        return recs

    def _print_report(self, url: str):
        if not self.actions:
            print(f"\n{Colors.YELLOW}[WARNING]{Colors.RESET} No events were captured.")
            return

        total_time = round(time.perf_counter() - self.session_start_time, 2)
        total_energy_j = sum(a['energy_j'] for a in self.actions)
        total_kwh = total_energy_j / 3_600_000.0
        estimated_cost = total_kwh * 0.15
        estimated_co2_grams = total_kwh * 450.0

        max_energy_action = max(self.actions, key=lambda x: x["energy_j"])
        max_time_action = max(self.actions, key=lambda x: x["duration_s"])

        print("\n" + f"{Colors.CYAN}{Colors.BOLD}{'='*95}{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}  8DoTs AUTOMATED WEB ENERGY REPORT + INTELLIGENT ADVISOR  {Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}{'='*95}{Colors.RESET}")
        print(f" {Colors.WHITE}Target URL       :{Colors.RESET} {url}")
        print(f" {Colors.WHITE}Total Session    :{Colors.RESET} {total_time} seconds")
        print(f" {Colors.WHITE}Total Energy     :{Colors.RESET} {total_energy_j:.4f} Joules ({total_kwh:.8f} kWh)")
        print(f" {Colors.GREEN}[COST]{Colors.RESET} Est. Cloud Cost: ${estimated_cost:.8f} (at $0.15/kWh)")
        print(f" {Colors.GREEN}[CO2]{Colors.RESET}  Est. CO2 Emit  : {estimated_co2_grams:.4f} grams (at 450g/kWh)")
        print(f" {Colors.WHITE}Tabs/Events      :{Colors.RESET} {len(self.actions)}")

        print(f"\n{Colors.BLUE}{Colors.BOLD}[BREAKDOWN] ENERGY PER TAB/ACTION:{Colors.RESET}")
        print("-" * 95)
        print(f" {'#':<3} | {'Tab/Action':<30} | {'Time (s)':<8} | {'Energy (J)':<10} | {'Avg Watts':<9}")
        print("-" * 95)
        for i, a in enumerate(self.actions, 1):
            marker = f" {Colors.RED}[HIGH]{Colors.RESET}" if a == max_energy_action else ""
            print(f" {i:<3} | {a['event']:<30} | {a['duration_s']:<8} | {a['energy_j']:<10} | {a['avg_watts']:<9}{marker}")
        print("-" * 95)

        print(f"\n{Colors.CYAN}{Colors.BOLD}[INSIGHT] WHY DOES EACH TAB USE ENERGY? + WHAT TO IMPROVE:{Colors.RESET}")
        print("=" * 95)

        for tab_name, analysis in self.page_analyses.items():
            tab_action = next((a for a in self.actions if tab_name in a["event"]), None)
            energy_str = f"{tab_action['energy_j']} J" if tab_action else "N/A"

            print(f"\n{Colors.YELLOW}[TAB]{Colors.RESET} '{tab_name}' {Colors.WHITE}(Energy:{Colors.RESET} {energy_str}{Colors.WHITE}){Colors.RESET}")
            print(f"   {Colors.WHITE}Page Metrics:{Colors.RESET}")
            print(f"     - Images: {analysis.get('images_total', 0)} total, {analysis.get('large_images', 0)} large, {analysis.get('uncompressed_images', 0)} uncompressed")
            print(f"     - CSS Animations: {analysis.get('css_animations', 0)}")
            print(f"     - DOM Elements: {analysis.get('dom_elements', 0)}")
            print(f"     - Third-party Scripts: {analysis.get('third_party_scripts', 0)}")
            
            recs = self._generate_recommendations(analysis)
            print(f"   {Colors.GREEN}Recommendations:{Colors.RESET}")
            for rec in recs:
                print(f"     {rec}")

        print("\n" + f"{Colors.YELLOW}{Colors.BOLD}{'='*95}{Colors.RESET}")
        print(f"{Colors.YELLOW}{Colors.BOLD}  [SUMMARY] OVERALL INSIGHTS:{Colors.RESET}")
        print(f"   {Colors.RED}*{Colors.RESET} Most energy-intensive : '{max_energy_action['event']}' ({max_energy_action['energy_j']} J)")
        print(f"   {Colors.CYAN}*{Colors.RESET} Slowest loading       : '{max_time_action['event']}' ({max_time_action['duration_s']} s)")

        if self.page_analyses:
            worst_tab = max(self.page_analyses.keys(), key=lambda t: len(self._generate_recommendations(self.page_analyses[t])))
            worst_recs = self._generate_recommendations(self.page_analyses[worst_tab])
            if len(worst_recs) > 1 or "well-optimized" not in worst_recs[0].lower():
                print(f"   {Colors.YELLOW}*{Colors.RESET} Needs most work       : '{worst_tab}' ({len(worst_recs)} issues found)")
        print(f"{Colors.YELLOW}{Colors.BOLD}{'='*95}{Colors.RESET}\n")


def save_reports(url: str, report_text: str):
    """Saves the terminal output to TXT, Markdown, and PDF in a 'reports' folder."""
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'reports')
    os.makedirs(report_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_url = url.replace("http://", "").replace("https://", "").replace("/", "_").replace(":", "").replace("#", "")[:30]
    base_name = f"8DoTs_Report_{safe_url}_{timestamp}"
    
    # Strip ANSI colors for file exports
    clean_text = strip_ansi(report_text)
    
    # 1. Save TXT
    txt_path = os.path.join(report_dir, f"{base_name}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(clean_text)
        
    # 2. Save Markdown
    md_path = os.path.join(report_dir, f"{base_name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"```text\n{clean_text}\n```")
        
    # 3. Save PDF
    pdf_status = ""
    if FPDF:
        pdf_path = os.path.join(report_dir, f"{base_name}.pdf")
        try:
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Courier", size=8)  # Monospace keeps tables aligned perfectly!
            for line in clean_text.split('\n'):
                pdf.multi_cell(0, 4, line)
            pdf.output(pdf_path)
            pdf_status = f"{Colors.GREEN}[SUCCESS]{Colors.RESET} PDF: {os.path.basename(pdf_path)}"
        except Exception as e:
            pdf_status = f"{Colors.RED}[ERROR]{Colors.RESET} PDF Generation Failed: {e}"
    else:
        pdf_status = f"{Colors.YELLOW}[WARNING]{Colors.RESET} PDF skipped (run: pip install fpdf2)"

    print("\n" + f"{Colors.GREEN}{Colors.BOLD}{'='*95}{Colors.RESET}")
    print(f"{Colors.GREEN}{Colors.BOLD}  [EXPORT] REPORTS SAVED SUCCESSFULLY!{Colors.RESET}")
    print(f"{Colors.GREEN}{Colors.BOLD}{'='*95}{Colors.RESET}")
    print(f" {Colors.WHITE}Folder :{Colors.RESET} {os.path.abspath(report_dir)}")
    print(f" {Colors.GREEN}*{Colors.RESET} Text     : {os.path.basename(txt_path)}")
    print(f" {Colors.GREEN}*{Colors.RESET} Markdown : {os.path.basename(md_path)}")
    print(f" {pdf_status}")
    print(f"{Colors.GREEN}{Colors.BOLD}{'='*95}{Colors.RESET}\n")


def run_profiler(url: str):
    profiler = AutoWebProfiler()
    try:
        profiler._init_rapl()
    except RaplUnavailable as e:
        print(f"{Colors.RED}[ERROR]{Colors.RESET} Hardware Error: {e}")
        sys.exit(1)

    print(f"\n{Colors.BLUE}[NETWORK]{Colors.RESET} Launching Headless Chromium to monitor: {url}")
    print(f"{Colors.CYAN}[INJECT]{Colors.RESET} Injecting JS Event Listeners + Page Analyzer...\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script(JS_INJECTION)

        def handle_console(msg):
            text = msg.text
            if text.startswith("8DOTS_EVENT:"):
                event_name = text.replace("8DOTS_EVENT: ", "").strip()
                print(f"  {Colors.YELLOW}[EVENT]{Colors.RESET} {event_name}")
                profiler._record_event(event_name)
            elif text.startswith("8DOTS_ANALYSIS:"):
                json_str = text.replace("8DOTS_ANALYSIS: ", "").strip()
                try:
                    analysis = json.loads(json_str)
                    if profiler.actions:
                        last_event = profiler.actions[-1]["event"]
                        profiler._store_analysis(last_event, analysis)
                        print(f"  {Colors.MAGENTA}[ANALYZE]{Colors.RESET} Page analyzed for: '{last_event}'")
                except json.JSONDecodeError:
                    pass

        page.on("console", handle_console)

        print(f"{Colors.BLUE}[LOAD]{Colors.RESET} Loading initial page...")
        page.goto(url, wait_until="networkidle")
        profiler._record_event("Initial Page Load")
        time.sleep(0.5)
        page.evaluate("window.__8dots_analyze_page()")
        time.sleep(0.5)

        print(f"\n{Colors.CYAN}[AUTO]{Colors.RESET} Auto-Explorer activated. Scanning for interactive elements...")
        interactive_elements = page.locator('a, button, [role="tab"], [role="button"]').all()
        
        clicked_count = 0
        max_clicks = 10
        
        for el in interactive_elements:
            if clicked_count >= max_clicks:
                print(f"  {Colors.RED}[LIMIT]{Colors.RESET} Reached safety limit ({max_clicks} clicks). Stopping auto-explorer.")
                break
                
            try:
                if el.is_visible(timeout=1000):
                    el_text = el.inner_text(timeout=1000).strip()
                    if not el_text:
                        el_text = el.get_attribute('aria-label') or el.get_attribute('title') or "Element"
                    
                    el_text = " ".join(el_text.split())[:30]
                    print(f"  {Colors.GREEN}[CLICK]{Colors.RESET} Discovered & clicking: '{el_text}'")
                    
                    el.click(timeout=3000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass
                    
                    time.sleep(1.0)
                    page.evaluate("window.__8dots_analyze_page()")
                    time.sleep(0.5)
                    clicked_count += 1
            except Exception:
                pass

        if clicked_count == 0:
            print(f"  {Colors.YELLOW}[WARNING]{Colors.RESET} No clickable tabs or buttons found on this page.")

        print(f"\n{Colors.GREEN}[COMPLETE]{Colors.RESET} Test finished. Generating report...\n")
        browser.close()

    profiler._cleanup_rapl()
    
    # Capture terminal output and save to files
    output_buffer = io.StringIO()
    with contextlib.redirect_stdout(output_buffer):
        profiler._print_report(url)
    
    report_text = output_buffer.getvalue()
    print(report_text)  # Print colored version to terminal
    save_reports(url, report_text)  # Save clean version to files


def main():
    print(f"{Colors.CYAN}{Colors.BOLD}==================================================={Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  8DoTs Automated Web Profiler + Energy Advisor  {Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}==================================================={Colors.RESET}")
    print("Measures energy per tab AND tells you WHY + WHAT to fix.\n")
    
    url = input(f"{Colors.WHITE}Enter your localhost or live URL:{Colors.RESET} ").strip()
    
    # Typo-proofing
    url = url.rstrip('\\').rstrip('/').strip()
    if not url.startswith("http"):
        url = "http://" + url
        
    run_profiler(url)


if __name__ == "__main__":
    main()
