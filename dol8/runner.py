"""Target process execution and context-managed energy profiling."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import ctypes
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, TextIO

from .poller import PollingThread
from .rapl import RAPLReader, RaplUnavailable, total_energy_joules
from .report import render_terminal
from .thermal import ThermalMonitor, psutil

_SUBREAPER_WARNING_LOGGED = False


@dataclass
class RunResult:
    """Captured target process result."""

    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    killed_by_timeout: bool = False
    survivors: List[int] = field(default_factory=list)


class TargetRunner:
    """Run a target sequentially and stream its standard output."""

    def run(self, cmd: Sequence[str], timeout_s: float = 600.0) -> RunResult:
        """Execute one command, killing its process group on timeout."""
        if not cmd:
            raise ValueError("cmd must contain an executable")
        _enable_child_subreaper()
        baseline_children = self._snapshot_runner_children()
        started = time.perf_counter()
        process = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=(os.name == "posix"),
            env=self._environment_for(cmd),
        )
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        stdout_thread = threading.Thread(
            target=self._capture_stdout, args=(process.stdout, stdout_lines), daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._capture_stderr, args=(process.stderr, stderr_lines), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        killed = False
        interrupted = False
        survivors: List[int] = []
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            killed = True
            survivors = self._kill_tree(process, baseline_children)
            process.wait()
        except KeyboardInterrupt:
            interrupted = True
            survivors = self._kill_tree(process, baseline_children)
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
        finally:
            stdout_thread.join()
            stderr_thread.join()
        if not killed:
            survivors = self._kill_tree(process, baseline_children)
        return RunResult(
            returncode=130 if interrupted else int(process.returncode or 0),
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            duration_s=time.perf_counter() - started,
            killed_by_timeout=killed,
            survivors=survivors,
        )

    @staticmethod
    def _capture_stdout(stream: Optional[TextIO], output: List[str]) -> None:
        """Print prefixed stdout lines live while retaining raw target output."""
        if stream is None:
            return
        for line in stream:
            output.append(line)
            print("[target-out] {}".format(line.rstrip("\r\n")), flush=True)

    @staticmethod
    def _capture_stderr(stream: Optional[TextIO], output: List[str]) -> None:
        """Print prefixed stderr lines live and retain raw output concurrently."""
        if stream is not None:
            for line in stream:
                output.append(line)
                print("[target-err] {}".format(line.rstrip("\r\n")), file=sys.stderr, flush=True)

    @staticmethod
    def _environment_for(cmd: Sequence[str]) -> Optional[dict[str, str]]:
        """Merge an unbuffered-output setting for Python script targets only."""
        if len(cmd) > 1 and cmd[1].lower().endswith(".py"):
            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            return environment
        return None

    @staticmethod
    def _snapshot_runner_children() -> set[int]:
        """Record pre-existing runner children so cleanup cannot kill unrelated work."""
        if psutil is None:
            return set()
        try:
            return {child.pid for child in psutil.Process(os.getpid()).children(recursive=True)}
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return set()

    def _kill_tree(self, process: subprocess.Popen, baseline_children: set[int]) -> List[int]:
        """Terminate a target tree, escalate, sweep descendants, and report survivors."""
        descendants = self._snapshot_descendants(process.pid)
        adopted = self._snapshot_adopted_children(process.pid, baseline_children)
        snapshot = set(descendants) | set(adopted)
        if _uses_posix_process_groups():
            if process.poll() is None:
                self._signal_group(process.pid, signal.SIGTERM)
                deadline = time.perf_counter() + 2.0
                while self._group_exists(process.pid) and time.perf_counter() < deadline:
                    process.poll()
                    time.sleep(0.05)
                if self._group_exists(process.pid):
                    self._signal_group(process.pid, int(getattr(signal, "SIGKILL", 9)))
        else:
            if process.poll() is None:
                process.terminate()
            for pid in snapshot:
                self._kill_pid(pid)
            if process.poll() is None:
                process.kill()
        for pid in snapshot:
            self._kill_pid(pid)
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        self._reap_adopted_children(snapshot)
        snapshot.update(self._snapshot_adopted_children(process.pid, baseline_children))
        for pid in snapshot:
            self._kill_pid(pid)
        return self._find_survivors(snapshot)

    @staticmethod
    def _snapshot_descendants(pid: int) -> List[int]:
        """Snapshot all visible descendants before sending any termination signal."""
        if psutil is None:
            return []
        try:
            return [child.pid for child in psutil.Process(pid).children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return []

    @staticmethod
    def _snapshot_adopted_children(pid: int, baseline_children: set[int]) -> List[int]:
        """Find new runner children, including descendants adopted by the subreaper."""
        if psutil is None:
            return []
        try:
            return [
                child.pid
                for child in psutil.Process(os.getpid()).children(recursive=True)
                if child.pid != pid and child.pid not in baseline_children
            ]
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return []

    @staticmethod
    def _signal_group(pgid: int, sig: int) -> None:
        """Send a signal to a target's isolated POSIX process group."""
        killpg = getattr(os, "killpg", None)
        if killpg is None:
            return
        try:
            killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _group_exists(pgid: int) -> bool:
        """Return whether a POSIX process group still has members."""
        killpg = getattr(os, "killpg", None)
        if killpg is None:
            return False
        try:
            killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _kill_pid(pid: int) -> None:
        """Hard-kill one snapshotted descendant on the current platform."""
        try:
            if psutil is not None:
                psutil.Process(pid).kill()
            else:
                kill_process = getattr(os, "kill", None)
                if kill_process is not None:
                    kill_process(pid, int(getattr(signal, "SIGKILL", 9)))
        except Exception as error:
            if isinstance(error, OSError) or (psutil is not None and isinstance(error, psutil.Error)):
                return
            raise

    @staticmethod
    def _find_survivors(pids: set[int]) -> List[int]:
        """Return snapshotted PIDs that are still running after cleanup."""
        survivors = []
        for pid in sorted(pids):
            if psutil is None:
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, PermissionError, OSError):
                    continue
                survivors.append(pid)
                continue
            try:
                child = psutil.Process(pid)
                if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                    survivors.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return survivors

    @staticmethod
    def _reap_adopted_children(pids: set[int]) -> None:
        """Reap terminated Linux descendants adopted by the subreaper."""
        if sys.platform != "linux":
            return
        pending = set(pids)
        deadline = time.perf_counter() + 0.5
        while pending and time.perf_counter() < deadline:
            for pid in tuple(pending):
                try:
                    waited_pid, _ = os.waitpid(pid, os.WNOHANG)
                    if waited_pid == pid:
                        pending.discard(pid)
                except (ChildProcessError, ProcessLookupError):
                    pending.discard(pid)
                except OSError:
                    pending.discard(pid)
            if pending:
                time.sleep(0.01)


