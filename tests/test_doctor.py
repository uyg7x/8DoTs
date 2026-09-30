"""Hardware-free tests for DoL8 environment diagnostics."""

from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import dol8.doctor as doctor_module
from dol8.doctor import run_doctor


class MovingReader:
    """Provide two incrementing fake RAPL readings."""

    unavailable_reason = ""

    def __init__(self) -> None:
        self.value = 100

    def available(self) -> bool:
        return True

    def ranges(self) -> Dict[str, int]:
        return {"PACKAGE": 1000}

    def read_all(self) -> Dict[str, int]:
        self.value += 10
        return {"PACKAGE": self.value}


class FakeThermal:
    """Provide CPU temperature and no battery for desktop-like test hosts."""

    _sensor_key = "coretemp"

    def available(self) -> bool:
        return True

    def current(self):
        return 52.0

    def battery(self):
        return None


class MissingHardware:
    """Represent a host without RAPL or thermal sensors."""

    unavailable_reason = "fake powercap is absent"

    def available(self) -> bool:
        return False


class MissingThermal:
    """Represent unavailable optional thermal and battery sensors."""

    def available(self) -> bool:
        return False

    def current(self):
        return None

    def battery(self):
        return None


def test_doctor_ready_with_fake_moving_counter(tmp_path: Path, monkeypatch) -> None:
    """Pass required diagnostics using fake counters without sleeping."""
    monkeypatch.setattr(doctor_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(doctor_module, "psutil", SimpleNamespace(cpu_freq=lambda: SimpleNamespace(current=2200.0)))

    result = run_doctor(MovingReader(), FakeThermal(), str(tmp_path), probe_seconds=1.0)

    assert result.ready
    assert result.issue_count == 0
    assert "ENVIRONMENT: READY" in result.render()
    assert any(check.name == "Counter movement" and check.status == "PASS" for check in result.checks)
    assert any(check.name == "Wrap math" and check.status == "PASS" for check in result.checks)
    assert any(check.name == "Thermal sensor" and "coretemp" in check.detail for check in result.checks)
    assert list(tmp_path.iterdir()) == []


def test_doctor_reports_required_failures_and_optional_skips(tmp_path: Path, monkeypatch) -> None:
    """Report missing RAPL and output access as issues, not tracebacks."""
    monkeypatch.setattr(doctor_module, "psutil", None)

    result = run_doctor(MissingHardware(), MissingThermal(), str(tmp_path / "missing"))

    assert result.issue_count == 2
    assert not result.ready
    assert "ENVIRONMENT: 2 ISSUES FOUND" in result.render()
    assert any(check.name == "Counter movement" and check.status == "SKIP" for check in result.checks)
    assert any(check.name == "Battery sensor" and check.status == "SKIP" for check in result.checks)
    assert "99-rapl.rules" in result.render()
