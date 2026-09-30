"""CI energy regression gate and boot-energy helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from .rapl import RAPLReader
from .stats import Stats


@dataclass
class GateResult:
    """Outcome and values for one baseline comparison."""

    baseline_j: float
    current_j: float
    delta_pct: float
    threshold_pct: float
    beyond_noise: bool
    passed: bool


def evaluate_gate(current: Any, baseline: Mapping[str, Any], threshold_pct: float = 15.0) -> GateResult:
    """Fail only for a threshold-exceeding increase outside combined noise."""
    current_mean, current_stdev = _mean_stdev(current)
    baseline_mean = float(baseline["mean_j"])
    baseline_stdev = float(baseline.get("stdev_j", 0.0))
    current_runs = _sample_count(current)
    baseline_runs = int(baseline.get("runs", 1))
    delta = current_mean - baseline_mean
    delta_pct = delta / baseline_mean * 100.0 if baseline_mean else (0.0 if delta == 0 else float("inf"))
    combined_std = (current_stdev ** 2 + baseline_stdev ** 2) ** 0.5
    beyond_noise = (
        current_runs >= 2 and baseline_runs >= 2 and delta > 2.0 * combined_std
    )
    passed = not (delta_pct > threshold_pct and beyond_noise)
    return GateResult(baseline_mean, current_mean, delta_pct, threshold_pct, beyond_noise, passed)


def save_baseline(path: str, stats: Any) -> None:
    """Persist a baseline's energy mean and standard deviation."""
    mean, deviation = _mean_stdev(stats)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mean_j": mean, "stdev_j": deviation, "runs": _sample_count(stats)}
    with target.open("w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")


def boot_energy(reader: RAPLReader, uptime_path: str = "/proc/uptime") -> Dict[str, Any]:
    """Estimate energy since boot from current counters and report wrap risk."""
    with open(uptime_path, "r", encoding="ascii") as uptime_file:
        uptime = float(uptime_file.read().split()[0])
    readings = reader.read_all()
    ranges = reader.ranges()
    per_domain = {name: value / 1_000_000.0 for name, value in readings.items()}
    energy = sum(per_domain.values())
    average_watts = energy / uptime if uptime > 0 else 0.0
    measure = getattr(reader, "measure", None)
    if measure is not None:
        current_watts = float(measure(seconds=0.1)["average_watts"])
    else:
        current_watts = average_watts
    wrap_windows = {
        name: (ranges[name] / 1_000_000.0 / current_watts) if current_watts > 0 else float("inf")
        for name in per_domain if name in ranges
    }
    warning = any(uptime > window for window in wrap_windows.values())
    return {
        "energy_joules": energy,
        "energy_kwh": energy / 3_600_000.0,
        "uptime_seconds": uptime,
        "average_watts": average_watts,
        "current_watts_estimate": current_watts,
        "per_domain": per_domain,
        "wrap_warning": warning,
        "wrap_window_seconds": wrap_windows,
    }


def _mean_stdev(stats: Any) -> tuple[float, float]:
    """Extract mean and deviation from a Stats object or JSON-like mapping."""
    if isinstance(stats, Stats):
        return stats.mean_j, stats.stdev_j
    if isinstance(stats, Mapping):
        return float(stats["mean_j"]), float(stats.get("stdev_j", 0.0))
    if isinstance(stats, list):
        values = [float(value) for value in stats]
        if not values:
            raise ValueError("At least one energy value is required")
        mean = sum(values) / len(values)
        deviation = (sum((value - mean) ** 2 for value in values) / (len(values) - 1)) ** 0.5 if len(values) > 1 else 0.0
        return mean, deviation
    raise TypeError("stats must be a Stats object, mapping, or list")


def _sample_count(stats: Any) -> int:
    """Return the available sample count, defaulting unknown data to one."""
    if isinstance(stats, Stats):
        return stats.runs
    if isinstance(stats, Mapping):
        return int(stats.get("runs", 1))
    if isinstance(stats, list):
        return len(stats)
    return 1
