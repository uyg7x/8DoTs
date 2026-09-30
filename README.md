<div align="center">
  <pre style="font-family: monospace; font-size: 1.3em; line-height: 1.2; margin: 0;">
    <span style="color: orange;">
██▀▀▀▄  ▄▀▀▀▀▄  █
██   █  █    █  █
    ██▄▄▄▀  ▀▄▄▄▄▀  █▄▄▄▀
    </span>
    <span style="color: #32CD32;">
     ▄▀▀▀▀▄
    █    █
    ▀▄▄▄▄▀
    █    █
     ▀▄▄▄▄▀
    </span>
  </pre>
</div>
# 8DoTs

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux%20bare--metal-lightgrey)](https://www.kernel.org/)

8DoTs (internally structured as the `dol8` Python package) is a command-line profiling tool designed to make energy consumption visible during software development. While execution time has always been a standard metric, energy use has remained largely invisible. 8DoTs reads hardware energy counters (RAPL - Running Average Power Limit) and thermal sensors directly from the Linux kernel to provide repeatable, high-resolution estimates of how much energy a specific workload consumes, how much heat it generates, and whether it triggers thermal throttling.

## Requirements

Before installing, ensure your environment meets these strict hardware and software requirements:
- **Operating System**: Bare-metal Linux (e.g., Ubuntu, Linux Mint). 
- **Hardware**: Intel or AMD CPU that exposes RAPL counters through the Linux `powercap` interface.
- **Software**: Python 3.9 or higher.
- **Unsupported Environments**: Virtual machines (VMs) and Windows Subsystem for Linux (WSL2) are **not supported** for energy profiling, as hypervisors hide the physical hardware counters. (You can still run the test suite on these platforms, but profiling commands will report RAPL as unavailable).

## Table of Contents

1. [The Problems It Solves](#the-problems-it-solves)
2. [Key Features](#key-features)
3. [Quick Start](#quick-start)
4. [Example Output](#example-output)
5. [How It Works](#how-it-works)
6. [Using the CLI in Workflows](#using-the-cli-in-workflows)
7. [Future Vision](#future-vision)
8. [Limitations, Honesty, and Solutions](#limitations-honesty-and-solutions)
9. [How to Contribute](#how-to-contribute)

## The Problems It Solves

1. **Invisible Energy Costs**: Two programs with identical execution times can differ drastically in energy consumption. 8DoTs exposes this hidden metric, proving that speed is not the same as efficiency.
2. **Silent Thermal Throttling**: Overheated processors silently reduce their frequency to manage heat, causing mysterious performance degradation without throwing errors. 8DoTs detects and reports these events.
3. **Unreliable Single-Run Measurements**: System noise (background tasks, CPU boost states, cache variance, and counter quantization) makes single-run energy measurements misleading. 8DoTs employs statistical methods to quantify measurement confidence.
4. **Runaway Processes**: Profiling tools often leave orphaned child processes running after a timeout, skewing results and wasting resources. 8DoTs enforces strict process-tree cleanup.

## Key Features

- **Direct RAPL Reads**: Dynamically discovers Linux powercap domains and accurately calculates energy deltas, including automatic mathematical correction for counter wraps.
- **Thermal Context**: Samples CPU thermal zones to report baseline, peak, average, and ending temperatures, alongside heuristic throttling detection.
- **Statistical Confidence**: Reports mean, standard deviation, and Coefficient of Variation (CoV) to label results as HIGH, MEDIUM, or LOW confidence.
- **Sequential Execution**: Runs targets one at a time with robust timeout handling, process-group isolation, and survivor reporting to ensure clean attribution.
- **Export Formats**: Outputs structured JSON and time-series CSV data for downstream analysis, archiving, and visualization.
- **CI Energy Gates**: Compares pull request measurements against a saved baseline with a noise guard, preventing energy regressions from being merged.

## Quick Start

1. **Install system prerequisites**:
   ```bash
   sudo apt update
   sudo apt install -y git python3 python3-venv python3-pip
   ```

2. **Clone the repository**:
   ```bash
   git clone https://github.com/uyg7x/8DoTs.git
   cd 8DoTs
   ```

3. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   ```

4. **Install the tool**:
   ```bash
   python -m pip install -e .
   ```

5. **Fix RAPL permissions (if required)**:
   If your system restricts access to powercap counters, apply this udev rule to make them readable. Review your local security policy before making hardware counters world-readable.
   ```bash
   sudo bash -c 'echo SUBSYSTEM=="powercap", MODE="0666" > /etc/udev/rules.d/99-rapl.rules'
   sudo udevadm control --reload
   sudo udevadm trigger
   ```

6. **Verify your system readiness**:
   ```bash
   python -m dol8.cli doctor
   ```
   *(Note: You can also use the `dol8` command directly if the console script entry point was successfully added to your PATH during installation. If `dol8` is not found, prepend `python -m dol8.cli` to all commands).*

## Example Output

When you run a profiled workload, 8DoTs provides a comprehensive, human-readable summary:

```text
===== DoL8 REPORT (HIGH-RESOLUTION ESTIMATE) =====
Target: workload.py
CPU/OS: AMD Ryzen 5 3500U with Radeon Vega Mobile Gfx / Linux
TIME: 2.099s (+- 0.044s)
ENERGY: 12.292 J (0.00000341 kWh); baseline removed 20.789 J
  PACKAGE: 12.292 J [####################] 100.0%
POWER: avg 5.857 W, peak 18.960 W
THERMAL: baseline 60.3 C, peak 60.6 C, avg 60.2 C, end 60.5 C, rise 0.3 C
THROTTLING: no
CONFIDENCE: 3 runs, mean 12.292 J +- 0.167 J, CoV 1.36%, HIGH
ECONOMICS: $0.00000041 per run, $0.0150 per year (at $0.15/kWh)
CO2: 0.002 g per SCI-aligned estimation (at 0.45 kg/kWh)
```

## How It Works

8DoTs operates on a rigorous measurement contract:
1. **Baseline**: Measures idle system energy for a short duration (default 5 seconds).
2. **Warm-up**: Executes the target once and discards the measurement to stabilize CPU frequency and cache states.
3. **Measurement**: Runs the target sequentially for a specified number of runs, capturing start and end RAPL counter values.
4. **Calculation**: Computes the energy delta in joules, adjusts for idle consumption, and calculates average and peak power.
5. **Reporting**: Outputs a comprehensive summary including thermal data, statistical confidence, and optional cost or CO2 estimates (configured via `--price` for cost per kWh and `--grid-factor` for CO2 emissions per kWh).

## Using the CLI in Workflows

8DoTs is designed to integrate seamlessly into development and CI/CD workflows. 

1. **Preventing Runaway Process and Timeout Issues**:  
   Use the built-in timeout feature to prevent hung workloads from consuming indefinite energy.  
   ```bash
   python -m dol8.cli run workload.py --timeout 120 --runs 3
   ```  
   The tool isolates the target in a new POSIX process group. If the timeout is reached, it gracefully terminates the entire process tree, preventing orphaned processes from skewing subsequent measurements.

2. **Eliminating Measurement Noise**:  
   Never rely on a single run for performance claims. Use the `--runs` and `--warmup` flags to ensure statistical validity.  
   ```bash
   python -m dol8.cli run workload.py --runs 5 --warmup 1 --json results.json
   ```  

3. **Automating Energy Regression Prevention (CI Gates)**:  
   To prevent code changes from silently increasing energy consumption, save a baseline and enforce it in your CI pipeline.  
   ```bash
   # Save a known-good baseline
   python -m dol8.cli gate save result.json --out baseline.json
   
   # Enforce in CI (fails only if increase is >15% AND statistically significant)
   python -m dol8.cli run new_workload.py --runs 5 --gate-baseline baseline.json --threshold 15
   ```  

4. **Comparing Two Implementations**:  
   Use the compare command to run alternating A/B tests with fresh baselines for each observation.
   ```bash
   python -m dol8.cli compare before.py after.py --runs 5 --json compare.json
   ```

## Future Vision

As software systems grow, so does their computational carbon footprint. 8DoTs is building toward a future where energy efficiency is a first-class citizen in software quality assurance. Upcoming and evolving capabilities include:

- **Enhanced Resource Tracking**: Correlating energy spikes with specific file I/O or memory allocation patterns to help developers optimize storage and memory efficiency, reducing unnecessary file writes and memory bloat.
- **Broader Hardware Support**: Expanding beyond CPU RAPL to include detailed GPU and specialized accelerator energy profiling for machine learning and high-performance computing workloads.
- **Refined Watch Mode**: Improving the experimental `python -m dol8.cli watch` command, which currently provides an ESTIMATED, CPU-time-prorated energy share for running processes, to offer more granular real-time machine observation.

## Limitations, Honesty, and Solutions

We believe in transparent engineering. Below are the current limitations of the tool and the practical solutions to work around them.

1. **Limitation: Platform Restriction**  
   *Issue*: Profiling requires bare-metal Linux. Windows and macOS do not expose the required RAPL interfaces.  
   *Solution*: For cross-platform development teams, designate a dedicated bare-metal Linux machine or a Linux CI runner specifically for energy profiling. The 8DoTs codebase can still be developed and tested on any OS using the mocked test suite.

2. **Limitation: Estimation, Not Exact Process Attribution**  
   *Issue*: RAPL measures energy at the CPU socket or domain level, not per individual process. It cannot perfectly isolate a single process in a multi-tenant environment.  
   *Solution*: To maximize attribution accuracy, run 8DoTs on a dedicated, idle machine. Use the `--runs` flag to average out background noise. Disable unnecessary background services, network activity, and desktop environments during the measurement window.

3. **Limitation: Hardware and Environmental Variance**  
   *Issue*: Results are influenced by the specific CPU model, cooling solution, ambient temperature, and background system state.  
   *Solution*: Always perform A/B comparisons on the exact same physical machine under consistent environmental conditions. Rely on the built-in statistical confidence metrics (CoV). If the confidence is labeled "LOW," increase the number of runs or investigate background system interference.

## How to Contribute

We welcome contributions from the community to make 8DoTs more robust, accurate, and useful. 

### Getting Started
1. **Fork the Repository**: Create your own fork of the 8DoTs repository on GitHub.
2. **Clone and Setup**: Clone your fork locally and set up the development environment with test dependencies:
   ```bash
   git clone https://github.com/YOUR-USERNAME/8DoTs.git
   cd 8DoTs
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e ".[test]"
   ```
3. **Run the Test Suite**: Ensure all existing tests pass before making changes:
   ```bash
   python -m pytest
   ```

### Contribution Guidelines
- **Code Style**: Follow the existing Python formatting and typing conventions used in the codebase.
- **Testing**: Every new feature or bug fix must include corresponding tests. The project relies heavily on mocked `sysfs` and `psutil` interfaces in the `tests/` directory to ensure tests can run reliably without requiring specific physical hardware.
- **Documentation**: Update the relevant documentation files in the `docs/` directory and this README if your change alters user-facing behavior or CLI arguments.
- **Pull Requests**: Submit a Pull Request with a clear, descriptive title. Explain the problem you are solving, the approach you took, and how to verify the changes.

### Areas We Need Help With
- Expanding support for additional thermal sensor families or battery APIs.
- Improving the granularity of the CSV timeline exports.
- Enhancing the `watch` mode for better real-time process estimation.
- Writing tutorials or case studies demonstrating energy optimization in real-world applications.

## License

MIT License.
