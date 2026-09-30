"""Hardware-free CLI parser and unsupported-platform checks."""

import dol8.cli as cli_module
from dol8.cli import _command_for, _map_run_exit, _measure_compare_target, _run_command, build_parser, main
from dol8.runner import RunResult
from dol8.stats import Baseline


def test_run_parser_accepts_documented_options() -> None:
    """Parse the documented multi-file run options."""
    args = build_parser().parse_args([
        "run", "a.py", "b.py", "--runs", "3", "--warmup", "2", "--json", "out.json",
        "--csv", "timeline.csv", "--timeout", "15", "--price", "0.15", "--runs-per-day", "5",
    ])

    assert args.command == "run"
    assert args.scripts == ["a.py", "b.py"]
    assert args.runs == 3
    assert args.json_path == "out.json"


def test_windows_platform_fails_with_documented_tool_status(monkeypatch, capsys) -> None:
    """Refuse unsupported hosts before trying to access Linux sysfs."""
    monkeypatch.setattr("dol8.cli._platform_supported", lambda: False)

    assert main(["boot-energy"]) == 1
    assert "requires Linux bare metal" in capsys.readouterr().err


def test_directory_target_runs_as_one_pytest_command() -> None:
    """Preserve test directories as one runnable target for energy gates."""
    command = _command_for("tests")

    assert command[1:3] == ["-m", "pytest"]
    assert command[-1] == "tests"


def test_single_crash_and_all_failed_scan_have_distinct_exit_codes() -> None:
    """Preserve target crash status 2 while retaining all-failed scan status 1."""
    assert _map_run_exit([2], target_count=1) == 2
    assert _map_run_exit([1, 2], target_count=2) == 1
    assert _map_run_exit([0, 2], target_count=2) == 2
    assert _map_run_exit([0, 0], target_count=2) == 0
    assert _map_run_exit([2], target_count=1, interrupted=True) == 130


def test_compare_discards_warmup_before_measured_sample(monkeypatch) -> None:
    """Run fresh baseline and warm-up before adding a comparison observation."""
    calls = []
    baseline = object()

    class FakeRunner:
        def run(self, command, timeout):
            calls.append(("warmup", command, timeout))
            return RunResult(0, "", "", 0.1)

    monkeypatch.setattr("dol8.cli.measure_baseline", lambda *args, **kwargs: calls.append(("baseline",)) or baseline)
    monkeypatch.setattr("dol8.cli.TargetRunner", FakeRunner)
    monkeypatch.setattr(
        "dol8.cli._measure_target",
        lambda target, reader, timeout: (calls.append(("measured", target)) or {"energy_joules": 20.0}, RunResult(0, "", "", 0.2)),
    )
    monkeypatch.setattr("dol8.cli.subtract_baseline", lambda result, value: calls.append(("subtract", value)) or dict(result))

    result, outcome = _measure_compare_target("target.py", object(), 5.0)

    assert [call[0] for call in calls] == ["baseline", "warmup", "measured", "subtract"]
    assert result["energy_joules"] == 20.0
    assert result["returncode"] == outcome.returncode == 0


class AvailableReader:
    """Provide readable counters and count final reads in interrupt tests."""

    def __init__(self) -> None:
        self.read_count = 0
        self.unavailable_reason = ""

    def available(self) -> bool:
        return True

    def read_all(self):
        self.read_count += 1
        return {"PACKAGE": self.read_count}


