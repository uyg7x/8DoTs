"""Environment and measurement-sanity diagnostics for ``dol8 doctor``."""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .rapl import RAPLReader, RaplUnavailable, UDEV_FIX
from .thermal import ThermalMonitor, psutil


@dataclass(frozen=True)
class DoctorCheck:
    """One named diagnostic with a status and human-readable detail."""

    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    """Aggregate doctor checks and determine whether required checks passed."""

    checks: List[DoctorCheck]

    @property
    def issue_count(self) -> int:
        """Count failed checks; skipped optional hardware does not block readiness."""
        return sum(1 for check in self.checks if check.status == "FAIL")

    @property
    def ready(self) -> bool:
        """Return whether every required diagnostic passed."""
        return self.issue_count == 0

    def render(self) -> str:
        """Format checks and the final readiness line for terminal output."""
        lines = ["DoL8 environment diagnostics"]
        lines.extend("[{}] {}: {}".format(check.status, check.name, check.detail) for check in self.checks)
        lines.append(
            "ENVIRONMENT: READY" if self.ready
            else "ENVIRONMENT: {} ISSUES FOUND".format(self.issue_count)
        )
        return "\n".join(lines)


def run_doctor(
    reader: Optional[RAPLReader] = None,
    thermal: Optional[ThermalMonitor] = None,
    output_directory: str = ".",
    probe_seconds: float = 1.0,
) -> DoctorResult:
    """Run RAPL, sensor, clock, wrap-math, and output-permission checks."""
    if probe_seconds < 0:
        raise ValueError("probe_seconds must be non-negative")
    rapl = reader or RAPLReader()
    monitor = thermal or ThermalMonitor()
    checks: List[DoctorCheck] = []
    try:
        rapl_available = rapl.available()
    except (OSError, RaplUnavailable, ValueError) as error:
        rapl_available = False
        rapl.unavailable_reason = str(error)
    if rapl_available:
        checks.append(DoctorCheck("RAPL sysfs", "PASS", "powercap counters are readable"))
        try:
            ranges = rapl.ranges()
            start = rapl.read_all()
            time.sleep(probe_seconds)
            end = rapl.read_all()
            deltas = RAPLReader.delta(start, end, ranges)
            moved = any(value > 0.0 for value in deltas.values())
            checks.append(DoctorCheck(
                "Counter movement",
                "PASS" if moved else "FAIL",
                "counter advanced during {:.3f}s probe".format(probe_seconds)
                if moved else "no counter movement during {:.3f}s probe".format(probe_seconds),
            ))
        except (OSError, RaplUnavailable, ValueError) as error:
            checks.append(DoctorCheck("Counter movement", "FAIL", str(error)))
    else:
        checks.append(DoctorCheck(
            "RAPL sysfs", "FAIL",
            "{} If counters exist but access is denied, try: {}".format(
                getattr(rapl, "unavailable_reason", "RAPL counters are unavailable")
                or "RAPL counters are unavailable",
                UDEV_FIX,
            ),
        ))
        checks.append(DoctorCheck("Counter movement", "SKIP", "requires readable RAPL counters"))

    synthetic = RAPLReader.delta({"TEST": 900}, {"TEST": 50}, {"TEST": 1000})["TEST"]
    wrap_ok = math.isclose(synthetic, 0.00015, rel_tol=0.0, abs_tol=1e-12)
    checks.append(DoctorCheck(
        "Wrap math", "PASS" if wrap_ok else "FAIL",
        "synthetic wrapped delta is {:.8f} J".format(synthetic),
    ))

    temperature = monitor.current()
    sensor_key = getattr(monitor, "_sensor_key", None)
    if monitor.available():
        zone = sensor_key or "selected zone"
        detail = "zone {} selected".format(zone)
        if temperature is None:
            detail += "; current reading unavailable"
        checks.append(DoctorCheck("Thermal sensor", "PASS", detail))
    else:
        checks.append(DoctorCheck("Thermal sensor", "SKIP", "no temperature sensors available"))

    battery = monitor.battery()
    checks.append(DoctorCheck(
        "Battery sensor", "PASS" if battery is not None else "SKIP",
        "{}% {}".format(battery["percent"], "plugged in" if battery["plugged"] else "on battery")
        if battery is not None else "not present (desktop or unsupported sensor)",
    ))

    frequency = None
    if psutil is not None:
        try:
            frequency = psutil.cpu_freq()
        except (AttributeError, OSError, RuntimeError):
            frequency = None
    checks.append(DoctorCheck(
        "CPU frequency", "PASS" if frequency and frequency.current else "SKIP",
        "throttle detection available" if frequency and frequency.current
        else "unavailable; throttle events will be reported as unknown",
    ))

    first_tick = time.perf_counter()
    time.sleep(0)
    second_tick = time.perf_counter()
    monotonic = math.isfinite(first_tick) and math.isfinite(second_tick) and second_tick >= first_tick
    checks.append(DoctorCheck(
        "Timing source", "PASS" if monotonic else "FAIL",
        "perf_counter is finite and monotonic" if monotonic else "perf_counter failed monotonicity probe",
    ))

    directory = Path(output_directory)
    try:
        if not directory.is_dir():
            raise OSError("output directory does not exist: {}".format(directory))
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", prefix=".dol8-doctor-", suffix=".tmp",
            dir=str(directory), delete=False,
        ) as output:
            output.write("ok")
            temporary_path = Path(output.name)
        temporary_path.unlink()
        checks.append(DoctorCheck("Output directory", "PASS", "{} is writable".format(directory)))
    except OSError as error:
        checks.append(DoctorCheck("Output directory", "FAIL", str(error)))
    return DoctorResult(checks)
