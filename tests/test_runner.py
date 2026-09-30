"""Hardware-free checks for process result handling."""

import sys
import time
import io
from types import SimpleNamespace
from pathlib import Path

import pytest

import dol8.runner as runner_module
import dol8.cli as cli_module
from dol8.rapl import RAPLReader
from dol8.runner import PowerProfiler, TargetRunner, map_exit


def test_runner_captures_and_streams_target_output(capsys) -> None:
    """Capture a short target's output and successful exit status."""
    result = TargetRunner().run([sys.executable, "-c", "print('dol8-runner-ok')"], timeout_s=5)

    assert result.returncode == 0
    assert result.stdout.strip() == "dol8-runner-ok"
    assert "dol8-runner-ok" in capsys.readouterr().out
    assert not result.killed_by_timeout
    assert map_exit(result) == 0


def test_exit_code_mapping() -> None:
    """Map crash, timeout, and interruption to documented status codes."""
    from dol8.runner import RunResult

    assert map_exit(RunResult(4, "", "", 0.1)) == 2
    assert map_exit(RunResult(-9, "", "", 0.1, True)) == 2
    assert map_exit(RunResult(130, "", "", 0.1)) == 130
    assert map_exit(None, interrupted=True) == 130


def test_runner_terminates_target_on_timeout() -> None:
    """Kill a sleeping target promptly when its deadline expires."""
    result = TargetRunner().run(
        [sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=0.05
    )

    assert result.killed_by_timeout
    assert map_exit(result) == 2


def test_poller_error_cannot_make_peak_power_less_than_average(monkeypatch) -> None:
    """Retain the measured average as a lower bound when polling fails."""
    class FakeReader:
        value = 0

        def available(self):
            return True

        def ranges(self):
            return {"PACKAGE": 10_000_000}

        def read_all(self):
            self.value += 3_000_000
            return {"PACKAGE": self.value}

        delta = staticmethod(RAPLReader.delta)

    class FakeThermal:
        def sample(self):
            return None

        def stats(self):
            return {"baseline": None, "peak": None, "avg": None, "end": None, "rise": None}

        def battery(self):
            return None

    class FailedPoller:
        def __init__(self, _reader, _thermal):
            self.peak_watts = 0.1
            self.error = "transient read failure"
            self.samples = []
            self.throttle_events = []
            self.gpu_available = False

        def start(self):
            pass

        def stop(self):
            pass

        def timeline(self):
            return []

    monkeypatch.setattr(runner_module, "PollingThread", FailedPoller)
    profiler = PowerProfiler(reader=FakeReader(), thermal=FakeThermal())
    with profiler:
        pass

    assert profiler.peak_watts >= profiler.avg_watts


def test_interrupt_stops_poller_before_final_counter_read(monkeypatch) -> None:
    """Capture interrupted energy only after polling has stopped."""
    events = []

    class FakeReader:
        value = 0

        def available(self):
            return True

        def ranges(self):
            return {"PACKAGE": 10_000_000}

        def read_all(self):
            events.append("final_read" if "poller_stop" in events else "start_read")
            self.value += 3_000_000
            return {"PACKAGE": self.value}

        delta = staticmethod(RAPLReader.delta)

    class FakeThermal:
        def sample(self):
            return None

        def stats(self):
            return {"baseline": None, "peak": None, "avg": None, "end": None, "rise": None}

        def battery(self):
            return None

    class FakePoller:
        def __init__(self, _reader, _thermal):
            self.peak_watts = 0.0
            self.error = None
            self.samples = []
            self.throttle_events = []
            self.gpu_available = False
            self.frequency_available = False

        def start(self):
            events.append("poller_start")

        def stop(self):
            events.append("poller_stop")

        def timeline(self):
            return []

    class InterruptingRunner:
        def run(self, command, timeout_s):
            raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "PollingThread", FakePoller)
    monkeypatch.setattr(cli_module, "ThermalMonitor", FakeThermal)
    monkeypatch.setattr(cli_module, "TargetRunner", InterruptingRunner)

    result, run_result = cli_module._measure_target("target.py", FakeReader(), 5.0)

    assert run_result.returncode == 130
    assert result["energy_joules"] == 3.0
    assert events.index("poller_stop") < events.index("final_read")


