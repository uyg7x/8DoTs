#!/usr/bin/env python3
"""
REAL Calibration Script for Tier D.
Runs 12 distinct workloads under a single perf+RAPL execution,
and fits a multivariate linear regression to find the true coefficients.

Requirements on Linux:
    pip install numpy
"""
import os
import sys
import json
import time
import subprocess
import shutil
import numpy as np
from dol8 import capabilities

# 1. Define 12 distinct workloads with varying profiles
WORKLOADS = {
    "compute_fp": "x = 0.0\nfor i in range(5_000_000): x += i * 0.5",
    "compute_int": "x = 0\nfor i in range(5_000_000): x += i",
    "memory_scan": "data = bytes(10_000_000)\nfor i in range(100): data.count(b'\\x00')",
    "memory_alloc": "a = [i for i in range(2_000_000)]\ns = sum(a)",
    "matrix_mult": "import numpy as np\na=np.random.rand(200,200)\nb=np.random.rand(200,200)\nfor _ in range(10): np.matmul(a,b)",
    "json_parse": "import json\ns = json.dumps({'k':[1]*100000})\nfor _ in range(100): json.loads(s)",
    "string_concat": "s = ''\nfor i in range(100_000): s += 'x'",
    "syscall_bound": "import os\nfor _ in range(10_000): os.urandom(64)",
    "dict_ops": "d = {}\nfor i in range(1_000_000): d[i] = i",
    "sort_heavy": "import random\na = [random.random() for _ in range(500_000)]\na.sort()",
    "sleep_idle": "import time\ntime.sleep(0.5)",
    "mixed_workload": "a = [i**0.5 for i in range(500_000)]\nimport json\njson.loads(json.dumps(a))"
}

def get_cpu_model():
    """Read the actual CPU model name from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"

def write_workload(name, code):
    os.makedirs("calibration_tmp", exist_ok=True)
    path = os.path.join("calibration_tmp", f"{name}.py")
    with open(path, "w") as f:
        f.write(code)
    return path

def parse_perf_output(stderr):
    """Parse perf stat output to extract instructions and cache-misses."""
    ins, cm = 0, 0
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            val_str = parts[0].strip()
            event = parts[2].strip()
            if val_str.isdigit():
                val = int(val_str)
                if "instructions" in event: ins = val
                if "cache-misses" in event: cm = val
    return ins, cm

def measure_combined(script_path):
    """Run the script ONCE under perf, while measuring RAPL around it."""
    import dol8_core
    reader = dol8_core.RaplReader("/sys/class/powercap")
    
    # Take RAPL snapshot BEFORE starting
    start_snap = reader.read_all()
    start_time = time.perf_counter()
    
    # Run the script wrapped in perf stat
    cmd = ["perf", "stat", "-x", ",", "-e", "instructions,cache-misses",
           "--", sys.executable, script_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # Take RAPL snapshot AFTER it finishes
    elapsed = time.perf_counter() - start_time
    end_snap = reader.read_all()
    
    joules = reader.joules_between(start_snap, end_snap)
    ins, cm = parse_perf_output(result.stderr)
    
    return joules, elapsed, ins, cm

def main():
    tier = capabilities.get_current_tier()
    if tier.tier != "A":
        print("ERROR: Calibration must be run on bare-metal Linux (Tier A).")
        sys.exit(1)

    print("Starting REAL calibration process...")
    print(f"CPU Model: {get_cpu_model()}")
    
    X = []
    y = []

    for name, code in WORKLOADS.items():
        print(f"  -> Running {name}...")
        path = write_workload(name, code)
        
        try:
            joules, cpu_time, ins, cm = measure_combined(path)
            
            print(f"     J: {joules:.2f} | T: {cpu_time:.2f}s | Ins: {ins:,} | CM: {cm:,}")
            
            # Features: [instructions, cache_misses, cpu_time, intercept(1.0)]
            X.append([ins, cm, cpu_time, 1.0])
            y.append(joules)
        except Exception as e:
            print(f"     [WARN] Workload {name} failed: {e}")

    # Perform Multivariate Linear Regression
    X = np.array(X)
    y = np.array(y)

    if len(X) < len(X[0]):
        print("ERROR: Not enough successful samples to fit the model.")
        sys.exit(1)

    # Fit the model: y = X * coeffs
    coeffs, residuals, rank, s = np.linalg.lstsq(X, y, rcond=None)
    alpha, beta, gamma, delta = coeffs

    # Calculate R^2
    y_pred = X @ coeffs
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Save the REAL calibration data
    calibration_data = {
        "_comment": "Real calibration data generated from multivariate regression over 12 workloads.",
        "cpu_model": get_cpu_model(),
        "coefficients": {
            "joules_per_instruction": float(alpha),
            "joules_per_cache_miss": float(beta),
            "joules_per_cpu_second": float(gamma),
            "intercept_joules": float(delta)
        },
        "r_squared": float(r_squared),
        "sample_count": len(X),
        "measured_on": time.strftime("%Y-%m-%d")
    }

    with open("dol8/calibration.json", "w") as f:
        json.dump(calibration_data, f, indent=2)

    print("\n✅ Calibration Complete!")
    print(json.dumps(calibration_data, indent=2))
    print("\nSaved to dol8/calibration.json")

    # Cleanup temp files
    shutil.rmtree("calibration_tmp")

if __name__ == "__main__":
    main()