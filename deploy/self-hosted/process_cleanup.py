"""Stop only repository-owned process trees, never whoever now owns a port."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import time


def identity(pid: int) -> tuple[int, str] | None:
    try:
        # comm may contain spaces or parentheses; fields after its final ')' are stable.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return int(fields[1]), fields[19]  # ppid, starttime
    except (OSError, ValueError, IndexError):
        return None


def owned(pid: int, root: Path) -> bool:
    try:
        return Path(f"/proc/{pid}/cwd").resolve(strict=True).is_relative_to(root)
    except OSError:
        return False


def stop_trees(root: Path, pids: list[int], grace: float = 5.0,
               expected_starts: dict[int, str] | None = None) -> None:
    root = root.resolve()
    snapshot = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and (stamp := identity(int(entry.name))):
            snapshot[int(entry.name)] = stamp
    expected_starts = expected_starts or {}
    selected = {pid for pid in pids if pid in snapshot and owned(pid, root)
                and (pid not in expected_starts or
                     snapshot[pid][1] == expected_starts[pid])}
    # Snapshot descendants before stopping their parents (which reparents children).
    while True:
        children = {pid for pid, (parent, _) in snapshot.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    selected.discard(os.getpid())

    def alive(pid: int) -> bool:
        current = identity(pid)
        return current is not None and current[1] == snapshot[pid][1]

    def send(pid: int, sig: int) -> None:
        if alive(pid):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    for pid in selected:
        send(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while any(alive(pid) for pid in selected) and time.monotonic() < deadline:
        time.sleep(0.1)
    for pid in selected:
        send(pid, signal.SIGKILL)


if __name__ == "__main__":
    # SIGINT can originate from an IDE/runner/process group as well as a keyboard.
    # A cleanup helper must complete its bounded teardown under repeated signals.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if sys.argv[1] == "--identity":
        pid = int(sys.argv[2])
        if stamp := identity(pid):
            print(f"{pid}:{stamp[1]}")
    else:
        pids, starts = [], {}
        for value in sys.argv[2:]:
            pid, separator, start = value.partition(":")
            if pid.isdigit() and (not separator or start.isdigit()):
                pids.append(int(pid))
                if separator:
                    starts[int(pid)] = start
        stop_trees(Path(sys.argv[1]), pids, expected_starts=starts)
