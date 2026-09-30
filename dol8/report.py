"""ASCII terminal, JSON, and CSV reporting helpers."""

from __future__ import annotations

import csv
import io
import json
import platform
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

TOOL_VERSION = "0.1.0"


def render_terminal(result: Mapping[str, Any]) -> str:
    """Render a compact ASCII-only measurement report."""
    stats = result.get("statistics", {})
    thermal = result.get("thermal", {})
    domains = result.get("per_domain", {})
    energy = float(result.get("energy_joules", 0.0))
    thermal_values = [thermal.get(key) for key in ("baseline_c", "peak_c", "avg_c", "end_c")]
    if all(value is None for value in thermal_values):
        thermal_line = "THERMAL: unavailable (no sensors)"
    else:
        thermal_line = "THERMAL: baseline {} C, peak {} C, avg {} C, end {} C, rise {} C".format(
            _fmt_optional(thermal.get("baseline_c")), _fmt_optional(thermal.get("peak_c")),
            _fmt_optional(thermal.get("avg_c")), _fmt_optional(thermal.get("end_c")),
            _fmt_optional(thermal.get("rise_c")),
        )
    domain_lines = []
    if isinstance(domains, Mapping):
        for name, value in domains.items():
            joules = float(value)
            ratio = joules / energy if energy > 0 else 0.0
            filled = max(0, min(20, round(ratio * 20)))
            domain_lines.append("  {}: {:.3f} J [{}{}] {:.1f}%".format(
                name, joules, "#" * filled, "." * (20 - filled), ratio * 100.0
            ))
    lines = [
        "===== DoL8 REPORT (HIGH-RESOLUTION ESTIMATE) =====",
        "Target: {}".format(result.get("target", "unknown")),
        "CPU/OS: {} / {}".format(result.get("cpu", "unknown"), result.get("os", platform.platform())),
        "Date: {}".format(result.get("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))),
        "TIME: {:.3f}s (+- {:.3f}s)".format(
            float(result.get("duration_seconds", 0.0)), float(stats.get("stdev_time_s", 0.0))
        ),
        "ENERGY: {:.3f} J ({:.8f} kWh); baseline removed {:.3f} J".format(
            energy, energy / 3_600_000.0, float(result.get("baseline_removed_joules", 0.0))
        ),
    ]
    if result.get("partial"):
        lines.insert(
            0,
            "PARTIAL REPORT (interrupted at t={:.1f}s)".format(
                float(result.get("duration_seconds", 0.0))
            ),
        )
    lines.extend(domain_lines or ["  Per-domain data unavailable"])
    lines.extend([
        "POWER: avg {:.3f} W, peak {:.3f} W".format(
            float(result.get("avg_watts", 0.0)), float(result.get("peak_watts", 0.0))
        ),
        thermal_line,
        "THROTTLING: {}".format(thermal.get("throttling", "unknown (no frequency sensor)")),
        "CONFIDENCE: {} runs, mean {:.3f} J +- {:.3f} J, CoV {:.2f}%, {}".format(
            int(stats.get("runs", 1)), float(stats.get("mean_j", energy)),
            float(stats.get("stdev_j", 0.0)), float(stats.get("cov_pct", 0.0)),
            stats.get("confidence", "LOW"),
        ),
    ])
    price = result.get("price_per_kwh")
    if price is not None:
        cost = energy / 3_600_000.0 * float(price)
        runs_per_day = float(result.get("runs_per_day", 0.0))
        lines.append("ECONOMICS: {:.8f} per run, {:.4f} per year".format(
            cost, cost * runs_per_day * 365.0
        ))
    grid_factor = result.get("grid_factor")
    if grid_factor is not None:
        lines.append("CO2: {:.3f} g per SCI-aligned estimation".format(
            energy / 3_600_000.0 * float(grid_factor) * 1000.0
        ))
    if result.get("gpu_available") is False:
        lines.append("GPU: not available")
    elif result.get("gpu"):
        gpu = result["gpu"]
        lines.append("GPU: {:.3f} W, {:.1f} C".format(
            float(gpu.get("watts", 0.0)), float(gpu.get("temperature_c", 0.0))
        ))
    survivors = result.get("survivors", [])
    if survivors:
        lines.append("WARNING: {} child processes could not be terminated: {}".format(
            len(survivors), list(survivors)
        ))
        lines.append("Their energy is NOT included in this measurement.")
    if result.get("saved_path"):
        lines.append("Saved: {}".format(result["saved_path"]))
    return "\n".join(
        wrapped
        for line in lines
        for wrapped in textwrap.wrap(line, width=72, break_long_words=True, break_on_hyphens=False)
    )


def to_json(result: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert a measurement result to the documented JSON schema."""
    thermal = result.get("thermal", {})
    stats = result.get("statistics", {})
    energy = float(result.get("energy_joules", 0.0))
    payload: Dict[str, Any] = {
        "metadata": {
            "tool": "DoL8",
            "version": TOOL_VERSION,
            "timestamp": result.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "cpu": result.get("cpu", "unknown"),
            "os": result.get("os", platform.platform()),
            "measurement": "high-resolution estimate",
        },
        "metrics": {
            "duration_seconds": float(result.get("duration_seconds", 0.0)),
            "energy_joules": energy,
            "energy_kwh": energy / 3_600_000.0,
            "avg_watts": float(result.get("avg_watts", 0.0)),
            "peak_watts": float(result.get("peak_watts", 0.0)),
        },
        "per_domain": dict(result.get("per_domain", {})),
        "thermal": {
            "baseline_c": thermal.get("baseline_c"),
            "peak_c": thermal.get("peak_c"),
            "end_c": thermal.get("end_c"),
            "rise_c": thermal.get("rise_c"),
            "throttling": thermal.get("throttling", "unknown (no frequency sensor)"),
        },
        "statistics": {
            "runs": int(stats.get("runs", 1)),
            "warmup_discarded": int(stats.get("warmup_discarded", 0)),
            "mean_j": float(stats.get("mean_j", energy)),
            "stdev_j": float(stats.get("stdev_j", 0.0)),
            "cov_pct": float(stats.get("cov_pct", 0.0)),
            "confidence": stats.get("confidence", "LOW"),
        },
    }
    if result.get("battery") is not None:
        payload["metadata"]["battery"] = {
            "percent": float(result["battery"]["percent"]),
            "plugged": bool(result["battery"]["plugged"]),
        }
    if result.get("price_per_kwh") is not None:
        payload["economics"] = {
            "price_per_kwh": float(result["price_per_kwh"]),
            "cost_per_run": energy / 3_600_000.0 * float(result["price_per_kwh"]),
            "runs_per_day": float(result.get("runs_per_day", 0.0)),
        }
    if result.get("grid_factor") is not None:
        payload["co2"] = {
            "grid_factor_kg_per_kwh": float(result["grid_factor"]),
            "co2_g": energy / 3_600_000.0 * float(result["grid_factor"]) * 1000.0,
            "label": "per SCI-aligned estimation",
        }
    if result.get("partial"):
        payload["partial"] = True
    if result.get("survivors"):
        payload["survivors"] = list(result["survivors"])
    if "gpu_available" in result:
        payload["gpu"] = {
            "available": bool(result["gpu_available"]),
            "watts": result.get("gpu", {}).get("watts") if result.get("gpu") else None,
            "temperature_c": result.get("gpu", {}).get("temperature_c") if result.get("gpu") else None,
        }
    return payload


def to_csv_timeline(samples: Iterable[Mapping[str, Any]]) -> str:
    """Serialize samples as CSV with the documented timeline columns."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["t_seconds", "joules_cum", "watts", "temp_c"])
    writer.writeheader()
    for sample in samples:
        writer.writerow({key: sample.get(key) for key in writer.fieldnames})
    return output.getvalue()


def write_json(path: str, result: Mapping[str, Any]) -> None:
    """Write a result using the public JSON schema."""
    with open(path, "w", encoding="utf-8") as output:
        json.dump(to_json(result), output, indent=2, sort_keys=True)
        output.write("\n")


def _fmt_optional(value: Optional[Any]) -> str:
    """Format optional numeric report values without inventing readings."""
    return "unavailable" if value is None else "{:.1f}".format(float(value))
