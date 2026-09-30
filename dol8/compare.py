"""Sequential file scans and statistically honest A/B comparisons."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .stats import Stats, Verdict, ab_compare, aggregate


def scan(
    scripts: Sequence[str],
    run_target: Callable[[str], Mapping[str, Any]],
    price_per_kwh: Optional[float] = None,
) -> Dict[str, Any]:
    """Run each script sequentially and continue after individual failures."""
    if not scripts:
        raise ValueError("At least one target is required")
    results: List[Dict[str, Any]] = []
    for script in scripts:
        try:
            result = dict(run_target(script))
        except Exception as error:
            result = {"target": script, "returncode": 1, "error": str(error)}
        result.setdefault("target", script)
        result.setdefault("returncode", 0)
        results.append(result)
    succeeded = sum(1 for result in results if int(result.get("returncode", 0)) == 0)
    exit_code = 0 if succeeded == len(results) else 2 if succeeded else 1
    ranked = sorted(
        (result for result in results if "energy_joules" in result),
        key=lambda result: float(result["energy_joules"]),
        reverse=True,
    )
    total_energy = sum(float(result["energy_joules"]) for result in ranked)
    summary = [
        {
            "target": result["target"],
            "energy_joules": float(result["energy_joules"]),
            "share_pct": float(result["energy_joules"]) / total_energy * 100.0
            if total_energy > 0 else 0.0,
            "cost": float(result["energy_joules"]) / 3_600_000.0 * price_per_kwh
            if price_per_kwh is not None else None,
        }
        for result in ranked
    ]
    highest = ranked[0] if ranked else None
    lowest = ranked[-1] if ranked else None
    highest_energy = float(highest["energy_joules"]) if highest else 0.0
    lowest_energy = float(lowest["energy_joules"]) if lowest else 0.0
    return {
        "results": results,
        "summary": {
            "ranked": summary,
            "total_energy_joules": total_energy,
            "total_energy_kwh": total_energy / 3_600_000.0,
            "total_cost": total_energy / 3_600_000.0 * price_per_kwh
            if price_per_kwh is not None else None,
            "most_expensive": highest.get("target") if highest else None,
            "least_expensive": lowest.get("target") if lowest else None,
            "energy_ratio": highest_energy / lowest_energy if lowest_energy > 0 else None,
            "exit_code": exit_code,
        },
    }


def compare(
    first: str,
    second: str,
    run_target: Callable[[str], Mapping[str, Any]],
    runs: int = 5,
) -> Dict[str, Any]:
    """Run paired targets in alternating order and compare energy distributions."""
    if runs < 1:
        raise ValueError("runs must be at least one")
    collected: Dict[str, List[Dict[str, Any]]] = {first: [], second: []}
    order_log: List[List[str]] = []
    for index in range(runs):
        order = [first, second] if index % 2 == 0 else [second, first]
        order_log.append(order)
        for target in order:
            collected[target].append(dict(run_target(target)))
    stats = {
        target: aggregate([{"energy_joules": float(run["energy_joules"])} for run in values])
        for target, values in collected.items()
    }
    verdict = ab_compare(stats[first], stats[second])
    return {
        "targets": {target: collected[target] for target in (first, second)},
        "order": order_log,
        "statistics": {target: _stats_dict(value) for target, value in stats.items()},
        "verdict": _verdict_dict(verdict),
    }


def _stats_dict(value: Stats) -> Dict[str, Any]:
    """Convert a Stats object to JSON-friendly values."""
    return {
        "mean_j": value.mean_j,
        "stdev_j": value.stdev_j,
        "cov_pct": value.coefficient_of_variation * 100.0,
        "confidence": value.confidence,
        "runs": value.runs,
    }


def _verdict_dict(value: Verdict) -> Dict[str, Any]:
    """Convert a Verdict object to JSON-friendly values."""
    return {
        "delta_j": value.delta_j,
        "delta_pct": value.delta_pct,
        "significant": value.significant,
        "label": value.label,
    }