def test_interrupt_during_baseline_attempts_final_read(monkeypatch, capsys) -> None:
    """Return 130 and explain that no workload ran when baseline is interrupted."""
    reader = AvailableReader()
    monkeypatch.setattr(cli_module, "RAPLReader", lambda: reader)
    monkeypatch.setattr(
        cli_module, "measure_baseline",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    args = build_parser().parse_args(["run", "target.py", "--no-warmup"])

    assert _run_command(args) == 130
    assert "No workload data collected" in capsys.readouterr().out
    assert reader.read_count == 1


def test_interrupt_during_warmup_reports_no_measured_data(monkeypatch, capsys) -> None:
    """Return 130 and make a final read when the discarded warm-up is interrupted."""
    reader = AvailableReader()
    baseline = Baseline({}, {}, 0.0, None, 5.0)

    class InterruptingRunner:
        def run(self, command, timeout):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "RAPLReader", lambda: reader)
    monkeypatch.setattr(cli_module, "measure_baseline", lambda *args, **kwargs: baseline)
    monkeypatch.setattr(cli_module, "TargetRunner", InterruptingRunner)
    args = build_parser().parse_args(["run", "target.py"])

    assert _run_command(args) == 130
    assert "No measured data collected" in capsys.readouterr().out
    assert reader.read_count == 1


def _measured_result(target: str) -> dict:
    """Create a deterministic result model for orchestration tests."""
    return {
        "target": target, "duration_seconds": 0.5, "energy_joules": 8.0,
        "avg_watts": 16.0, "peak_watts": 20.0, "per_domain": {"PACKAGE": 8.0},
        "thermal": {"baseline_c": None, "peak_c": None, "avg_c": None, "end_c": None, "rise_c": None,
                    "throttling": "unknown (no frequency sensor)"},
        "throttle_events": [], "timeline": [], "gpu_available": False, "gpu": None, "battery": None,
    }


def test_measured_interrupt_emits_partial_json_report(tmp_path, monkeypatch, capsys) -> None:
    """Retain interrupted measured energy and label the report and JSON partial."""
    import json

    reader = AvailableReader()
    baseline = Baseline({}, {}, 0.0, None, 5.0)
    output_path = tmp_path / "partial.json"
    monkeypatch.setattr(cli_module, "RAPLReader", lambda: reader)
    monkeypatch.setattr(cli_module, "measure_baseline", lambda *args, **kwargs: baseline)
    monkeypatch.setattr(
        cli_module, "_measure_target",
        lambda *args: (_measured_result("target.py"), RunResult(130, "", "", 0.5)),
    )
    args = build_parser().parse_args([
        "run", "target.py", "--no-warmup", "--json", str(output_path),
    ])

    assert _run_command(args) == 130
    rendered = capsys.readouterr().out
    assert "PARTIAL REPORT (interrupted at t=0.5s)" in rendered
    assert "ENERGY:" in rendered
    assert json.loads(output_path.read_text(encoding="utf-8"))["partial"] is True


def test_interrupted_multi_scan_reports_completed_and_skipped_targets(tmp_path, monkeypatch, capsys) -> None:
    """Keep completed reports, include a partial target, and skip later files."""
    import json

    reader = AvailableReader()
    baseline = Baseline({}, {}, 0.0, None, 5.0)
    calls = []

    def measurement(target, _reader, _timeout):
        calls.append(target)
        return _measured_result(target), RunResult(130 if target == "b.py" else 0, "", "", 0.5)

    monkeypatch.setattr(cli_module, "RAPLReader", lambda: reader)
    monkeypatch.setattr(cli_module, "measure_baseline", lambda *args, **kwargs: baseline)
    monkeypatch.setattr(cli_module, "_measure_target", measurement)
    output_path = tmp_path / "scan.json"
    args = build_parser().parse_args([
        "run", "a.py", "b.py", "c.py", "--no-warmup", "--json", str(output_path),
    ])

    assert _run_command(args) == 130
    rendered = capsys.readouterr().out
    assert calls == ["a.py", "b.py"]
    assert "Target: a.py" in rendered
    assert "PARTIAL REPORT" in rendered
    assert "[SKIPPED] c.py" in rendered
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["partial"] is True
    assert payload["skipped_targets"] == ["c.py"]
    assert [item["metadata"]["cpu"] for item in payload["files"]] == [cli_module._cpu_name()] * 2
    assert payload["files"][1]["partial"] is True
    assert [row["target"] for row in payload["summary"]["ranked"]] == ["a.py"]
