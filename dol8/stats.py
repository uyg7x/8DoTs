"""Baseline measurements and run-to-run statistics."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

from .rapl import RAPLReader
from .thermal import ThermalMonitor


@dataclass
class Baseline:
    """Idle power and temperature measurements used for subtraction."""

    per_domain_joules: Dict[str, float]
    per_domain_watts: Dict[str, float]
    idle_watts: float
    idle_temp: Optional[float]
    duration_seconds: float


@dataclass
class Stats:
    """Aggregate energy statistics for a set of measured runs."""

    mean_j: float
    stdev_j: float
    coefficient_of_variation: float
    confidence: str
    runs: int


@dataclass
class Verdict:
    """Statistical comparison of two energy distributions."""

    delta_j: float
    delta_pct: float
    significant: bool
    label: str


def measure_baseline(
    reader: RAPLReader, thermal: ThermalMonitor, seconds: float = 5.0
) -> Baseline:
    """Measure idle energy and temperature over the requested interval."""
    thermal.sample()
    measurement = reader.measure(seconds=seconds)
    temperature = thermal.sample()
    elapsed = float(measurement["elapsed_seconds"])
    per_domain = measurement["per_domain_joules"]
    if not isinstance(per_domain, dict):
        per_domain = {}
    domain_watts = {
        name: float(joules) / elapsed if elapsed > 0 else 0.0
        for name, joules in per_domain.items()
    }
    return Baseline(
        per_domain_joules={name: float(value) for name, value in per_domain.items()},
        per_domain_watts=domain_watts,
        idle_watts=float(measurement["average_watts"]),
        idle_temp=temperature,
        duration_seconds=elapsed,
    )


def aggregate(runs: List[Dict[str, float]]) -> Stats:
    """Aggregate energy_joules values and classify measurement confidence."""
    values = [float(run["energy_joules"]) for run in runs if "energy_joules" in run]
    if not values:
        raise ValueError("At least one run with energy_joules is required")
    mean = statistics.mean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    coefficient = deviation / abs(mean) if mean != 0 else (0.0 if deviation == 0 else math.inf)
    confidence = (
        "LOW" if len(values) < 2
        else "HIGH" if coefficient < 0.05
        else "MEDIUM" if coefficient < 0.15
        else "LOW"
    )
    return Stats(mean, deviation, coefficient, confidence, len(values))


def subtract_baseline(result: Dict[str, object], baseline: Baseline) -> Dict[str, object]:
    """Return a result copy with estimated idle energy removed and floored at zero."""
    adjusted = dict(result)
    duration = float(adjusted.get("duration_seconds", 0.0))
    total = max(0.0, float(adjusted.get("energy_joules", 0.0)) - baseline.idle_watts * duration)
    adjusted["energy_joules"] = total
    adjusted["baseline_removed_joules"] = max(
        0.0, float(result.get("energy_joules", 0.0)) - total
    )
    raw_domains = result.get("per_domain", {})
    if isinstance(raw_domains, dict):
        adjusted["per_domain"] = {
            name: max(0.0, float(joules) - baseline.per_domain_watts.get(name, 0.0) * duration)
            for name, joules in raw_domains.items()
        }
    return adjusted


def ab_compare(a: Stats, b: Stats) -> Verdict:
    """Compare mean energy, marking differences within combined noise inconclusive."""
    delta = b.mean_j - a.mean_j
    delta_pct = (delta / a.mean_j * 100.0) if a.mean_j else (0.0 if delta == 0 else math.inf)
    combined_std = math.sqrt(a.stdev_j ** 2 + b.stdev_j ** 2)
    significant = a.runs >= 2 and b.runs >= 2 and abs(delta) > 2.0 * combined_std
    label = "SIGNIFICANT" if significant else "INCONCLUSIVE (difference within noise)"
    return Verdict(delta, delta_pct, significant, label)
