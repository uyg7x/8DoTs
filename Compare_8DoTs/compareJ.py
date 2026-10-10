#!/usr/bin/env python3
"""
8DoTs Folder Comparison Engine
Compares the energy consumption of two project folders (e.g., old vs new version).
"""
import sys
import os
import time
import argparse
from pathlib import Path
from typing import Dict, Any, List

# Add parent directory to import the core dol8 module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dol8.rapl import RAPLReader, RaplUnavailable
from dol8.runner import TargetRunner, PowerProfiler
from dol8.thermal import ThermalMonitor

# ─── ANSI COLOR CODES ───
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'

def get_cpu_temp():
    """Safely read CPU temperature directly from Linux sysfs."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_mC = int(f.read().strip())
            return temp_mC / 1000.0  # Convert from millidegrees to degrees Celsius
    except Exception:
        return None

def measure_folder(folder_path: Path, entry_file: str, reader: RAPLReader, runs: int) -> Dict[str, float]:
    """Measures the energy of a specific folder's entry file over multiple runs."""
    script_path = folder_path / entry_file
    original_dir = os.getcwd()

    total_energy = 0.0
    total_time = 0.0

    print(f"  {Colors.BLUE}[TESTING]{Colors.RESET} {folder_path.name} ({runs} runs)...")

    for i in range(runs):
        os.chdir(folder_path)
        try:
            thermal = ThermalMonitor()
            profiler = PowerProfiler(reader=reader, thermal=thermal)

            with profiler:
                runner = TargetRunner()
                runner.run([sys.executable, str(script_path)], timeout_s=600.0)

            total_energy += profiler.energy_joules
            total_time += profiler.duration_s

        finally:
            os.chdir(original_dir)

    avg_energy = total_energy / runs
    avg_time = total_time / runs

    print(f"    {Colors.GREEN}[DONE]{Colors.RESET} Avg Energy: {Colors.YELLOW}{avg_energy:.3f} J{Colors.RESET} | Avg Time: {Colors.CYAN}{avg_time:.3f} s{Colors.RESET}")
    return {"energy_j": avg_energy, "time_s": avg_time}

