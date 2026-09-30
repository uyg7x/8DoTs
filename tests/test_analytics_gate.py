"""Hardware-free tests for scan, compare, watch, and gate helpers."""

import json
from types import SimpleNamespace

from dol8.compare import compare, scan
from dol8.gate import boot_energy, evaluate_gate
from dol8.stats import aggregate
from dol8.watch import cpu_time_share, estimate_app_energy, read_checkpoint, write_checkpoint
import dol8.watch as watch_module


def test_scan_is_sequential_and_isolates_failures() -> None:
    """Continue scanning after a target failure and return partial status."""
    calls = []

    def fake_run(target):
        calls.append(target)
        if target == "bad.py":
            return {"returncode": 2}
        return {"returncode": 0, "energy_joules": 10.0}

    result = scan(["one.py", "bad.py", "three.py"], fake_run)

    assert calls == ["one.py", "bad.py", "three.py"]
    assert result["summary"]["exit_code"] == 2
    assert result["summary"]["total_energy_joules"] == 20.0


def test_scan_calculates_cost_and_energy_ratio() -> None:
    """Summarize comparative cost and most-to-least energy ratio."""
    result = scan(
        ["small.py", "large.py"],
        lambda target: {"energy_joules": 10.0 if target == "small.py" else 20.0},
        price_per_kwh=0.15,
    )

    assert result["summary"]["most_expensive"] == "large.py"
    assert result["summary"]["least_expensive"] == "small.py"
    assert result["summary"]["energy_ratio"] == 2.0
    assert result["summary"]["total_cost"] == 30 / 3_600_000 * 0.15


def test_compare_alternates_order_and_computes_verdict() -> None:
    """Alternate paired execution order to reduce thermal ordering bias."""
    def fake_run(target):
        return {"energy_joules": 10.0 if target == "a.py" else 20.0}

    result = compare("a.py", "b.py", fake_run, runs=2)

    assert result["order"] == [["a.py", "b.py"], ["b.py", "a.py"]]
    assert result["verdict"]["delta_pct"] == 100.0


def test_watch_attribution_and_atomic_checkpoint(tmp_path) -> None:
    """Bound attribution and recover checkpoint JSON after atomic replace."""
    assert cpu_time_share(3.0, 4.0) == 0.75
    assert estimate_app_energy(100.0, 3.0, 4.0) == {
        "app_share_pct": 75.0,
        "app_energy_estimated_joules": 75.0,
    }
    path = tmp_path / "session.json"
    write_checkpoint(str(path), {"machine_energy_joules": 10.0})

    assert read_checkpoint(str(path)) == {"machine_energy_joules": 10.0}
    assert not path.with_name(path.name + ".tmp").exists()


def test_gate_respects_threshold_and_noise_guard() -> None:
    """Fail only when both the percent threshold and noise guard are exceeded."""
    baseline = {"mean_j": 100.0, "stdev_j": 1.0, "runs": 5}
    clear_regression = evaluate_gate({"mean_j": 120.0, "stdev_j": 1.0, "runs": 5}, baseline, 15.0)
    noisy_change = evaluate_gate({"mean_j": 120.0, "stdev_j": 20.0, "runs": 5}, baseline, 15.0)

    assert not clear_regression.passed and clear_regression.beyond_noise
    assert noisy_change.passed and not noisy_change.beyond_noise


def test_gate_does_not_fail_when_sample_counts_are_unknown() -> None:
    """A zero stored deviation without sample counts is not evidence of significance."""
    result = evaluate_gate(
        {"mean_j": 130.0, "stdev_j": 0.0},
        {"mean_j": 100.0, "stdev_j": 0.0},
        15.0,
    )

    assert result.passed
    assert not result.beyond_noise


def test_boot_energy_uses_reader_and_reports_wrap_risk(tmp_path) -> None:
    """Convert current counters into a boot estimate without real sysfs."""
    uptime_file = tmp_path / "uptime"
    uptime_file.write_text("10.0 5.0", encoding="ascii")

    class FakeReader:
        def read_all(self):
            return {"PACKAGE": 20_000_000}

        def ranges(self):
            return {"PACKAGE": 1_000_000}

    result = boot_energy(FakeReader(), str(uptime_file))

    assert result["energy_joules"] == 20.0
    assert result["average_watts"] == 2.0
    assert result["wrap_warning"]


def test_auto_attach_uses_largest_cpu_time_increase(monkeypatch) -> None:
    """Prefer newly active processes over a process with more lifetime CPU."""
    slow_high_total = SimpleNamespace(
        pid=10, info={"cpu_times": SimpleNamespace(user=1000.0, system=0.0)}
    )
    recent_activity = SimpleNamespace(
        pid=20, info={"cpu_times": SimpleNamespace(user=110.0, system=0.0)}
    )

    class FakePsutil:
        NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        AccessDenied = type("AccessDenied", (Exception,), {})

        @staticmethod
        def process_iter(_attributes):
            return [slow_high_total, recent_activity]

    monkeypatch.setattr(watch_module, "psutil", FakePsutil)
    process, previous = watch_module._most_active_process({10: 999.0, 20: 100.0})

    assert process is recent_activity
    assert previous == 100.0
