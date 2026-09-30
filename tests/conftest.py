"""Shared fake hardware fixtures for the test suite."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_sysfs(tmp_path: Path):
    """Create a writable fake package counter and return its path/helper."""
    root = tmp_path / "powercap"
    domain = root / "intel-rapl0"
    domain.mkdir(parents=True)
    (domain / "name").write_text("package-0", encoding="ascii")
    energy_path = domain / "energy_uj"
    energy_path.write_text("0", encoding="ascii")
    (domain / "max_energy_range_uj").write_text("100000000", encoding="ascii")

    def advance(value: int) -> None:
        energy_path.write_text(str(value), encoding="ascii")

    return root, energy_path, advance


@pytest.fixture
def fake_psutil():
    """Return a tiny psutil-shaped object for sensor and battery tests."""
    return SimpleNamespace(
        sensors_temperatures=lambda: {"coretemp": [SimpleNamespace(current=45.0)]},
        sensors_battery=lambda: None,
        cpu_freq=lambda: None,
    )
