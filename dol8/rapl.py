import sys
import time
from typing import Dict

UDEV_FIX = "Check your permissions. Try: sudo chmod -R a+r /sys/class/powercap/"

class RaplUnavailable(Exception):
    """Exception raised when RAPL cannot be accessed."""
    pass

# Try to import the Rust engine
try:
    import dol8_core
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

class RAPLReader:
    def __init__(self, root: str = "/sys/class/powercap"):
        self.root = root
        self.unavailable_reason = ""

        if not sys.platform.startswith("linux"):
            self.unavailable_reason = "RAPL is only available on Linux bare metal systems."
            self._rust_reader = None
        elif not HAS_RUST:
            self.unavailable_reason = "Rust backend (dol8_core) is not compiled."
            self._rust_reader = None
        else:
            try:
                self._rust_reader = dol8_core.RaplReader(root)
            except Exception as e:
                self._rust_reader = None
                self.unavailable_reason = f"Failed to initialize Rust RAPL reader: {e}"

    def available(self) -> bool:
        if not self._rust_reader:
            return False
        try:
            readings = self.read_all()
            if not readings:
                self.unavailable_reason = "No RAPL counters found. Access denied."
                return False
            return True
        except Exception as e:
            self.unavailable_reason = str(e)
            return False

    def read_all(self) -> Dict[str, int]:
        if not self._rust_reader:
            return {}
        return self._rust_reader.read_all()

    def measure(self, seconds: float) -> Dict:
        if not self._rust_reader:
            raise RaplUnavailable(self.unavailable_reason)

        start_readings = self.read_all()
        start_time = time.perf_counter()
        time.sleep(seconds) 
        end_readings = self.read_all()
        elapsed = time.perf_counter() - start_time

        ranges = self.ranges()
        per_domain = self.delta(start_readings, end_readings, ranges)
        total_joules = total_energy_joules(per_domain)
        avg_watts = total_joules / elapsed if elapsed > 0 else 0.0

        return {
            "elapsed_seconds": elapsed,
            "per_domain_joules": per_domain,
            "average_watts": avg_watts
        }

    def ranges(self) -> Dict[str, int]:
        domains = self.read_all().keys()
        return {domain: 262143328850 for domain in domains}

    @staticmethod
    def delta(start: Dict[str, int], end: Dict[str, int], ranges: Dict[str, int]) -> Dict[str, float]:
        deltas = {}
        for domain, start_val in start.items():
            if domain in end:
                end_val = end[domain]
                if end_val < start_val: # Wraparound
                    max_val = ranges.get(domain, 262143328850)
                    delta_uj = (max_val - start_val) + end_val
                else:
                    delta_uj = end_val - start_val
                deltas[domain] = delta_uj / 1_000_000.0
        return deltas

def total_energy_joules(per_domain_joules: Dict[str, float]) -> float:
    return sum(per_domain_joules.values())