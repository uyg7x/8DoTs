"""Hardware-free checks for statistics and report serialization."""

import csv
import io

import pytest

from dol8.report import render_terminal, to_csv_timeline, to_json
from dol8.stats import Baseline, ab_compare, aggregate, subtract_baseline


def test_aggregate_confidence_and_sample_deviation() -> None:
    """Aggregate sample data and classify its coefficient of variation."""
    stats = aggregate([{"energy_joules": 100.0}, {"energy_joules": 101.0}, {"energy_joules": 99.0}])

    assert stats.mean_j == pytest.approx(100.0)
    assert stats.stdev_j == pytest.approx(1.0)
    assert stats.confidence == "HIGH"
    assert stats.runs == 3


def test_single_run_has_low_confidence() -> None:
    """Do not present an unmeasured variance as high confidence."""
    stats = aggregate([{"energy_joules": 100.0}])

    assert stats.stdev_j == 0.0
    assert stats.confidence == "LOW"


def test_ab_compare_marks_noisy_differences_inconclusive() -> None:
    """Avoid claiming a meaningful change when the shift is inside noise."""
    a = aggregate([{"energy_joules": 100}, {"energy_joules": 104}])
    b = aggregate([{"energy_joules": 102}, {"energy_joules": 106}])

    verdict = ab_compare(a, b)

    assert not verdict.significant
    assert verdict.label == "INCONCLUSIVE (difference within noise)"


def test_ab_compare_requires_samples_to_estimate_noise() -> None:
    """Do not declare significance from one observation per target."""
    verdict = ab_compare(
        aggregate([{"energy_joules": 100.0}]),
        aggregate([{"energy_joules": 130.0}]),
    )

    assert not verdict.significant
    assert verdict.label.startswith("INCONCLUSIVE")


def test_baseline_subtraction_floors_energy_at_zero() -> None:
    """Remove estimated idle energy without producing negative results."""
    baseline = Baseline({"PACKAGE": 5.0}, {"PACKAGE": 1.0}, 1.0, 40.0, 5.0)
    result = subtract_baseline(
        {"duration_seconds": 10.0, "energy_joules": 8.0, "per_domain": {"PACKAGE": 8.0}},
        baseline,
    )

    assert result["energy_joules"] == 0.0
    assert result["baseline_removed_joules"] == 8.0
    assert result["per_domain"] == {"PACKAGE": 0.0}


def test_json_schema_terminal_report_and_csv() -> None:
    """Expose the documented schema and timeline columns."""
    result = {
        "target": "fixture.py",
        "duration_seconds": 2.0,
        "energy_joules": 20.0,
        "avg_watts": 10.0,
        "peak_watts": 12.0,
        "per_domain": {"PACKAGE": 20.0},
        "statistics": {"runs": 2, "mean_j": 20.0, "stdev_j": 1.0, "cov_pct": 5.0, "confidence": "MEDIUM"},
        "thermal": {"baseline_c": 40.0, "peak_c": 42.0, "avg_c": 41.0, "end_c": 42.0, "rise_c": 2.0},
    }
    payload = to_json(result)
    rendered = render_terminal(result)
    csv_data = to_csv_timeline([{"t_seconds": 1, "joules_cum": 5, "watts": 5, "temp_c": 40}])
    parsed = list(csv.DictReader(io.StringIO(csv_data)))

    assert set(payload) == {"metadata", "metrics", "per_domain", "thermal", "statistics"}
    assert payload["metadata"]["measurement"] == "high-resolution estimate"
    assert payload["metrics"]["energy_kwh"] == pytest.approx(20 / 3_600_000)
    assert "DoL8 REPORT" in rendered and "PACKAGE" in rendered
    assert parsed[0] == {"t_seconds": "1", "joules_cum": "5", "watts": "5", "temp_c": "40"}


def test_report_identifies_missing_sensors_and_wraps_to_72_columns() -> None:
    """Keep the no-sensor case clear and terminal output within the spec width."""
    rendered = render_terminal({"thermal": {"baseline_c": None, "peak_c": None, "avg_c": None, "end_c": None}})

    assert "THERMAL: unavailable (no sensors)" in rendered
    assert all(len(line) <= 72 for line in rendered.splitlines())


def test_json_metadata_includes_battery_when_available() -> None:
    """Include optional power-source state without requiring a battery sensor."""
    payload = to_json({"battery": {"percent": 73.5, "plugged": True}})

    assert payload["metadata"]["battery"] == {"percent": 73.5, "plugged": True}


def test_report_warns_and_serializes_surviving_children() -> None:
    """Expose incomplete process containment in both terminal and JSON reports."""
    result = {"survivors": [4321]}

    assert "Their energy is NOT included in this measurement." in render_terminal(result)
    assert to_json(result)["survivors"] == [4321]
