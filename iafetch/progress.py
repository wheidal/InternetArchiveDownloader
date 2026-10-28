"""What a running `iafetch get` is doing, in a status file (~/.local/share/iafetch/status.json) that
`iafetch status` reads from another terminal (F12). Rewritten at most once a second while files download.

It carries the run's process id, so `status` can tell a run that's going from one that was killed, and `get`
refuses to start a second run alongside the first.
"""

import json
import os
import time
from pathlib import Path

from .queue import data_dir


def status_path() -> Path:
    return data_dir() / "status.json"


def log_path() -> Path:
    return data_dir() / "get.log"


def read_status(path: Path | None = None) -> dict | None:
    try:
        return json.loads((path or status_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running(status: dict | None) -> bool:
    """True if the run that wrote `status` is still going."""
    if not status or status.get("state") in ("finished", "stopped"):
        return False
    try:
        pid = int(status["pid"])
        os.kill(pid, 0)
    except (KeyError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    recorded, now = status.get("pid_start"), process_start(pid)
    return recorded is None or now is None or recorded == now  # else the id was reused after a reboot


def process_start(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


class Progress:
    """Takes iacore's progress calls (at, add) and the item and file being fetched; writes the status file."""

    def __init__(self, items: int, rate: float | None, *, path: Path | None = None, clock=time.time):
        self.path, self.clock = path or status_path(), clock
        self.s = {"pid": os.getpid(), "pid_start": process_start(os.getpid()), "started": clock(),
                  "updated": clock(), "state": "starting", "rate": rate, "items": items, "item": 0,
                  "identifier": "", "files": 0, "file": 0, "name": "", "file_size": 0, "file_done": 0,
                  "got": 0, "failed": 0, "bytes": 0}
        self._written = 0.0

    def item(self, n: int, identifier: str) -> None:
        self.s.update(state="downloading", item=n, identifier=identifier, files=0, file=0, name="",
                      file_size=0, file_done=0)
        self.write(force=True)

    def file(self, n: int, total: int, name: str, size: int | None) -> None:
        self.s.update(files=total, file=n, name=name, file_size=size or 0, file_done=0)
        self.write()

    def have_file(self, size: int) -> None:
        pass

    def at(self, offset: int) -> None:
        self.s["file_done"] = offset
        self.write()

    def add(self, n: int) -> None:
        self.s["file_done"] += n
        self.s["bytes"] += n
        self.write()

    def done(self, ok: bool) -> None:
        self.s["got" if ok else "failed"] += 1
        self.write(force=True)

    def finish(self, state: str = "finished") -> None:
        self.s["state"] = state
        self.write(force=True)

    def write(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._written < 1.0:
            return
        self._written = self.s["updated"] = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(self.s), encoding="utf-8")
        tmp.replace(self.path)
