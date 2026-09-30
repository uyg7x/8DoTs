"""Hardware-free tests for background polling and wrap-aware samples."""

import time

from dol8.poller import PollingThread


class CounterReader:
    """Return a short sequence of package-counter values."""

    def __init__(self):
        self.values = iter([9_000_000, 100, 1_000_100, 2_000_100])

    def ranges(self):
        return {"PACKAGE": 10_000_000}

    def read_all(self):
        return {"PACKAGE": next(self.values, 2_000_100)}

    @staticmethod
    def delta(start, end, ranges):
        return {
            "PACKAGE": ((ranges["PACKAGE"] - start["PACKAGE"] + end["PACKAGE"])
                        if end["PACKAGE"] < start["PACKAGE"]
                        else (end["PACKAGE"] - start["PACKAGE"])) / 1_000_000.0
        }


class EmptyThermal:
    """Supply the minimal thermal-monitor interface to the poller."""

    @staticmethod
    def sample():
        return None

    @staticmethod
    def stats():
        return {"baseline": None, "peak": None, "avg": None, "end": None, "rise": None}


def test_poller_handles_wrap_and_stops_before_final_read() -> None:
    """Capture wrap-adjusted samples and join the sampler cleanly."""
    poller = PollingThread(CounterReader(), EmptyThermal(), interval_s=0.01)
    poller.start()
    deadline = time.monotonic() + 1.0
    while not poller.samples and time.monotonic() < deadline:
        time.sleep(0.005)
    poller.stop(timeout=1.0)

    assert poller.samples
    assert poller.samples[0]["joules_cum"] > 1.0
    assert poller.peak_watts > 0.0
    assert not poller.is_alive()
    assert all(sample["gpu_watts"] is None for sample in poller.samples) or not poller.gpu_available
