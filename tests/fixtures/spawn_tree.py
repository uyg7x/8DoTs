"""Spawn a target, child, and detached grandchild for cleanup tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List


def record_pid(path: Path) -> None:
    """Append this process ID to the test's shared PID file."""
    with path.open("a", encoding="ascii") as output:
        output.write("{}\n".format(os.getpid()))
        output.flush()


def run_process(role: str, pid_path: Path) -> None:
    """Record this process and spawn the next fixture level when applicable."""
    record_pid(pid_path)
    if role == "root":
        subprocess.Popen([sys.executable, __file__, "child", str(pid_path)])
    elif role == "child":
        subprocess.Popen(
            [sys.executable, __file__, "grandchild", str(pid_path)],
            start_new_session=True,
        )
    while True:
        time.sleep(1.0)


def main(arguments: List[str]) -> None:
    """Start one level of the process tree and remain alive until terminated."""
    if len(arguments) == 1:
        run_process("root", Path(arguments[0]))
    elif len(arguments) == 2:
        run_process(arguments[0], Path(arguments[1]))
    else:
        raise SystemExit("usage: spawn_tree.py PID_FILE")


if __name__ == "__main__":
    main(sys.argv[1:])
