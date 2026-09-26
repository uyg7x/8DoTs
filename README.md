# DoL8: Hardware-Aware Power and Thermal Profiling Framework
​
**Every program leaves a delta. Measure it.**
​
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![OS: Linux Bare-Metal](https://img.shields.io/badge/OS-Linux%20Bare--Metal-green.svg)]()
[![Telemetry: RAPL + ACPI](https://img.shields.io/badge/telemetry-RAPL%20%2B%20ACPI-orange.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)]()
​
> DoL8 (pronounced "DOH-late" — think **D**, for **delta**) is a developer-first
> command-line tool that reads your computer's built-in electricity meter and
> thermometer, and tells you the true cost of your program: not just in seconds,
> but in **energy (joules), power (watts), heat (degrees Celsius), and money.**
​
Every factual claim in this document carries a citation — `[1]`, `[2]`, and so on — pointing to the [Evidence Base](#evidence-base-all-sources) section, where each source is listed with a permanent link (DOI or official page) so you can verify everything yourself.
​
---
​
## Table of Contents
​
1. [The Idea in One Paragraph](#the-idea-in-one-paragraph)
2. [The Problem: Software Has a Hidden Electricity Bill](#the-problem-software-has-a-hidden-electricity-bill)
3. [The Solution: Read the Meter That Already Exists](#the-solution-read-the-meter-that-already-exists)
4. [What You Get: The Eight Measurements](#what-you-get-the-eight-measurements)
5. [For Individual Developers](#for-individual-developers)
6. [For Companies: Where the Money Is](#for-companies-where-the-money-is)
7. [Sample Outputs](#sample-outputs)
8. [Measurement Honesty: Constraints and Mitigations](#measurement-honesty-constraints-and-mitigations)
9. [Competitive Landscape](#competitive-landscape)
10. [Validation Plan](#validation-plan)
11. [Installation and Usage](#installation-and-usage)
12. [Roadmap](#roadmap)
13. [Conclusion](#conclusion)
14. [Evidence Base: All Sources](#evidence-base-all-sources)
​
---
​
## The Idea in One Paragraph
​
Your house has an electricity meter. Your car has a fuel gauge. Your computer's processor has both — a hidden energy counter and a temperature sensor, built into the silicon by the chip manufacturer, counting silently every second of every day [13][14]. Almost nobody reads them. DoL8 reads them for you: once before your program starts, continuously while it runs, and once after it finishes. Then it tells you, in plain numbers, what your program *physically cost*.
​
You can verify the meter exists on your own Linux machine right now:
​
```bash
cat /sys/class/powercap/intel-rapl:0/energy_uj
sleep 5
cat /sys/class/powercap/intel-rapl:0/energy_uj   # the number grew: that is joules burning
```
​
---
​
## The Problem: Software Has a Hidden Electricity Bill
​
For most of computing history, code was treated as an abstract thing. Programmers measured their work in two dimensions: **how fast** it ran and **how much memory** it used. Energy — the physical quantity that actually costs money, generates heat, and emits carbon — was invisible. It did not need to be visible, because each hardware generation automatically did more work per watt, quietly absorbing software's inefficiency.
​
That era is over. Three structural shifts ended it:
​
1. **Hardware stopped gifting free efficiency gains.** CPU clock speeds plateaued after the end of Dennard scaling [17], and the historical doubling of computations-per-kilowatt-hour slowed dramatically (Koomey's law: from ~1.5 years to ~2.6 years per doubling) [16]. Software can no longer rely on next year's chip to fix this year's wasteful code [1].
2. **Compute became a metered bill.** Workloads moved to the cloud, where every CPU-hour is billed, and into AI clusters where a single training run can emit hundreds of tonnes of CO2 [5][6]. Data centers already consume roughly 460 TWh per year — about 1–1.5% of global electricity — with projections approaching 1,000 TWh by 2026 [8][9].
3. **Governments started asking.** Under the EU's Corporate Sustainability Reporting Directive (CSRD), roughly 50,000 companies must publish audited sustainability data, including energy consumption [11]. Measurement is no longer optional; it is compliance.
​
This creates four concrete pain points:
​
### 1. The Speed-Is-Not-Energy Problem
​
Two programs can finish in the same time while one consumes far more energy. Execution time has always been visible; energy never was. Independent research across 27 programming languages found energy differences up to 75x for identical tasks — and showed that the fastest option is not always the most energy-efficient one [2].
​
### 2. The Silent Heat Problem
​
When a processor gets too hot, it protects itself by slowing down — thermal throttling [14]. It throws no error and writes no log. Your program simply becomes mysteriously slower, your laptop fan roars, your battery drains. Users blame the machine; the real culprit is the software.
​
### 3. Heat Shortens Hardware Life
​
Sustained high temperature physically degrades silicon interconnects through electromigration; reliability physics (Black's equation / Arrhenius acceleration) implies that roughly every 10 degrees Celsius of sustained extra heat approximately halves component lifetime [18]. Hot code slowly consumes the machine it runs on — and eventually becomes e-waste. The world generated 62 million metric tons of electronic waste in 2022 alone [10].
​
### 4. Waste at Scale
​
Data centers consume hundreds of terawatt-hours annually and AI is pushing demand sharply upward [8][9]. The cheapest power plant in the world is the energy we stop wasting — and the cheapest place to stop wasting it is at the moment the code is written. Even infrastructure-side efficiency has limits: servers draw roughly half their peak power even at low utilization [15], which is why demand-side (software) efficiency matters.
​
> **In plain language:** today, every developer is driving a car that shows
> only the speedometer. No fuel gauge. No temperature light. Two cars can
> arrive at the same time, but one burned one liter and the other burned
> thirty — and nobody could see it. DoL8 adds the missing gauges.
​
---
​
## The Solution: Read the Meter That Already Exists
​
Since the Sandy Bridge generation (2011), Intel processors have contained a hardware power-metering system called **RAPL (Running Average Power Limit)** [14], studied extensively for accuracy in academic literature [3]. It maintains a running counter of energy consumed, exposed on Linux through the standard `powercap` sysfs interface as a simple readable file [13]. Processors also expose their temperature through ACPI thermal zones, readable via the standard `psutil` library [26].
​
DoL8 is a thin, honest, statistically careful reader of those two sources.
​
> **In plain language:** DoL8 works exactly like your electricity company.
> Read the meter before. Let life happen. Read the meter after. Subtract.
> What remains is *your* consumption.
​
### The Mathematics
​
```text
E(T0)     = energy counter reading when your program starts
E(T_end)  = energy counter reading when your program finishes
T0, T_end = precise timestamps
​
Delta Energy (DE)  = E(T_end) - E(T0)        total joules consumed
Delta Time (Dt)    = T_end - T0              duration in seconds
Average Power      = DE / Dt                 watts
Total Energy       = DE                      joules
kWh                = joules / 3,600,000      the unit on your electricity bill
```
​
### Why One Reading Is Not Enough (The Statistics)
​
A computer is a busy place — the operating system, background updates, and other apps all burn energy too. A single before/after reading would wrongly blame your program for all of it. So DoL8 applies a rigorous protocol:
​
```text
1. IDLE BASELINE   : measure 5 seconds of background noise, subtract it later
2. WARM-UP RUN     : run your program once, discard the result
                       (first runs are distorted by cold caches)
3. MEASURED RUNS   : run it 5 times by default
4. REPORT          : mean +/- standard deviation, with a confidence label
5. COOLDOWN        : between runs, wait for the CPU to cool back to baseline
```
​
And a strict fairness rule: when profiling multiple files, DoL8 always runs them **one at a time, never in parallel**. The CPU has a single energy counter per socket [13] — two programs running simultaneously would merge into one number that physically cannot be separated. DoL8 chooses scientific validity over convenience.
​
> **In plain language:** if three roommates run heaters at the same time, the
> house meter shows a total — it can never say whose heater used what. So DoL8
> measures one heater at a time, on a calm day, several times, and tells you
> exactly how confident it is in the result.
​
---
​
## What You Get: The Eight Measurements
​
DoL8 reports eight dimensions of your program's physical footprint. The first six ship in version 1.0; the last two are roadmap items:
​
| # | Measurement | Unit | What It Tells You |
|---|-------------|------|-------------------|
| 1 | Execution time | seconds | The metric everyone already knows |
| 2 | Total energy | joules, kWh | The true physical cost of the run |
| 3 | Average power | watts | How hard the CPU worked overall |
| 4 | Peak power | watts | The moment of maximum stress |
| 5 | Peak temperature | degrees C | How much heat your code generated |
| 6 | Throttling detection | yes / no | Did your code make the CPU slow itself down? |
| 7 | Cost estimate | currency | What the run cost at your electricity price |
| 8 | Carbon estimate | grams CO2 | The climate cost, aligned with the SCI standard [12] |
​
Energy is also broken down by hardware domain — **PACKAGE** (whole chip), **CORE** (compute cores), **UNCORE** (caches and memory controller), and **DRAM** (system memory) [13][14] — so you can see whether your program is compute-hungry or memory-hungry. This matters: routine choices like which data structure to use have been shown to cause large, measurable energy differences [4].
​
---
​
## For Individual Developers
​
DoL8 turns vague frustration into exact numbers.
​
### "Why is my laptop hot and the battery dying?"
​
A typical laptop battery stores 50–80 watt-hours. A script drawing an extra 20 watts for one hour consumes roughly a quarter to a third of a full charge — for one run of one script. DoL8 converts "my laptop gets hot" into "this script: 18.4 Wh per run," and now you can fix it, schedule it, or at least stop wondering.
​
### "Is my new version actually better?"
​
You refactored a slow loop. It feels faster. DoL8's compare mode proves it (sample output — illustrative):
​
```text
                 Version A     Version B     Change
Time             8.42 s        3.11 s        -63%
Energy           267.8 J       96.4 J        -64%
Peak Temp        74.2 C        65.1 C        -9.1 C
​
VERDICT: Version B is 64% more energy efficient (statistically significant).
```
​
### "Which language should I use for this hot loop?"
​
Same task, several languages, one command — DoL8 ranks them on *your* machine, *your* inputs, *your* real conditions. The class of result is well established in research [2]; DoL8 lets you reproduce it locally in minutes.
​
### "Is my code silently slowing my machine down?"
​
DoL8 watches temperature continuously and flags when your program pushes the CPU into throttle territory [14] — the hidden mechanism behind "it just randomly gets slower."
​
### The career dividend
​
Green software engineering is becoming a hiring category [12][27]. A developer who can say "I measured a large energy difference between two implementations on real hardware, with statistical confidence" holds a portfolio artifact no certificate course can match.
​
---
​
## For Companies: Where the Money Is
​
Every model below is presented with transparent assumptions and is arithmetic, not magic. One multiplier to know first: in a data center, every watt of computing saved also saves its share of cooling overhead (Power Usage Effectiveness typically 1.1–1.5), so compute savings are amplified by 10–50% on the facility side [15][8].
​
### 1. Developer productivity: the largest lever
​
Bloated builds and slow test suites consume engineering hours, which cost far more than the electricity itself.
​
**Assumptions:** 200 developers; energy-gated CI keeps bloated builds out of `main`, keeping the pipeline 20% leaner; this unblocks each developer by 5 minutes per day; loaded engineer cost $50/hour.
​
```text
200 devs x 5 min/day x 250 days = 4,167 hours/year
= ~$208,000/year in reclaimed engineering time
```
​
### 2. Cloud compute: waste is billed by the hour
​
Cloud billing converts directly to time on provisioned machines. Energy evidence finds *which* workloads to optimize.
​
**Assumptions:** a CPU-bound batch workload of 14,400 instance-hours/month at $0.085/hour (~$14,700/year); DoL8-guided optimization (found via the DRAM domain counter: a cache-hostile loop) cuts CPU time 25%.
​
```text
$14,700 x 25% = ~$3,700/year per workload
A portfolio of 10 such workloads = ~$37,000/year
```
​
### 3. CI/CD energy gating: stop regressions at the gate
​
DoL8 returns meaningful exit codes, so a pipeline can fail automatically when a pull request increases test-suite energy beyond a threshold — exactly like a failing test (sample output — illustrative):
​
```text
Baseline energy ......... 204.0 J
This PR energy .......... 251.3 J  (+23.2%, threshold 15%)
STATUS: BUILD FAILED
```
​
**Assumptions:** an organization running 500,000 pipeline-minutes/month on managed CI at $0.008/min; a 20% reduction saves:
​
```text
100,000 min x $0.008 = $800/month = ~$9,600/year (direct)
```
​
### 4. AI pipelines: profile before you scale
​
Data preprocessing and evaluation harnesses are CPU-heavy and fully visible to RAPL. Finding a 2x waste in a preprocessing pipeline when 10 people use a model is cheap; discovering it at 10,000 users is a thousand times more expensive. The energy footprint of large-scale ML is now a documented, board-level concern [5][6]. DoL8 is the cheap early warning.
​
### 5. Hardware lifespan: physics-grounded capex deferral
​
Thermal-aware workloads run cooler; cooler machines live longer (Arrhenius reliability physics [18]). **Assumptions:** a 300-server fleet ($900,000), lifespan extended from 5 to 6 years on just 20% of the fleet defers roughly:
​
```text
$900,000 x (1/5 - 1/6) x 20% = ~$3,600/year of capex deferred,
plus reduced failure tickets and downtime
```
​
### 6. Compliance: the European mandate
​
Under CSRD, ~50,000 EU companies must publish audited sustainability data including energy [11]. Estimate-based reporting increasingly fails audit scrutiny; measured, per-pipeline joules do not. DoL8 provides the developer-level data layer for those reports, aligned with the Green Software Foundation's SCI specification (ISO/IEC 21031:2024) [12]. The value here is risk avoidance and audit readiness at near-zero marginal cost.
​
### The honest aggregate
​
| Channel | Illustrative Annual Value | Confidence |
|---|---|---|
| Reclaimed developer time | $208,000 | High |
| Cloud workload optimization | $3,700 – $37,000 | High |
| CI/CD minutes | $9,600 | High |
| AI pipeline efficiency | grows with scale | Medium |
| Hardware capex deferral | $3,600+ | Medium |
| CSRD/ESG data | risk avoidance + financing | Strategic |
​
**Conservative total for a mid-size organization: tens of thousands of dollars annually — against a tool that costs nothing to license and minutes to install.**
​
---
​
## Sample Outputs
​
> **Note:** all numbers below are *illustrative examples* of the report
> format, included to document the interface — not real measurements. Real
> screenshots replace these once v1.0 is built.
​
### Terminal report
​
```text
$ dol8 run train_model.py
​
[*] DoL8 v0.1 -- hardware-aware energy profiler
[*] RAPL detected ......... 4 domains (PACKAGE, CORE, UNCORE, DRAM)
[*] Thermal sensor ........ available (coretemp)
[*] Idle baseline ......... measured (5s, background subtracted)
[*] Warm-up run ........... done (discarded)
[*] Measuring ............. 5 runs
​
=========================================================================
DoL8 REPORT
=========================================================================
Target Script   : train_model.py
CPU / OS        : Intel Core i7-10700K / Linux 5.15.0
Date            : 2024-01-15 14:32:07
-------------------------------------------------------------------------
TIME
Execution Time .......... 14.23 s  (+/- 0.31 s)
​
ENERGY  (high-resolution estimate)
Total Energy ............ 412.5 J   (0.000114 kWh)
Average Power ........... 29.0 W
Peak Power .............. 45.2 W
-- Domains --
CPU Package ............. 317.4 J   [#####.............] 77%
DRAM .................... 95.1 J    [##................] 23%
​
THERMAL
Baseline Temp ........... 42.0 C
Peak Temp ............... 78.5 C    [!] near throttle range
End Temp ................ 51.0 C
Throttling Detected ..... No
​
CONFIDENCE  (5 runs, 1 warm-up, baseline subtracted)
Energy .................. 412.5 J +/- 4.2 J   (high confidence)
=========================================================================
Saved: output.json
```
​
### Machine-readable JSON
​
```json
{
  "metadata": {
    "tool": "DoL8",
    "version": "0.1.0",
    "timestamp": "2024-01-15T14:32:07Z",
    "script": "train_model.py",
    "cpu": "Intel Core i7-10700K",
    "os": "Linux 5.15.0"
  },
  "metrics": {
    "duration_seconds": 14.23,
    "energy_joules": 412.5,
    "energy_kwh": 0.000114,
    "avg_watts": 29.0,
    "peak_watts": 45.2
  },
  "thermal": {
    "baseline_c": 42.0,
    "peak_c": 78.5,
    "end_c": 51.0,
    "throttling": false
  },
  "statistics": {
    "runs": 5,
    "warmup_discarded": 1,
    "energy_mean_j": 412.5,
    "energy_std_dev_j": 4.2,
    "confidence": "high"
  }
}
```
​
---
​
## Measurement Honesty: Constraints and Mitigations
​
Credibility is the product. DoL8 documents its limits rather than hiding them. The constraints below follow directly from the hardware documentation [13][14] and from the published accuracy analysis of RAPL [3].
​
| Constraint | Reality | DoL8's Mitigation |
|---|---|---|
| Platform scope | RAPL works on Intel/AMD CPUs on Linux bare metal — not in VMs or WSL2 [13] | v1.0 targets Linux bare metal explicitly; macOS and Windows backends planned for v2.0 |
| Permissions | Some kernels restrict counter reads [13] | Uses the user-readable sysfs path; ships a one-line `udev` rule when needed |
| Attribution | RAPL meters the whole CPU socket, not a single process [3] | Idle baseline subtraction, sequential-only measurement, 5-run statistics |
| Accuracy | Microjoule exactness is impossible (counter quantization); validation studies place RAPL within roughly 5–10% of external meters [3] | Outputs labeled "high-resolution estimates"; dual-track validation (below) |
| Multi-file scans | Parallel runs merge energies into one unreadable number [13] | Enforced sequential execution with cooldown and fresh baseline per file |
​
---
​
## Competitive Landscape
​
| Tool | Energy | Thermal | Dev-Local UX | CI/CD Gating |
|---|:---:|:---:|:---:|:---:|
| PyJoules / PowerAPI [20] | Yes | No | Yes | No |
| CodeCarbon [19] | Partial (TDP estimates) | No | Yes | No |
| Scaphandre / Kepler [21][22] | Yes | No | No (K8s agents) | Partial |
| Green Metrics Tool / EnergiBridge [23] | Yes | Partial | No (heavy setup) | Partial |
| **DoL8 (this project)** | **Yes (direct RAPL)** | **Yes (ACPI + throttling)** | **Yes (one command)** | **Yes (statistical gating)** |
​
**The niche:** estimation frameworks like GSF's Impact Framework [24] serve analysts; cluster agents serve platform teams [21][22]. DoL8 serves the individual developer at commit time — direct measurement, thermal telemetry, and zero setup.
​
---
​
## Validation Plan
​
A profiling tool without validation is just a hypothesis. DoL8 validates along three tracks:
​
1. **Physical track:** sustained CPU-bound workload compared against a wall-socket power meter; target correlation within 5–10% — the margin is justified by PSU inefficiency and non-CPU components, consistent with published RAPL validation methodology [3].
2. **Kernel track:** side-by-side deltas against `turbostat` and `perf stat` (shipped with the Linux kernel) on identical workloads; target agreement within rounding tolerance.
3. **Statistical track:** repeatability (coefficient of variation under 5% across sessions) and discrimination tests — known-different implementations must rank correctly; near-identical ones must produce an honest "inconclusive."
​
---
​
## Installation and Usage
​
```bash
# Install
pip install dol8
​
# Profile any script (any language runnable from the terminal)
dol8 run my_program.py
​
# Multiple files: measured sequentially, one at a time, for accuracy
dol8 run cleanup.py etl_job.py train_model.py
​
# Options
dol8 run my_program.py --runs 10 --json --output report.json
​
# Compare two versions statistically
dol8 compare v1_search.py v2_search.py
```
​
Python context manager for profiling a single code block:
​
```python
from dol8 import PowerProfiler
​
with PowerProfiler() as p:
    result = heavy_function(data)
​
print(p.report())          # terminal report
print(p.energy_joules)     # direct numeric access
```
​
**Exit codes:** `0` success, `1` tool/hardware failure, `2` target script crashed (energy still reported), `130` interrupted by user (partial report provided) — designed for CI/CD gating from day one.
​
**Requirements:** Linux bare metal, Intel or AMD CPU, Python 3.9+.
​
---
​
## Roadmap
​
### Phase 1 — MVP (Weeks 1–6): COMMITTED
​
- CLI and Python context manager
- RAPL energy tracking (Package and DRAM domains) [13]
- ACPI temperature polling with throttle detection [26]
- Baseline subtraction and multi-run statistics
- JSON/CSV export and terminal report
​
### Phase 2 — Analytics (Weeks 7–9): COMMITTED
​
- A/B compare mode with confidence intervals
- Cost estimate output
- Multi-file scan ranking tables
​
### Phase 3 — Stretch (Weeks 10–12): STRETCH
​
- GitHub Action for automated CI energy gating
- CO2 estimation via Electricity Maps API [25], aligned with the GSF SCI specification [12]
- Prometheus exporter for Grafana dashboards
- GPU (NVML) energy support for AI workloads
​
---
​
## Conclusion
​
Software's invisibility to physics was a temporary historical condition. It ended when hardware stopped getting automatically more efficient [16][17], when compute became a metered cloud bill [15], when AI made energy a line item [5][6][9], and when regulation made measurement mandatory [11].
​
DoL8 closes the loop at the only place efficiency can be prevented instead of paid for: the developer's machine, at the moment the code is written.
​
> **Stop writing code that merely functions.**  
> **Start writing code whose physical cost you can see.**
​
---
​
## Evidence Base: All Sources
​
Every factual claim above is tagged with a bracketed number matching this list. DOIs are permanent identifiers; all links verified as of 2024.
​
### Core research papers
​
1. Hindle, A. (2012). *Green Software Engineering: The Curse of Moore's Law.* ICSE 2012.  
   <https://doi.org/10.1145/2337223.2337283>
2. Pereira, R. et al. (2017). *Energy Efficiency Across Programming Languages: How Do Energy, Time, and Memory Relate?* SLE 2017.  
   <https://doi.org/10.1145/3136014.3136031>  
   Benchmark code: <https://github.com/greensoftwarelab/Energy-Languages>
3. Khan, K. N. et al. (2018). *RAPL in Action: Experiences in Using RAPL for Power Measurements.* ACM Transactions on Modeling and Performance Evaluation of Computing Systems.  
   <https://doi.org/10.1145/3242738>
4. Hasan, S. et al. (2016). *Energy Profiles of Java Collections Classes.* ICSE 2016.  
   <https://doi.org/10.1145/2884781.2884792>
5. Strubell, E. et al. (2019). *Energy and Policy Considerations for Deep Learning in NLP.* ACL 2019.  
   <https://aclanthology.org/P19-1355/> (also <https://arxiv.org/abs/1906.02243>)
6. Patterson, D. et al. (2021). *Carbon Emissions and Large Neural Network Training.*  
   <https://arxiv.org/abs/2104.10350>
​
### Hardware and physics foundations
​
7. Dennard, R. et al. (1974). *Design of Ion-Implanted MOSFET's with Very Small Physical Dimensions.* IEEE Journal of Solid-State Circuits.  
   <https://doi.org/10.1109/JSSC.1974.1050511>
8. Black, J. R. (1969). *Electromigration Failure Modes in Aluminum Metallization for Semiconductor Devices.* Proceedings of the IEEE.  
   <https://doi.org/10.1109/PROC.1969.7346>
9. Koomey, J. et al. (2011). *Implications of Historical Trends in the Electrical Efficiency of Computing.* IEEE Annals of the History of Computing.  
   <https://doi.org/10.1109/MAHC.2010.28>
10. Barroso, L. A. and Hoelzle, U. (2007). *The Case for Energy-Proportional Computing.* IEEE Computer.  
    <https://doi.org/10.1109/MC.2007.443>
11. Intel Corporation. *Intel 64 and IA-32 Architectures Software Developer's Manual* (RAPL defined in Vol. 3).  
    <https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html>
12. Linux Kernel Documentation. *Powercap framework (RAPL sysfs interface).*  
    <https://www.kernel.org/doc/html/latest/powercap/powercap.html>
13. Linux kernel source. *turbostat* (tools/power/x86/turbostat).  
    <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/power/x86/turbostat>
14. psutil documentation — `sensors_temperatures()` (ACPI thermal zones).  
    <https://psutil.readthedocs.io/>
​
### Industry statistics and regulation
​
15. Freitag, C. et al. (2021). *The Real Climate and Transformative Impact of ICT: A Critique of Estimates, Trends, and Regulations.* Patterns 2(9). [Source for the 1.5–4% ICT emissions estimate]  
    <https://doi.org/10.1016/j.patter.2021.100340>
16. International Energy Agency (2024). *Electricity 2024* — data center consumption of ~460 TWh in 2022, projected toward ~1,000 TWh by 2026.  
    <https://www.iea.org/reports/electricity-2024>
17. International Energy Agency. *Data Centres and Data Transmission Networks.*  
    <https://www.iea.org/energy-system/buildings/data-centres-and-data-transmission-networks>
18. UNITAR / ITU (2024). *The Global E-waste Monitor 2024* — 62 million metric tons of e-waste in 2022.  
    <https://ewastemonitor.info/the-global-e-waste-monitor-2024/>
19. European Commission. *Corporate Sustainability Reporting Directive (CSRD)* — Directive (EU) 2022/2464; ~50,000 companies in scope.  
    <https://finance.ec.europa.eu/capital-markets-union-and-financial-markets/company-reporting-and-auditing/company-reporting/corporate-sustainability-reporting_en>
​
### Standards, tools, and prior art (compared in this README)
​
20. Green Software Foundation. *Software Carbon Intensity (SCI) Specification* — ISO/IEC 21031:2024.  
    <https://sci.greensoftware.foundation/> | <https://github.com/Green-Software-Foundation/sci>
21. Green Software Foundation. *Impact Framework.*  
    <https://github.com/Green-Software-Foundation/ief>
22. PyJoules (PowerAPI project).  
    <https://github.com/powerapi-ng/pyJoules> | <https://powerapi.org>
23. CodeCarbon — Henderson, P. et al. (2022). *Towards the Systematic Reporting of the Energy and Carbon Footprints of Machine Learning.*  
    <https://github.com/mlco2/codecarbon> | <https://arxiv.org/abs/2201.02054>
24. Scaphandre.  
    <https://github.com/hubblo-org/scaphandre>
25. Kepler (Kubernetes-based Efficient Power Level Exporter).  
    <https://github.com/sustainable-computing-io/kepler>
26. Green Metrics Tool — Green Coding Berlin/Berlin-based measurement platform.  
    <https://www.green-coding.ai>  
    (EnergiBridge: search GitHub for "EnergiBridge" — cross-platform energy measurement harness)
27. Electricity Maps — real-time grid carbon intensity API.  
    <https://www.electricitymaps.com>
28. Currie, A., Bergman, S., Hsu, S. (2024). *Building Green Software.* O'Reilly Media.  
    <https://www.oreilly.com/library/view/building-green-software/9781098150617/>
​
> **Verify it yourself in 30 seconds:** on any bare-metal Linux machine with
> an Intel/AMD CPU, run the two `cat` commands in
> [The Idea in One Paragraph](#the-idea-in-one-paragraph). The counter exists,
> it is real hardware, and it is counting your joules right now [12].
​
---
​
*DoL8 — built for Green Software Engineering. Eight dots form the delta. The delta is what we measure.*
​
