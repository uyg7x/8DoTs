# DoL8

DoL8 is a Linux CLI that produces high-resolution estimates of program energy use from dynamically discovered RAPL counters. It can also sample CPU temperature and report run-to-run variation. Results are estimates, not exact attribution to a process.

## Requirements

- Linux bare metal with Intel or AMD RAPL exposed through `/sys/class/powercap`
- Python 3.9 or newer
- `psutil` for temperature, battery, and process observation features

DoL8 v1 does not support Windows or virtual machines without RAPL powercap access. Missing permissions are reported with a suggested udev rule.

## Install

```sh
python -m pip install .
```

For development and tests:

```sh
python -m pip install -e ".[test]"
python -m pytest
```

## Usage

```sh
dol8 run workload.py --runs 5 --json result.json --csv timeline.csv
dol8 run first.py second.py --price 0.15 --runs-per-day 20
dol8 compare before.py after.py --runs 5
dol8 watch --attach 1234 --out session.json
dol8 boot-energy --json boot.json
dol8 doctor
dol8 run tests/ --gate-baseline baseline.json --threshold 15
```

Targets run sequentially. A target crash or timeout still produces its energy report and returns status 2; tool or hardware failures return 1; Ctrl+C returns 130 after partial reporting where possible. Watch-mode process energy is CPU-time prorated and labeled ESTIMATED.

DoL8 streams target stdout and stderr as prefixed lines. Python script targets run with `PYTHONUNBUFFERED=1`; other runtimes may buffer their own output according to their runtime behavior.

## Current status

- Code checks: 54 automated tests passed on the Windows development host; hardware interfaces are mocked in CI tests.
- Static diagnostics: clean in the current workspace.
- Hardware validation: not complete. This Windows host does not expose Linux RAPL counters, and the real-machine validation table remains unfilled.
- Release readiness: RAPL measurements have not yet been verified against bare-metal Linux hardware.

## GitHub Actions energy gate

RAPL access requires a bare-metal Linux runner with powercap permissions. Save a baseline from a prior DoL8 result, then use it on the same runner class:

```yaml
jobs:
	energy:
		runs-on: [self-hosted, linux]
		steps:
			- uses: actions/checkout@v4
			- run: python -m pip install .[test]
			- run: dol8 run tests/ --gate-baseline baseline.json --threshold 15
```

## Measurement validation

Real hardware validation is intentionally separate from CI. Record physical-meter, `turbostat`, repeatability, discrimination, and profiler-overhead results in [docs/VALIDATION.md](docs/VALIDATION.md). No hardware measurements are claimed by this repository.