def main():
    parser = argparse.ArgumentParser(description="Compare energy of two project folders.")
    parser.add_argument("old_dir", help="Path to the old project folder")
    parser.add_argument("new_dir", help="Path to the new project folder")
    parser.add_argument("--entry", default="main.py", help="Main file to run (default: main.py)")
    parser.add_argument("--runs", type=int, default=3, help="Number of test runs (default: 3)")
    args = parser.parse_args()

    old_path = Path(args.old_dir).resolve()
    new_path = Path(args.new_dir).resolve()

    if not (old_path / args.entry).exists():
        print(f"{Colors.RED}[ERROR]{Colors.RESET} Cannot find {args.entry} in {old_path}")
        sys.exit(1)
    if not (new_path / args.entry).exists():
        print(f"{Colors.RED}[ERROR]{Colors.RESET} Cannot find {args.entry} in {new_path}")
        sys.exit(1)

    reader = RAPLReader()
    try:
        if not reader.available():
            raise RaplUnavailable(reader.unavailable_reason)
    except RaplUnavailable as e:
        print(f"{Colors.RED}[ERROR]{Colors.RESET} {e}")
        sys.exit(1)

    # ─── PRE-FLIGHT THERMAL CHECK ───
    current_temp = get_cpu_temp()

    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  8DoTs PRE-FLIGHT CHECK{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")

    if current_temp is not None and current_temp > 60.0:
        print(f"  {Colors.RED}[WARNING]{Colors.RESET} CPU temperature is high: {Colors.RED}{current_temp:.1f} C{Colors.RESET}")
        print(f"  {Colors.YELLOW}[ACTION]{Colors.RESET} For accurate results, please:")
        print(f"    {Colors.DIM}1. Close heavy background apps (Chrome, VS Code, Games).{Colors.RESET}")
        print(f"    {Colors.DIM}2. Ensure laptop is plugged into power.{Colors.RESET}")
        print(f"    {Colors.DIM}3. Wait 2-3 minutes for CPU to cool below 55 C.{Colors.RESET}")
        proceed = input(f"\n  {Colors.WHITE}Proceed anyway? (y/n): {Colors.RESET}").strip().lower()
        if proceed != 'y':
            print(f"  {Colors.GREEN}[ABORTED]{Colors.RESET} Test cancelled. Cool down and retry.")
            sys.exit(0)
    else:
        temp_str = f"{current_temp:.1f} C" if current_temp is not None else "N/A"
        print(f"  {Colors.GREEN}[PASS]{Colors.RESET} CPU temperature is optimal: {Colors.GREEN}{temp_str}{Colors.RESET}")

    print(f"{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}\n")

    # ─── HEADER ───
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  8DoTs FOLDER COMPARISON ENGINE{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f" {Colors.WHITE}Old Folder :{Colors.RESET} {old_path}")
    print(f" {Colors.WHITE}New Folder :{Colors.RESET} {new_path}")
    print(f" {Colors.WHITE}Entry File :{Colors.RESET} {args.entry}")
    print(f" {Colors.WHITE}Test Runs  :{Colors.RESET} {args.runs}\n")

    # ─── MEASURE OLD FOLDER ───
    old_stats = measure_folder(old_path, args.entry, reader, args.runs)
    
    # ─── CPU COOLDOWN PERIOD ───
    # Prevents thermal throttling from ruining the second test
    print(f"\n  {Colors.CYAN}[COOLDOWN]{Colors.RESET} Pausing for 5 seconds to let CPU temperature normalize...")
    time.sleep(5) 
    
    # ─── MEASURE NEW FOLDER ───
    new_stats = measure_folder(new_path, args.entry, reader, args.runs)

    # ─── CALCULATE DELTA ───
    energy_delta = new_stats["energy_j"] - old_stats["energy_j"]
    energy_pct = (energy_delta / old_stats["energy_j"]) * 100 if old_stats["energy_j"] > 0 else 0

    time_delta = new_stats["time_s"] - old_stats["time_s"]
    time_pct = (time_delta / old_stats["time_s"]) * 100 if old_stats["time_s"] > 0 else 0

    # ─── PRINT FINAL REPORT ───
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}  FINAL VERDICT{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"\n {Colors.WHITE}{'Metric':<20} | {'Old Folder':<15} | {'New Folder':<15} | {'Delta':<15}{Colors.RESET}")
    print(f" {'-'*68}")

    # Energy Row
    e_color = Colors.GREEN if energy_delta < 0 else Colors.RED
    print(f" {Colors.WHITE}Energy (Joules){Colors.RESET}   | {old_stats['energy_j']:<15.3f} | {new_stats['energy_j']:<15.3f} | {e_color}{energy_delta:+.3f} J ({energy_pct:+.1f}%){Colors.RESET}")

    # Time Row
    t_color = Colors.GREEN if time_delta < 0 else Colors.RED
    print(f" {Colors.WHITE}Time (Seconds){Colors.RESET}    | {old_stats['time_s']:<15.3f} | {new_stats['time_s']:<15.3f} | {t_color}{time_delta:+.3f} s ({time_pct:+.1f}%){Colors.RESET}")
    print(f" {'-'*68}")

    # Final Conclusion
    if energy_delta < 0:
        print(f"\n {Colors.GREEN}{Colors.BOLD}[SUCCESS] The NEW folder is MORE energy-efficient!{Colors.RESET}")
        print(f" {Colors.GREEN}You saved {abs(energy_pct):.1f}% in computing energy.{Colors.RESET}")
    elif energy_delta > 0:
        print(f"\n {Colors.RED}{Colors.BOLD}[WARNING] The NEW folder uses MORE energy!{Colors.RESET}")
        print(f" {Colors.RED}Energy increased by {energy_pct:.1f}%. Consider optimizing the code.{Colors.RESET}")
    else:
        print(f"\n {Colors.YELLOW}[NEUTRAL] Both folders use the exact same amount of energy.{Colors.RESET}")

    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*70}{Colors.RESET}\n")

if __name__ == "__main__":
    main()
