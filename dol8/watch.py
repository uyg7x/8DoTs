"""Observe machine energy and report CPU-time-prorated attribution."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .poller import PollingThread
from .rapl import RAPLReader
from .thermal import ThermalMonitor, psutil


def cpu_time_share(app_delta: float, total_delta: float) -> float:
    """Return a bounded CPU-time attribution fraction."""
    if total_delta <= 0:
        return 0.0
    return max(0.0, min(1.0, app_delta / total_delta))


def estimate_app_energy(machine_joules: float, app_delta: float, total_delta: float) -> Dict[str, float]:
    """Estimate application energy using CPU-time share, explicitly as a fraction."""
    share = cpu_time_share(app_delta, total_delta)
    return {"app_share_pct": share * 100.0, "app_energy_estimated_joules": machine_joules * share}


def write_checkpoint(path: str, data: Dict[str, Any]) -> None:
    """Atomically replace a JSON checkpoint file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(str(temporary), str(destination))


def read_checkpoint(path: str) -> Dict[str, Any]:
    """Read a saved watch checkpoint."""
    with open(path, "r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("Watch checkpoint must contain a JSON object")
    return value


def run_watch(
    reader: RAPLReader,
    thermal: ThermalMonitor,
    attach: Optional[str] = None,
    out_path: str = "dol8_session.json",
    interval_s: float = 1.0,
    checkpoint_s: float = 30.0,
    duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Observe a process until it exits, duration elapses, or Ctrl+C is pressed."""
    if psutil is None:
        raise RuntimeError("Watch mode requires psutil")
    previous_session = None
    if Path(out_path).exists():
        previous_session = read_checkpoint(out_path)
        if not previous_session.get("complete", False):
            answer = input("Resume interrupted session? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                previous_session["resumed_from_checkpoint"] = True
                previous_session["interrupted"] = True
                return previous_session
    process_baseline = _process_cpu_snapshot() if attach is None else {}
    process = _find_process(attach)
    target_name = process.name() if process is not None else "machine"
    previous_app = _process_cpu_seconds(process) if process is not None else 0.0
    previous_total = _total_cpu_seconds()
    app_cpu = 0.0
    total_cpu = 0.0
    started = time.perf_counter()
    last_checkpoint = started
    poller = PollingThread(reader, thermal, interval_s=min(0.1, interval_s))
    poller.start()
    interrupted = False
    try:
        while True:
            time.sleep(interval_s)
            now = time.perf_counter()
            if process is not None:
                current_app = _process_cpu_seconds(process)
                app_cpu += max(0.0, current_app - previous_app)
                previous_app = current_app
                if not process.is_running():
                    break
            else:
                process, previous_app = _most_active_process(process_baseline)
                if process is not None:
                    target_name = process.name()
                    current_app = _process_cpu_seconds(process)
                    app_cpu += max(0.0, current_app - previous_app)
                    process_baseline = {}
            current_total = _total_cpu_seconds()
            total_cpu += max(0.0, current_total - previous_total)
            previous_total = current_total
            if duration_s is not None and now - started >= duration_s:
                break
            if now - last_checkpoint >= checkpoint_s:
                write_checkpoint(out_path, _watch_result(
                    target_name, now - started, poller, app_cpu, total_cpu, interrupted=False
                ))
                last_checkpoint = now
    except KeyboardInterrupt:
        interrupted = True
    finally:
        poller.stop()
    result = _watch_result(target_name, time.perf_counter() - started, poller, app_cpu, total_cpu, interrupted)
    result["complete"] = True
    if previous_session and previous_session.get("target") == target_name:
        previous_energy = float(previous_session.get("machine_energy_joules", 0.0))
        current_energy = float(result["machine_energy_joules"])
        result["previous_session_energy_joules"] = previous_energy
        result["change_from_previous_pct"] = (
            (current_energy - previous_energy) / previous_energy * 100.0 if previous_energy else 0.0
        )
    write_checkpoint(out_path, result)
    return result


def _watch_result(
    target: str,
    duration: float,
    poller: PollingThread,
    app_cpu: float,
    total_cpu: float,
    interrupted: bool,
) -> Dict[str, Any]:
    """Assemble a checkpoint/report with explicit estimated attribution."""
    machine_energy = poller.samples[-1]["joules_cum"] if poller.samples else 0.0
    thermal_stats = poller.thermal.stats()
    attribution = estimate_app_energy(machine_energy, app_cpu, total_cpu)
    return {
        "target": target,
        "duration_seconds": duration,
        "machine_energy_joules": machine_energy,
        "machine_energy_label": "high-resolution estimate",
        "app_share_pct": attribution["app_share_pct"],
        "app_energy_estimated_joules": attribution["app_energy_estimated_joules"],
        "attribution_label": "ESTIMATED (CPU-time prorating)",
        "honesty_note": "Machine energy is measured; per-process energy is estimated from CPU time.",
        "temperature": thermal_stats,
        "throttle_events": poller.throttle_events,
        "timeline": poller.timeline(),
        "interrupted": interrupted,
    }


def _find_process(attach: Optional[str]) -> Any:
    """Resolve an explicit PID/name or choose no process for automatic mode."""
    if attach is None:
        return None
    try:
        if attach.isdigit():
            return psutil.Process(int(attach))
        matches = [process for process in psutil.process_iter(["name"]) if process.info.get("name") == attach]
        if not matches:
            raise RuntimeError("No process found named {}".format(attach))
        return matches[0]
    except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
        raise RuntimeError("Unable to attach to process: {}".format(error)) from error


def _process_cpu_snapshot() -> Dict[int, float]:
    """Capture cumulative CPU time for processes present at watch startup."""
    snapshot: Dict[int, float] = {}
    for process in psutil.process_iter(["cpu_times"]):
        try:
            cpu = process.info.get("cpu_times")
            snapshot[process.pid] = float(cpu.user + cpu.system) if cpu else 0.0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return snapshot


def _most_active_process(baseline: Dict[int, float]) -> Tuple[Any, float]:
    """Choose the process with the largest CPU-time increase since watch startup."""
    best = None
    best_delta = 0.0
    best_previous = 0.0
    for process in psutil.process_iter(["cpu_times"]):
        try:
            cpu = process.info.get("cpu_times")
            amount = float(cpu.user + cpu.system) if cpu else 0.0
            previous = baseline.get(process.pid, 0.0)
            delta = amount - previous
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if delta > best_delta:
            best, best_delta, best_previous = process, delta, previous
    return best, best_previous


def _process_cpu_seconds(process: Any) -> float:
    """Read a process's cumulative user and system CPU seconds."""
    try:
        times = process.cpu_times()
        return float(times.user + times.system)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _total_cpu_seconds() -> float:
    """Read cumulative machine CPU time across all logical CPUs."""
    try:
        return float(sum(psutil.cpu_times()))
    except (AttributeError, OSError, RuntimeError):
        return 0.0
