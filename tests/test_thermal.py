"""Hardware-free tests for thermal and battery handling."""

from types import SimpleNamespace

import pytest

import dol8.thermal as thermal_module
from dol8.thermal import ThermalMonitor


class FakePsutil:
    """Provide controllable psutil sensor results."""

    def __init__(self, sensors=None, battery=None):
        self._sensors = sensors or {}
        self._battery = battery

    def sensors_temperatures(self):
        return self._sensors

    def sensors_battery(self):
        return self._battery


def test_prefers_cpu_sensor_and_ignores_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choose coretemp over other zones and reject zero readings."""
    monkeypatch.setattr(
        thermal_module,
        "psutil",
        FakePsutil({"acpitz": [SimpleNamespace(current=51)], "coretemp": [SimpleNamespace(current=0)]}),
    )
    monitor = ThermalMonitor()

    assert monitor.available()
    assert monitor.current() is None
    assert monitor.sample() is None
    assert monitor.stats()["baseline"] is None


def test_temperature_stats_and_fallback_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a fallback zone and aggregate valid temperatures."""
    fake = FakePsutil({"soc": [SimpleNamespace(current=42.0)]})
    monkeypatch.setattr(thermal_module, "psutil", fake)
    monitor = ThermalMonitor()
    monitor.sample()
    fake._sensors["soc"] = [SimpleNamespace(current=48.0)]
    monitor.sample()

    assert monitor.stats() == {"baseline": 42.0, "peak": 48.0, "avg": 45.0, "end": 48.0, "rise": 6.0}


@pytest.mark.parametrize(
    ("temperature", "zone"),
    [(69, "SAFE"), (70, "WARM"), (84, "WARM"), (85, "THROTTLE RISK"), (99, "THROTTLE RISK"), (100, "CRITICAL")],
)
def test_throttle_boundaries(temperature: float, zone: str) -> None:
    """Keep thermal classification boundaries stable."""
    assert ThermalMonitor.throttle_zone(temperature) == zone


def test_battery_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return battery details on laptops and None on desktops."""
    monkeypatch.setattr(
        thermal_module,
        "psutil",
        FakePsutil(battery=SimpleNamespace(percent=73.5, power_plugged=True)),
    )
    assert ThermalMonitor().battery() == {"percent": 73.5, "plugged": True}
    monkeypatch.setattr(thermal_module, "psutil", FakePsutil())
    assert ThermalMonitor().battery() is None
