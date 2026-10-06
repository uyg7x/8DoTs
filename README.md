[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux_Bare_Metal-green.svg)](https://www.linux.org/)

![Backend Profiling](https://img.shields.io/badge/Backend-Profiling-2783DE)
![Folder A/B Testing](https://img.shields.io/badge/Folder-A%2FB_Testing-D5803B)
![Web Profiling](https://img.shields.io/badge/Web-Profiling-46A171)

# DoL8
   ##समय मापने से रूप दिखता है, ऊर्जा मापने से सत्य ज्ञात होता है।
   ##测时见形，测能知真
   ##To measure time is to see the form. To measure energy is to know the truth

```text
██████╗  ██████╗ ██╗
██╔══██╗██╔══██╗██║
██║  ██║██║  ██║██║
██║  ██║██║  ██║██║
██████╔╝╚██████╔╝██████╗
╚═════╝ ╚═════╝ ╚═════╝
          ██████╗
         ██╔═══██╗
         ██║   ██║
         ╚██████╔╝
         ██╔═══██╗
         ╚██████╔╝


> [!TIP]
> Measure energy in joules, detect thermal impact, compare implementations, and uncover frontend bloat.

---

## Video Tutorial

<!-- Replace YOUTUBE_VIDEO_ID below with your actual YouTube video ID, then uncomment the next line. -->
<!-- [![Watch the DoL8 Usage Tutorial](https://img.youtube.com/vi/YOUTUBE_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=YOUTUBE_VIDEO_ID) -->

> A complete video walkthrough showing how to install and use the DoL8 modules will appear here.

---

## Table of Contents

1. [What is DoL8 and Why We Built This](#what-is-dol8-and-why-we-built-this)
2. [The Business Case: How Organizations Save Money](#the-business-case-how-organizations-save-money)
3. [System Requirements](#system-requirements)
4. [The Problems DoL8 Solves](#the-problems-dol8-solves)
5. [Installation and Setup](#installation-and-setup)
6. [Pillar 1: Core File Profiler (Backend)](#pillar-1-core-file-profiler-backend)
7. [Pillar 2: Folder Comparison Engine (A/B Testing)](#pillar-2-folder-comparison-engine-ab-testing)
8. [Pillar 3: Web Energy Profiler (Frontend/JS)](#pillar-3-web-energy-profiler-frontendjs)
9. [Visualizing the Output](#visualizing-the-output)
10. [Future Vision](#future-vision)
11. [Limitations and Solutions](#limitations-and-solutions)
12. [License](#license)

---

## What is DoL8 and Why We Built This

DoL8 is a comprehensive, hardware-level energy profiling suite designed to measure, compare, and optimize the energy consumption of software.

For decades, the software industry has been obsessed with one metric: speed. We constantly ask, “How fast does this code run?” But speed is not the same as efficiency. Imagine two cars driving to the same destination in exactly 10 minutes. Car A guzzles fuel, overheats its engine, and wears down its parts. Car B arrives just as fast, but uses half the fuel and runs perfectly cool.

In software, we have historically only measured the “10 minutes” (execution time), completely ignoring the hidden fuel and engine wear. Two programs can finish a task in the exact same timeframe, yet one might force the CPU to work significantly harder. This hidden inefficiency generates excess heat, triggers silent performance throttling, and unnecessarily drains cloud computing budgets.

**The Solution:** DoL8 acts as a high-precision fuel gauge and engine monitor for your software. It makes the invisible visible. By reading Intel and AMD RAPL counters and thermal sensors directly from the bare-metal Linux kernel, DoL8 estimates how much energy your code consumes (at the socket level) and how much heat it generates. It transforms energy efficiency from an abstract concept into a clear, measurable metric that anyone—from a solo developer to a massive enterprise—can track, understand, and optimize.

---

## The Business Case: How Organizations Save Money

Energy inefficiency in software is a silent budget killer at scale. DoL8 provides measurable return on investment (ROI) for both massive enterprises and individual developers.

### Example 1: Enterprise Cloud Cost Reduction

If a data-processing microservice runs 10,000 times a day *per instance* across a fleet of 500 cloud instances, reducing its energy footprint by just 800 joules per run saves approximately 1.46 terajoules per year. This equals roughly 405,000 kWh of wasted electricity, translating to **over $60,000 saved annually** (at $0.15/kWh) on pure compute waste for a single service.

### Example 2: Frontend Optimization for Mobile Users

In our demo, DoL8 analyzed a standard company landing page. The tool instantly flagged that the page consumed 211 joules per session and identified the root causes: 10 large, unoptimized images and 233 concurrent CSS animations. By implementing lazy loading and reducing unnecessary animations, frontend energy consumption was reduced by up to 40%. This directly improves mobile device battery life, reduces bounce rates, and boosts Core Web Vitals scores.

---

## System Requirements

Before installing, ensure your environment meets these hardware and software requirements:

- **Operating System:** Bare-metal Linux (for example, Ubuntu, Fedora, or Linux Mint).
- **Hardware:** An Intel or AMD CPU that exposes RAPL (Running Average Power Limit) counters through the Linux `powercap` interface.
- **Software:** Python 3.9 or higher.

> [!NOTE]
> Virtual machines (VMs) and Windows Subsystem for Linux (WSL2) are not supported for energy profiling because hypervisors hide physical hardware counters. However, the codebase includes a mocked test suite in the `tests/` directory so development and testing can occur on any OS.

---

## The Problems DoL8 Solves

1. **Invisible Energy Costs:** Traditional profilers measure time but not energy. DoL8 exposes the hidden metric of joules, proving that speed is not the same as efficiency.
2. **Silent Thermal Throttling:** Overheated processors silently reduce their frequency to manage heat, causing mysterious performance degradation. DoL8 detects and reports these thermal events.
3. **Unreliable Single-Run Measurements:** System noise, background tasks, and CPU boost states can make single-run energy measurements misleading. DoL8 employs statistical methods, including the coefficient of variation, to quantify measurement confidence.
4. **Frontend Bloat:** Web applications can drain user-device batteries through unoptimized assets. DoL8 scans the DOM to identify and report specific energy-draining patterns.

---

## Installation and Setup

**1. Clone the repository:**

```bash
git clone https://github.com/uyg7x/8DoTs.git
cd 8DoTs
```

**2. Create and activate a virtual environment:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

**3. Install the core DoL8 tool:**

```bash
python -m pip install -e .
```

**4. Fix RAPL permissions (crucial for Linux):**

If your system restricts access to powercap counters, apply this udev rule to make them **read-only**:

```bash
sudo bash -c 'echo SUBSYSTEM=="powercap", MODE="0444" > /etc/udev/rules.d/99-rapl.rules'
sudo udevadm control --reload
sudo udevadm trigger
```

**5. Verify system readiness:**

```bash
python -m dol8.cli doctor
```

**Expected healthy output:**

```text
[DoL8 Doctor] System Check
[OK] Python 3.9+ detected
[OK] RAPL interface accessible (read-only)
[OK] Thermal sensors detected (thermal_zone0)
[OK] All systems ready for profiling.
```

---

<!-- Add your Core File Profiler screenshot at docs/images/core-file-profiler.png, then uncomment the next line. -->
<!-- ![DoL8 Core File Profiler](docs/images/core-file-profiler.png) -->

## Pillar 1: Core File Profiler (Backend)

**What it is:** A high-resolution, multi-run backend profiler for Python, Java, C++, and compiled binaries. It measures energy, power, and thermal impact with statistical confidence scoring.

**When to use it:** When you need to measure the energy footprint of a single script, algorithm, or backend microservice.

**Installation:** No additional dependencies are required beyond the core setup.

### How to Use

```bash
# Basic single run
python -m dol8.cli run my_script.py

# Professional run: three runs, one warmup, and JSON export for CI/CD
python -m dol8.cli run my_script.py --runs 3 --warmup 1 --json results.json
```

**Why multiple runs?** A single execution can be skewed by background operating-system noise. Multiple runs calculate the coefficient of variation (CoV). If the CoV is under 5%, DoL8 labels the result as **HIGH CONFIDENCE**.

---

<!-- Add your Folder Comparison Engine screenshot at docs/images/folder-comparison-engine.png, then uncomment the next line. -->
<!-- ![DoL8 Folder Comparison Engine](docs/images/folder-comparison-engine.png) -->

## Pillar 2: Folder Comparison Engine (A/B Testing)

**What it is:** An enterprise-grade A/B testing tool for entire project directories. It uses a black-box measurement approach with automatic CPU cooldown periods to enable statistically valid comparisons.

**When to use it:** When you have refactored an entire project (for example, `old_version/` versus `new_version/`) and need mathematical evidence that the new codebase is more energy-efficient.

**Installation:** No additional dependencies are required.

> [!NOTE]
> The directory is named `Compare_8DoTs/` for legacy repository compatibility.

### How to Use

```bash
python Compare_8DoTs/compareJ.py old_version/ new_version/ --entry main.py --runs 3
```

**How it works under the hood:**

1. **Pre-flight check:** Reads CPU temperature. If it exceeds 60°C, it warns you to cool down to prevent thermal throttling from skewing results.
2. **Test old:** Enters the `old_version/` directory, runs the entry file three times, and measures total energy.
3. **CPU cooldown:** Pauses for five seconds to let the CPU temperature normalize, ensuring a fair comparison.
4. **Test new:** Enters the `new_version/` directory, runs the entry file three times, and calculates the exact delta percentage saved.

---

<!-- Add your Web Energy Profiler screenshot at docs/images/web-energy-profiler.png, then uncomment the next line. -->
<!-- ![DoL8 Web Energy Profiler](docs/images/web-energy-profiler.png) -->

## Pillar 3: Web Energy Profiler (Frontend/JS)

**What it is:** A headless-browser automation tool with JavaScript injection. It measures frontend energy, scans the DOM for bloat, and calculates real-world cloud costs and CO₂ emissions.

**When to use it:** When you want to measure the battery drain of HTML/CSS/JavaScript websites or identify why your frontend is draining user-device batteries.

**Installation:** Requires Playwright and the PDF export library.

```bash
python -m pip install playwright fpdf2
sudo playwright install-deps chromium
playwright install chromium
```

### How to Use

```bash
# Run the interactive web profiler
python URL_8DoTs/URL_8DoTs.py
```

> [!WARNING]
> The auto-explorer clicks up to 10 interactive elements. Only run this against localhost URLs or read-only staging pages—live production URLs risk accidental form submissions, logouts, or purchases.

**Workflow:**

1. Prompts you for a URL, such as `http://localhost:3000`.
2. Launches headless Chromium and injects a custom DOM analyzer.
3. Automatically discovers and clicks interactive elements such as tabs and buttons.
4. Generates a color-coded terminal report and saves `.txt`, `.md`, and `.pdf` files in the `reports/` directory.
5. **Intelligent Advisor:** Highlights specific issues such as “10 large image(s) found. Use responsive images” or “233 CSS animations running. Reduce or use prefers-reduced-motion.”

---

## Visualizing the Output

Add screenshots of the real DoL8 terminal reports below. Save the three images in `docs/images/`, then remove the comment markers around the image links.

### Pillar 1: Core File Profiler

<!-- Add: docs/images/core-file-profiler.png -->
<!-- ![DoL8 Core File Profiler](docs/images/core-file-profiler.png) -->

### Pillar 2: Folder Comparison Engine

<!-- Add: docs/images/folder-comparison-engine.png -->
<!-- ![DoL8 Folder Comparison Engine](docs/images/folder-comparison-engine.png) -->

### Pillar 3: Web Energy Profiler

<!-- Add: docs/images/web-energy-profiler.png -->
<!-- ![DoL8 Web Energy Profiler](docs/images/web-energy-profiler.png) -->

---

## Future Vision

As software systems grow, so does their computational carbon footprint. DoL8 is building toward a future where energy efficiency is a first-class citizen in software quality assurance.

Our roadmap includes:

1. **Unified CLI Experience:** Consolidating all pillars under a single, intuitive command structure, such as `dol8 compare` and `dol8 web`.
2. **Native CI/CD Integration:** Providing out-of-the-box GitHub Actions to automatically fail pull requests that introduce energy regressions beyond a defined threshold.
3. **Expanded Hardware Support:** Adding detailed GPU and specialized accelerator energy profiling for machine-learning and high-performance-computing workloads.
4. **Enhanced Resource Tracking:** Correlating energy spikes with specific file I/O or memory-allocation patterns to help developers optimize storage and memory efficiency.

---

## Limitations and Solutions

We believe in transparent engineering. Below are the current limitations of the tool and the practical solutions we have implemented or recommend.

| Limitation | Impact | The DoL8 Solution |
| :--- | :--- | :--- |
| **Platform Restriction** | Requires bare-metal Linux. Windows and macOS do not expose RAPL. | Designate a dedicated Linux machine or CI runner specifically for energy profiling. The codebase includes a mocked test suite in the `tests/` directory so development can occur on any OS. |
| **Thermal Throttling** | Overheated CPUs slow down, artificially inflating energy metrics. | The Folder Comparison Engine includes a pre-flight thermal check and an automatic five-second cooldown between tests to ensure fair, thermally stable A/B comparisons. |
| **Frontend vs. Backend** | HTML/CSS cannot be run directly via Python like backend scripts. | Use the Web Profiler (Pillar 3). It hosts the site locally and measures the browser’s CPU energy while rendering the DOM, providing frontend metrics. |
| **Multi-tenant Noise** | RAPL measures at the CPU-socket level, not per individual process. | Run DoL8 on a dedicated, idle machine. Use the `--runs` flag to average out background operating-system noise. Disable unnecessary background services during measurement. |

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.

---

<p align="center">
  <b>Built for a greener, more efficient software future.</b>
</p>
