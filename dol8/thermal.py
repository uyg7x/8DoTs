"""Optional CPU temperature and battery monitoring."""

from __future__ import annotations

import logging
import statistics
import time
from typing import Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:  # Thermal data is optional on unsupported systems.
    psutil = None  # type: ignore[assignment]

SAFE_MAX_C = 70.0
WARM_MAX_C = 85.0
THROTTLE_RISK_MAX_C = 100.0
SENSOR_PRIORITY = ("coretemp", "k10temp", "cpu_thermal", "acpitz")


class ThermalMonitor:
    """Read CPU temperatures when available and keep basic run statistics."""

    def __init__(self) -> None:
        """Initialize sensor selection and an empty temperature timeline."""
        self._sensor_key: Optional[str] = None
        self._logged_choice = False
        self._samples: List[Tuple[float, float]] = []

    def available(self) -> bool:
        """Return whether a thermal sensor zone can be queried."""
        return bool(self._select_sensor())

    def current(self) -> Optional[float]:
        """Return the selected CPU sensor temperature, excluding invalid values."""
        sensor_key = self._select_sensor()
        if sensor_key is None or psutil is None:
            return None
        try:
            sensors = psutil.sensors_temperatures() or {}
            readings = sensors.get(sensor_key, [])
        except (AttributeError, OSError, RuntimeError):
            return None
        for reading in readings:
            temperature = getattr(reading, "current", None)
            if isinstance(temperature, (int, float)) and temperature > 0:
                return float(temperature)
        return None

    def sample(self) -> Optional[float]:
        """Append a valid temperature sample and return its value."""
        temperature = self.current()
        if temperature is not None:
            self._samples.append((time.perf_counter(), temperature))
        return temperature

    def stats(self) -> Dict[str, Optional[float]]:
        """Return baseline, peak, average, ending temperature, and rise."""
        values = [temperature for _, temperature in self._samples]
        if not values:
            return {
                "baseline": None,
                "peak": None,
                "avg": None,
                "end": None,
                "rise": None,
            }
        baseline = values[0]
        peak = max(values)
        return {
            "baseline": baseline,
            "peak": peak,
            "avg": statistics.mean(values),
            "end": values[-1],
            "rise": peak - baseline,
        }

    @staticmethod
    def throttle_zone(temperature: float) -> str:
        """Classify a temperature using the module-level Celsius thresholds."""
        if temperature < SAFE_MAX_C:
            return "SAFE"
        if temperature < WARM_MAX_C:
            return "WARM"
        if temperature < THROTTLE_RISK_MAX_C:
            return "THROTTLE RISK"
        return "CRITICAL"

    def battery(self) -> Optional[Dict[str, object]]:
        """Return battery percentage and charging state, or None on desktops."""
        if psutil is None:
            return None
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, OSError, RuntimeError):
            return None
        if battery is None:
            return None
        return {"percent": float(battery.percent), "plugged": bool(battery.power_plugged)}

    def _select_sensor(self) -> Optional[str]:
        """Choose the preferred CPU sensor, falling back to the first zone."""
        if psutil is None:
            return None
        try:
            sensors = psutil.sensors_temperatures() or {}
        except (AttributeError, OSError, RuntimeError):
            return None
        if not sensors:
            self._sensor_key = None
            return None
        if self._sensor_key in sensors:
            return self._sensor_key
        lowered = {key.lower(): key for key in sensors}
        selected = None
        for priority in SENSOR_PRIORITY:
            selected = next((key for key in lowered if priority in key), None)
            if selected is not None:
                break
        if selected is None:
            selected = next(iter(sensors))
        self._sensor_key = selected
        if not self._logged_choice:
            logging.getLogger(__name__).info("Using thermal sensor zone %s", selected)
            self._logged_choice = True
        return selected


if __name__ == "__main__":
    monitor = ThermalMonitor()
    if not monitor.available():
        print("Thermal sensors unavailable")
    else:
        for _ in range(10):
            monitor.sample()
            time.sleep(0.5)
        print(monitor.stats())
