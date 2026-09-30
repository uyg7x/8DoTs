"""Background sampler for energy, temperature, frequency, and timeline data."""

from __future__ import annotations

import threading
import time
import importlib
from typing import Any, Dict, List, Optional

from .rapl import RAPLReader, RaplUnavailable, total_energy_joules
from .thermal import THROTTLE_RISK_MAX_C, ThermalMonitor, psutil


class PollingThread(threading.Thread):
    """Sample RAPL and thermal state while a target process is running."""

    def __init__(
        self,
        reader: RAPLReader,
        thermal: ThermalMonitor,
        interval_s: float = 0.1,
    ) -> None:
        """Create a daemon poller with per-run peaks and timeline storage."""
        super().__init__(daemon=True)
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.reader = reader
        self.thermal = thermal
        self.interval_s = interval_s
        self.stop_flag = threading.Event()
        self.samples: List[Dict[str, Any]] = []
        self.peak_watts = 0.0
        self.peak_temp: Optional[float] = None
        self.throttle_events: List[Dict[str, float]] = []
        self.error: Optional[str] = None
        self.frequency_available = False
        self._ranges: Dict[str, int] = {}
        self.gpu_available = False
        self._gpu_module: Any = None
        self._gpu_handle: Any = None

    def run(self) -> None:
        """Collect counter deltas and sensor samples until stopped."""
        try:
            self._ranges = self.reader.ranges()
            previous = self.reader.read_all()
        except RaplUnavailable as error:
            self.error = str(error)
            return
        previous_time = time.perf_counter()
        previous_frequency = self._frequency()
        self.frequency_available = previous_frequency is not None
        self._initialize_gpu()
        start_time = previous_time
        cumulative_joules = 0.0
        while not self.stop_flag.wait(self.interval_s):
            sampled_at = time.perf_counter()
            try:
                current = self.reader.read_all()
                energy = self.reader.delta(previous, current, self._ranges)
            except RaplUnavailable as error:
                self.error = str(error)
                break
            elapsed = max(sampled_at - previous_time, 1e-9)
            joules = total_energy_joules(energy)
            watts = joules / elapsed
            cumulative_joules += joules
            temperature = self.thermal.sample()
            if temperature is not None:
                self.peak_temp = max(self.peak_temp or temperature, temperature)
            frequency = self._frequency()
            gpu = self._sample_gpu()
            if (
                frequency is not None
                and previous_frequency is not None
                and frequency < previous_frequency * 0.8
                and temperature is not None
                and temperature >= THROTTLE_RISK_MAX_C - 15.0
            ):
                self.throttle_events.append(
                    {
                        "timestamp": sampled_at - start_time,
                        "temp": temperature,
                        "freq_before": previous_frequency,
                        "freq_after": frequency,
                    }
                )
            self.peak_watts = max(self.peak_watts, watts)
            self.samples.append(
                {
                    "t_seconds": sampled_at - start_time,
                    "joules_cum": cumulative_joules,
                    "watts": watts,
                    "temp_c": temperature,
                    "gpu_watts": gpu.get("watts") if gpu else None,
                    "gpu_temp_c": gpu.get("temperature_c") if gpu else None,
                }
            )
            previous = current
            previous_time = sampled_at
            previous_frequency = frequency

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal the worker to stop and join it before the final counter read."""
        self.stop_flag.set()
        if self.is_alive():
            self.join(timeout)
        if self.is_alive():
            raise RuntimeError("Polling thread did not stop before timeout")
        self._shutdown_gpu()

    def timeline(self) -> List[Dict[str, Any]]:
        """Return one representative sample per one-second bucket."""
        buckets: Dict[int, Dict[str, Any]] = {}
        for sample in self.samples:
            bucket = int(sample["t_seconds"])
            buckets[bucket] = sample
        return [buckets[index] for index in sorted(buckets)]

    @staticmethod
    def _frequency() -> Optional[float]:
        """Return current CPU frequency in MHz when psutil supports it."""
        if psutil is None:
            return None
        try:
            frequency = psutil.cpu_freq()
        except (AttributeError, OSError, RuntimeError):
            return None
        return float(frequency.current) if frequency and frequency.current else None

    def _initialize_gpu(self) -> None:
        """Initialize NVIDIA sampling only when pynvml and a GPU are present."""
        try:
            module = importlib.import_module("pynvml")
            module.nvmlInit()
            self._gpu_handle = module.nvmlDeviceGetHandleByIndex(0)
            self._gpu_module = module
            self.gpu_available = True
        except Exception:
            self._gpu_module = None
            self._gpu_handle = None
            self.gpu_available = False

    def _sample_gpu(self) -> Optional[Dict[str, float]]:
        """Return NVIDIA power and temperature when optional sampling works."""
        if not self.gpu_available or self._gpu_module is None:
            return None
        try:
            module = self._gpu_module
            handle = self._gpu_handle
            power = float(module.nvmlDeviceGetPowerUsage(handle)) / 1000.0
            temperature = float(module.nvmlDeviceGetTemperature(handle, module.NVML_TEMPERATURE_GPU))
            return {"watts": power, "temperature_c": temperature}
        except Exception:
            self.gpu_available = False
            return None

    def _shutdown_gpu(self) -> None:
        """Release NVML resources after polling has stopped."""
        if self._gpu_module is not None:
            try:
                self._gpu_module.nvmlShutdown()
            except Exception:
                pass
            self._gpu_module = None
            self._gpu_handle = None
