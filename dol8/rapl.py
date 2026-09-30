"""Read Linux powercap RAPL energy counters safely."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional


UDEV_FIX = (
    "sudo bash -c 'echo SUBSYSTEM==\"powercap\",MODE=\"0666\" > "
    "/etc/udev/rules.d/99-rapl.rules' && "
    "sudo udevadm control --reload && sudo udevadm trigger"
)
DOMAIN_NAMES = {
    "package-0": "PACKAGE",
    "package": "PACKAGE",
    "core": "CORE",
    "uncore": "UNCORE",
    "dram": "DRAM",
}


class RaplUnavailable(Exception):
    """Raised when a RAPL counter cannot be discovered or read."""


@dataclass(frozen=True)
class _Domain:
    """Paths needed to read one discovered powercap domain."""

    energy_path: Path
    range_path: Path


class RAPLReader:
    """Discover and read energy counters exposed through Linux sysfs."""

    def __init__(self, root: Path = Path("/sys/class/powercap")) -> None:
        """Initialize a reader, optionally using a fake sysfs root in tests."""
        self.root = Path(root)
        self.unavailable_reason = ""
        self._domain_info: Dict[str, _Domain] = {}

    def domains(self) -> Dict[str, str]:
        """Return discovered domain names mapped to their energy file paths."""
        discovered: Dict[str, _Domain] = {}
        if not self.root.is_dir():
            self._domain_info = {}
            self.unavailable_reason = (
                "No readable RAPL domains were found under {}. "
                "DoL8 requires Linux powercap RAPL support.".format(self.root)
            )
            return {}
        try:
            pending = sorted(
                (path for path in self.root.iterdir() if "rapl" in path.name.lower()),
                reverse=True,
            )
            directories = []
            visited = set()
            while pending:
                directory = pending.pop()
                if not directory.is_dir():
                    continue
                resolved = directory.resolve()
                if resolved in visited:
                    continue
                visited.add(resolved)
                directories.append(directory)
                pending.extend(
                    child for child in directory.iterdir()
                    if "rapl" in child.name.lower() and child.is_dir()
                )
            for directory in directories:
                name_file = directory / "name"
                energy_path = directory / "energy_uj"
                range_path = directory / "max_energy_range_uj"
                if not energy_path.is_file() or not range_path.is_file():
                    continue
                try:
                    raw_name = name_file.read_text(encoding="ascii").strip().lower()
                except OSError:
                    continue
                label = (
                    "PACKAGE"
                    if raw_name.startswith("package-")
                    else DOMAIN_NAMES.get(raw_name, raw_name.upper().replace("-", "_"))
                )
                key = label
                suffix = 2
                while key in discovered:
                    key = "{}_{}".format(label, suffix)
                    suffix += 1
                discovered[key] = _Domain(energy_path, range_path)
        except OSError as error:
            self.unavailable_reason = self._error_message(error)
            self._domain_info = {}
            return {}
        self._domain_info = discovered
        if not discovered:
            self.unavailable_reason = (
                "No readable RAPL domains were found under {}. "
                "DoL8 requires Linux powercap RAPL support.".format(self.root)
            )
        else:
            self.unavailable_reason = ""
        return {name: str(info.energy_path) for name, info in discovered.items()}

    def available(self) -> bool:
        """Return whether at least one discovered energy counter can be read."""
        paths = self.domains()
        if not paths:
            return False
        try:
            self.read(next(iter(paths)))
        except RaplUnavailable as error:
            self.unavailable_reason = str(error)
            return False
        return True

    def read(self, domain: str) -> int:
        """Read one domain's cumulative energy in microjoules."""
        if domain not in self._domain_info:
            self.domains()
        info = self._domain_info.get(domain)
        if info is None:
            raise RaplUnavailable(
                self.unavailable_reason or "Unknown RAPL domain: {}".format(domain)
            )
        try:
            return int(info.energy_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as error:
            raise RaplUnavailable(self._error_message(error)) from error

    def read_all(self) -> Dict[str, int]:
        """Read all available domains, skipping domains removed during discovery."""
        if not self._domain_info:
            self.domains()
        readings: Dict[str, int] = {}
        for domain in self._domain_info:
            readings[domain] = self.read(domain)
        return readings

    def ranges(self) -> Dict[str, int]:
        """Read each domain's maximum counter range in microjoules."""
        if not self._domain_info:
            self.domains()
        values: Dict[str, int] = {}
        for domain, info in self._domain_info.items():
            try:
                values[domain] = int(info.range_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError) as error:
                raise RaplUnavailable(self._error_message(error)) from error
        return values

    @staticmethod
    def delta(
        start: Dict[str, int], end: Dict[str, int], ranges: Dict[str, int]
    ) -> Dict[str, float]:
        """Calculate per-domain energy deltas in joules, correcting one wrap."""
        result: Dict[str, float] = {}
        for domain in start.keys() & end.keys():
            before = start[domain]
            after = end[domain]
            max_range = ranges.get(domain)
            if before < 0 or after < 0:
                raise RaplUnavailable("RAPL counter readings must be non-negative for {}.".format(domain))
            if max_range is not None and (
                max_range <= 0 or before > max_range or after > max_range
            ):
                raise RaplUnavailable("RAPL counter reading is outside its range for {}.".format(domain))
            if after < before:
                if max_range is None:
                    raise RaplUnavailable(
                        "Counter wrapped for {} but its maximum range is unavailable.".format(domain)
                    )
                energy_uj = max_range - before + after
            else:
                energy_uj = after - before
            result[domain] = energy_uj / 1_000_000.0
        return result

    def measure(
        self, callback_or_sleep: Optional[Callable[[], object]] = None, seconds: float = 5.0
    ) -> Dict[str, object]:
        """Measure a callback or idle interval and return energy, duration, and watts."""
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        before = self.read_all()
        ranges = self.ranges()
        started = time.perf_counter()
        if callback_or_sleep is None:
            time.sleep(seconds)
        else:
            callback_or_sleep()
        elapsed = time.perf_counter() - started
        after = self.read_all()
        per_domain = self.delta(before, after, ranges)
        total_joules = total_energy_joules(per_domain)
        return {
            "per_domain_joules": per_domain,
            "total_joules": total_joules,
            "elapsed_seconds": elapsed,
            "average_watts": total_joules / elapsed if elapsed > 0 else 0.0,
        }

    @staticmethod
    def _error_message(error: BaseException) -> str:
        """Format sysfs errors with actionable permissions guidance."""
        message = "Unable to read RAPL powercap data: {}".format(error)
        if isinstance(error, PermissionError):
            message += "\nIf permissions are the problem, configure powercap access with:\n{}".format(
                UDEV_FIX
            )
        return message


def total_energy_joules(per_domain: Dict[str, float]) -> float:
    """Return a non-double-counted total, preferring top-level package counters."""
    package_values = [
        value for name, value in per_domain.items()
        if name == "PACKAGE" or name.startswith("PACKAGE_")
    ]
    return sum(package_values) if package_values else sum(per_domain.values())


if __name__ == "__main__":
    reader = RAPLReader()
    if not reader.available():
        raise SystemExit(reader.unavailable_reason)
    try:
        measurement = reader.measure(seconds=5.0)
    except RaplUnavailable as error:
        raise SystemExit(str(error))
    for domain, joules in measurement["per_domain_joules"].items():
        print("{}: {:.3f} J".format(domain, joules))
    print("Average: {:.3f} W".format(measurement["average_watts"]))
