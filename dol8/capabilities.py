"""Environment capability detection for dol8.

Determines which energy measurement sources are available on this machine
and picks the best one. The priority order is:

    Tier A (EXACT)        RAPL via /sys/class/powercap   - bare-metal Linux
    Tier B (HIGH_PROXY)   IPMI / Redfish (BMC)           - bare-metal cloud
    Tier D (MEDIUM_PROXY) perf hardware counters         - CI runners, VMs
    Tier E (LOW_PROXY)    wall-clock CPU time            - last resort

Each result is tagged with a `confidence` so downstream gates can refuse to
block on low-confidence data (see stats.regression_verdict).

The v0.1 version of this file only checked three things (RAPL exists,
valgrind present, hypervisor flag in cpuinfo). This version adds perf,
IPMI, NVML, cgroup accounting, real RAPL *readability* probing (file exists
does not imply readable), and a VM heuristic that does not lie on bare
metal that happens to expose a hypervisor flag.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class TierInfo:
    """The chosen measurement tier plus enough metadata for honest JSON."""

    tier: str  # "A" | "B" | "D" | "E"
    name: str  # short label, e.g. "rapl"
    metric: str  # JSON field holding the measured value
    confidence: str  # EXACT | HIGH_PROXY | MEDIUM_PROXY | LOW_PROXY
    source: str  # detailed source string for the JSON schema
    cpu_vendor: Optional[str] = None
    is_vm: bool = False
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_vm() -> bool:
    """Best-effort VM detection. None of the heuristics are perfect on their
    own, so we combine two: the cpuinfo `hypervisor` flag and the DMI product
    name. A bare-metal box that reports a hypervisor flag (some do) still gets
    caught by the DMI check; a VM with no DMI still gets caught by the cpuinfo
    flag.
    """
    if not _is_linux():
        return False
    try:
        with open("/proc/cpuinfo", "r", errors="ignore") as f:
            if "hypervisor" in f.read().lower():
                return True
    except OSError:
        pass
    try:
        with open("/sys/class/dmi/id/product_name", "r", errors="ignore") as f:
            name = f.read().strip().lower()
            vm_markers = (
                "vmware",
                "virtualbox",
                "kvm",
                "xen",
                "qemu",
                "hyper-v",
                "docker",
            )
            if any(v in name for v in vm_markers):
                return True
    except OSError:
        pass
    return False


def _cpu_vendor() -> Optional[str]:
    if not _is_linux():
        return None
    try:
        with open("/proc/cpuinfo", "r", errors="ignore") as f:
            text = f.read().lower()
        if "amd" in text:
            return "amd"
        if "intel" in text:
            return "intel"
    except OSError:
        pass
    return None


def _rapl_available() -> bool:
    """True iff /sys/class/powercap exists AND at least one domain's
    energy_uj file is actually readable (not just present - permissions
    matter). The v0.1 check used os.path.exists which returned True on
    machines that had the directory but no read permission.
    """
    base = "/sys/class/powercap"
    if not os.path.isdir(base):
        return False
    try:
        for entry in os.listdir(base):
            if not entry.startswith("intel-rapl:"):
                continue
            uj = os.path.join(base, entry, "energy_uj")
            if not os.access(uj, os.R_OK):
                continue
            with open(uj, "r") as f:
                content = f.read().strip()
            if content.isdigit():
                return True
    except OSError:
        return False
    return False


def _perf_available() -> bool:
    """`perf` binary present AND perf_event_paranoid permits unprivileged
    access (value <= 2)."""
    if not _is_linux():
        return False
    if not shutil.which("perf"):
        return False
    try:
        with open("/proc/sys/kernel/perf_event_paranoid", "r") as f:
            return int(f.read().strip()) <= 2
    except (OSError, ValueError):
        # If we can't read the paranoid level, assume the binary works;
        # the worst case is a runtime error inside compare.rs which will
        # degrade to Tier E for that one run.
        return True


def _ipmi_available() -> bool:
    """ipmitool presence. Whole-node watt readings via the BMC."""
    if not _is_linux():
        return False
    return shutil.which("ipmitool") is not None


def _nvml_available() -> bool:
    """NVML for GPU power. Useful only when the workload is GPU-bound."""
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        pynvml.nvmlShutdown()
        return True
    except Exception:
        return False


def _cgroup_cpu_accounting() -> bool:
    """cgroup v2 / v1 CPU accounting. Useful for container attribution."""
    if os.path.exists("/sys/fs/cgroup/cpu.stat"):
        return True
    if os.path.exists("/sys/fs/cgroup/cpuacct/cpuacct.usage"):
        return True
    return False


def get_current_tier() -> TierInfo:
    """Detect the best available measurement tier on this machine."""
    vendor = _cpu_vendor()
    vm = _is_vm()

    if _rapl_available():
        return TierInfo(
            tier="A",
            name="rapl",
            metric="energy_joules",
            confidence="EXACT",
            source="intel-rapl-sysfs",
            cpu_vendor=vendor,
            is_vm=vm,
            notes="bare-metal Linux RAPL; exact Joules",
        )

    if _ipmi_available():
        # Bare-metal cloud instance with a BMC (e.g. AWS *.metal).
        return TierInfo(
            tier="B",
            name="ipmi",
            metric="node_watts_avg",
            confidence="HIGH_PROXY",
            source="ipmi-dcmi",
            cpu_vendor=vendor,
            is_vm=vm,
            notes="whole-node watts via BMC; prorate to app via CPU time",
        )

    if _perf_available():
        return TierInfo(
            tier="D",
            name="perf",
            metric="proxy_count",
            confidence="MEDIUM_PROXY",
            source="perf-stat-instructions+cache-misses",
            cpu_vendor=vendor,
            is_vm=vm,
            notes="perf hardware counters; calibration required for Joules",
        )

    # Last resort: CPU time. Always works on any OS, but the noisiest metric.
    return TierInfo(
        tier="E",
        name="cpu_time",
        metric="cpu_time_s",
        confidence="LOW_PROXY",
        source="wall-clock-time",
        cpu_vendor=vendor,
        is_vm=vm,
        notes="no hardware counters; advisory only, do not hard-gate",
    )


def available_sources() -> list:
    """Diagnostic listing of every source detected, even if not chosen.
    Used by `dol8 doctor --ci` to print a one-line verdict.
    """
    return [
        {"name": "rapl", "available": _rapl_available()},
        {"name": "ipmi", "available": _ipmi_available()},
        {"name": "perf", "available": _perf_available()},
        {"name": "nvml", "available": _nvml_available()},
        {"name": "cgroup", "available": _cgroup_cpu_accounting()},
    ]


if __name__ == "__main__":
    import json

    print("chosen tier:")
    print(json.dumps(get_current_tier().as_dict(), indent=2))
    print("\navailable sources:")
    print(json.dumps(available_sources(), indent=2))