def test_timeout_kills_target_child_and_detached_grandchild(tmp_path: Path) -> None:
    """Terminate the target tree even when its grandchild starts a new session."""
    psutil = pytest.importorskip("psutil")
    pid_file = tmp_path / "process-ids.txt"
    fixture = Path(__file__).parent / "fixtures" / "spawn_tree.py"

    result = TargetRunner().run([sys.executable, str(fixture), str(pid_file)], timeout_s=1.0)

    assert result.killed_by_timeout
    assert result.survivors == []
    pids = [int(value) for value in pid_file.read_text(encoding="ascii").splitlines()]
    assert len(pids) == 3
    deadline = time.perf_counter() + 1.0
    while any(psutil.pid_exists(pid) for pid in pids) and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert not any(psutil.pid_exists(pid) for pid in pids)


def test_kill_escalation_snapshots_before_signals_and_reports_survivors(monkeypatch) -> None:
    """Enforce snapshot, TERM, KILL, sweep order and retain survivor IDs."""
    events = []
    runner = TargetRunner()

    class FakeProcess:
        pid = 9001
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("target-kill")
            self.returncode = -9

        def wait(self, timeout=None):
            self.returncode = -9
            return self.returncode

    monkeypatch.setattr(runner_module, "_uses_posix_process_groups", lambda: True)
    monkeypatch.setattr(runner_module.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(runner, "_snapshot_descendants", lambda _pid: events.append("snapshot") or [9010, 9020])
    monkeypatch.setattr(runner, "_snapshot_adopted_children", lambda *_args: [])
    monkeypatch.setattr(runner, "_signal_group", lambda _pid, sig: events.append("term" if sig == runner_module.signal.SIGTERM else "kill-group"))
    monkeypatch.setattr(runner, "_group_exists", lambda _pid: events.append("group-check") or True)
    ticks = iter([0.0, 3.0, 3.0])
    monkeypatch.setattr(runner_module.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(runner, "_kill_pid", lambda pid: events.append("pid-{}".format(pid)))
    monkeypatch.setattr(runner, "_reap_adopted_children", lambda _pids: None)
    monkeypatch.setattr(runner, "_find_survivors", lambda _pids: [9020])

    survivors = runner._kill_tree(FakeProcess(), set())

    assert survivors == [9020]
    assert events.index("snapshot") < events.index("term") < events.index("kill-group")
    assert events.index("kill-group") < events.index("pid-9010")
    assert events.index("kill-group") < events.index("pid-9020")


def test_subreaper_is_linux_only_and_failure_is_nonfatal(monkeypatch) -> None:
    """Attempt prctl only on Linux and degrade when libc rejects it."""
    calls = []
    fake_libc = SimpleNamespace(prctl=lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(runner_module.sys, "platform", "win32")
    monkeypatch.setattr(runner_module.ctypes, "CDLL", lambda *_args, **_kwargs: fake_libc)
    assert not runner_module._enable_child_subreaper()
    assert calls == []

    monkeypatch.setattr(runner_module.sys, "platform", "linux")
    assert runner_module._enable_child_subreaper()
    assert calls == [(36, 1, 0, 0, 0)]
    fake_libc.prctl = lambda *_args: -1
    assert not runner_module._enable_child_subreaper()


def test_runner_streams_both_pipes_and_unbuffers_python_targets(monkeypatch, capsys) -> None:
    """Prefix both output streams, join readers, and inject only the Python env setting."""
    instances = []
    threads = []
    real_thread = runner_module.threading.Thread

    class FakeProcess:
        pid = 987654
        returncode = 0
        stdout = io.StringIO("out-line\n")
        stderr = io.StringIO("err-line\n")

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        instances.append((command, kwargs))
        return FakeProcess()

    def recording_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(runner_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_module.threading, "Thread", recording_thread)
    monkeypatch.setenv("DO_L8_TEST_ENV", "preserved")
    monkeypatch.setattr(TargetRunner, "_snapshot_runner_children", staticmethod(lambda: set()))
    monkeypatch.setattr(TargetRunner, "_kill_tree", lambda self, process, children: [])
    runner = TargetRunner()

    python_result = runner.run([sys.executable, "target.py"])
    other_result = runner.run(["native-tool", "--version"])
    captured = capsys.readouterr()

    assert python_result.stdout == "out-line\n"
    assert python_result.stderr == "err-line\n"
    assert "[target-out] out-line" in captured.out
    assert "[target-err] err-line" in captured.err
    assert all(not thread.is_alive() for thread in threads)
    python_env = instances[0][1]["env"]
    assert python_env["PYTHONUNBUFFERED"] == "1"
    assert python_env["DO_L8_TEST_ENV"] == "preserved"
    assert instances[1][1]["env"] is None
    assert other_result.returncode == 0