def _uses_posix_process_groups() -> bool:
    """Return whether isolated POSIX process groups are available."""
    return os.name == "posix"


def _enable_child_subreaper() -> bool:
    """Enable Linux child-subreaper behavior when libc supports prctl."""
    global _SUBREAPER_WARNING_LOGGED
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(36, 1, 0, 0, 0)
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        return True
    except Exception as error:
        if not _SUBREAPER_WARNING_LOGGED:
            logging.getLogger(__name__).warning("Could not enable child subreaper: %s", error)
            _SUBREAPER_WARNING_LOGGED = True
        return False


def map_exit(run_result: Optional[RunResult], interrupted: bool = False) -> int:
    """Map a run outcome to DoL8's documented CLI exit status."""
    if interrupted or (run_result is not None and run_result.returncode == 130):
        return 130
    if run_result is None:
        return 1
    return 0 if run_result.returncode == 0 and not run_result.killed_by_timeout else 2


class PowerProfiler:
    """Measure energy and thermal data around a Python context block."""

    def __init__(self, reader: Optional[RAPLReader] = None, thermal: Optional[ThermalMonitor] = None) -> None:
        """Create a profiler with injectable hardware readers for tests."""
        self.reader = reader or RAPLReader()
        self.thermal = thermal or ThermalMonitor()
        self.energy_joules = 0.0
        self.avg_watts = 0.0
        self.peak_watts = 0.0
        self.peak_temp: Optional[float] = None
        self.duration_s = 0.0
        self.per_domain = {}
        self.timeline = []
        self.throttle_events = []
        self.gpu_available = False
        self.gpu = None
        self._start_readings = {}
        self._ranges = {}
        self._started = 0.0
        self._poller: Optional[PollingThread] = None
        self._thermal_start: Optional[float] = None

    def __enter__(self) -> "PowerProfiler":
        """Verify RAPL availability and start background sampling."""
        if not self.reader.available():
            raise RaplUnavailable(self.reader.unavailable_reason)
        self._ranges = self.reader.ranges()
        self._start_readings = self.reader.read_all()
        self._thermal_start = self.thermal.sample()
        self._started = time.perf_counter()
        self._poller = PollingThread(self.reader, self.thermal)
        self._poller.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        """Stop sampling, read final counters, and preserve exceptions."""
        if self._poller is not None:
            self._poller.stop()
            self.peak_watts = self._poller.peak_watts
        final = self.reader.read_all()
        self.duration_s = max(0.0, time.perf_counter() - self._started)
        self.per_domain = self.reader.delta(self._start_readings, final, self._ranges)
        self.energy_joules = total_energy_joules(self.per_domain)
        self.avg_watts = self.energy_joules / self.duration_s if self.duration_s > 0 else 0.0
        self.peak_watts = max(self.peak_watts, self.avg_watts)
        if self._poller is not None:
            self.timeline = self._poller.timeline()
            self.throttle_events = list(self._poller.throttle_events)
            self.gpu_available = self._poller.gpu_available
            gpu_readings = [sample for sample in self._poller.samples if sample.get("gpu_watts") is not None]
            if gpu_readings:
                self.gpu = {
                    "watts": sum(float(sample["gpu_watts"]) for sample in gpu_readings) / len(gpu_readings),
                    "temperature_c": sum(float(sample["gpu_temp_c"]) for sample in gpu_readings) / len(gpu_readings),
                }
        thermal_stats = self.thermal.stats()
        self.peak_temp = thermal_stats["peak"]
        return False

    def report(self) -> str:
        """Render a report for the most recent context measurement."""
        thermal_stats = self.thermal.stats()
        result = {
            "target": "Python context",
            "duration_seconds": self.duration_s,
            "energy_joules": self.energy_joules,
            "avg_watts": self.avg_watts,
            "peak_watts": self.peak_watts,
            "per_domain": self.per_domain,
            "gpu_available": self.gpu_available,
            "gpu": self.gpu,
            "battery": self.thermal.battery(),
            "thermal": {
                "baseline_c": self._thermal_start,
                "peak_c": thermal_stats["peak"],
                "avg_c": thermal_stats["avg"],
                "end_c": thermal_stats["end"],
                "rise_c": thermal_stats["rise"],
            },
        }
        return render_terminal(result)
