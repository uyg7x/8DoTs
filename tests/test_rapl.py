"""Hardware-free tests for the RAPL reader."""

from pathlib import Path

import pytest

from dol8.rapl import RAPLReader, RaplUnavailable, total_energy_joules


def add_domain(root: Path, relative: str, name: str, energy: int, limit: int) -> Path:
    """Create one fake powercap domain and return its energy counter path."""
    directory = root / relative
    directory.mkdir(parents=True)
    (directory / "name").write_text(name, encoding="ascii")
    counter = directory / "energy_uj"
    counter.write_text(str(energy), encoding="ascii")
    (directory / "max_energy_range_uj").write_text(str(limit), encoding="ascii")
    return counter


def test_dynamic_discovery_and_read_all(tmp_path: Path) -> None:
    """Discover package and child domains without hardcoding their paths."""
    add_domain(tmp_path, "intel-rapl0", "package-0", 120, 1000)
    add_domain(tmp_path, "intel-rapl0/intel-rapl0-child", "core", 45, 500)
    add_domain(tmp_path, "intel-rapl1", "package-1", 210, 1000)
    add_domain(tmp_path, "amd-rapl0", "package-0", 330, 2000)
    reader = RAPLReader(tmp_path)

    assert reader.available()
    assert set(reader.domains()) == {"PACKAGE", "PACKAGE_2", "CORE", "PACKAGE_3"}
    assert sorted(reader.read_all().values()) == [45, 120, 210, 330]


def test_delta_handles_normal_and_wrapped_counters() -> None:
    """Compute joules correctly both before and after a counter wrap."""
    deltas = RAPLReader.delta(
        {"PACKAGE": 900, "CORE": 100},
        {"PACKAGE": 50, "CORE": 250},
        {"PACKAGE": 1000, "CORE": 500},
    )

    assert deltas == {"PACKAGE": pytest.approx(0.00015), "CORE": pytest.approx(0.00015)}


def test_total_prefers_package_to_avoid_nested_domain_double_counting() -> None:
    """Treat package as the total while keeping child counters independently reportable."""
    assert total_energy_joules({"PACKAGE": 12.0, "CORE": 7.0, "DRAM": 2.0}) == 12.0
    assert total_energy_joules({"PACKAGE": 12.0, "PACKAGE_2": 10.0, "CORE": 7.0}) == 22.0
    assert total_energy_joules({"CORE": 7.0, "DRAM": 2.0}) == 9.0


def test_delta_rejects_negative_or_out_of_range_counter_values() -> None:
    """Reject corrupt readings rather than returning physically impossible energy."""
    with pytest.raises(RaplUnavailable, match="non-negative"):
        RAPLReader.delta({"PACKAGE": -1}, {"PACKAGE": 10}, {"PACKAGE": 100})
    with pytest.raises(RaplUnavailable, match="outside its range"):
        RAPLReader.delta({"PACKAGE": 101}, {"PACKAGE": 0}, {"PACKAGE": 100})


def test_read_permission_error_includes_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn a mid-session permission error into an actionable exception."""
    add_domain(tmp_path, "intel-rapl0", "package-0", 100, 1000)
    reader = RAPLReader(tmp_path)
    reader.domains()
    energy_path = Path(reader.domains()["PACKAGE"])
    original_read_text = Path.read_text

    def denied(path: Path, *args: object, **kwargs: object) -> str:
        if path == energy_path:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(RaplUnavailable, match="99-rapl\\.rules"):
        reader.read("PACKAGE")


def test_missing_root_is_unavailable(tmp_path: Path) -> None:
    """Report missing hardware without depending on the host machine."""
    reader = RAPLReader(tmp_path / "not-present")

    assert not reader.available()
    assert "No readable RAPL" in reader.unavailable_reason


def test_measure_uses_domain_range_for_wrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure an interval using dynamically discovered range metadata."""
    counter = add_domain(tmp_path, "intel-rapl0", "package-0", 950, 1000)
    reader = RAPLReader(tmp_path)
    monkeypatch.setattr("dol8.rapl.time.sleep", lambda _seconds: counter.write_text("25"))

    result = reader.measure(seconds=0.01)

    assert result["per_domain_joules"] == {"PACKAGE": pytest.approx(0.000075)}
    assert result["total_joules"] == pytest.approx(0.000075)
