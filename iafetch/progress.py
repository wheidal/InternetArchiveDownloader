"""What a running `iafetch get` is doing, in a status file (~/.local/share/iafetch/status.json) that
`iafetch status` and the dashboard read from another process (F12). Rewritten at most once a second while
files download, and at once when an item starts or ends.

It carries the run's process id, so `status` can tell a run that's going from one that was killed, and `get`
refuses to start a second run alongside the first.
"""

import json
import os
import time
from collections import deque
from pathlib import Path

from .queue import data_dir

SPEED_WINDOW = 20.0  # seconds of downloading the speed is averaged over
MIN_SPEED_TIME = 5.0  # no speed (or time left) until there's this much to go on


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


def time_left(s: dict) -> str:
    """"about 2 h 10 min left" for the whole run, at the current speed; "" if there's no speed yet."""
    speed, left = s.get("speed") or 0, max(0, (s.get("total") or 0) - (s.get("done") or 0))
    if speed <= 0 or not left:
        return ""
    minutes = round(left / speed / 60)
    if minutes < 1:
        return "under a minute left"
    hours, minutes = divmod(minutes, 60)
    return "about " + (f"{hours} h {minutes} min" if hours else f"{minutes} min") + " left"


class Progress:
    """Takes iacore's progress calls (at, add) and the item and file being fetched; writes the status file."""

    def __init__(self, items: int, total: int, rate: float | None, *, path: Path | None = None, clock=time.time):
        self.path, self.clock = path or status_path(), clock
        self.s = {"pid": os.getpid(), "pid_start": process_start(os.getpid()), "started": clock(),
                  "updated": clock(), "state": "starting", "rate": rate, "items": items, "item": 0,
                  "identifier": "", "title": "", "kind": "", "item_size": 0, "item_done": 0,
                  "files": 0, "file": 0, "name": "", "file_size": 0, "file_done": 0,
                  "total": total, "done": 0, "speed": 0.0, "got": 0, "failed": 0, "bytes": 0}
        self._run_base = 0  # bytes of the items finished in this run
        self._item_base = 0  # bytes of this item's finished files
        self._samples: deque = deque()  # (time, bytes received), for the speed
        self._written = 0.0

    def plan(self, items: int, waiting: int) -> None:
        """The run as it stands: `items` in all (done and to come), `waiting` bytes still to fetch. The queue can
        change while a run goes (items queued, moved or removed), so this is said before each item."""
        if (items, self._run_base + waiting) != (self.s["items"], self.s["total"]):
            self.s["items"], self.s["total"] = items, self._run_base + waiting
            self.write(force=True)

    def grow(self, items: int, size: int) -> None:
        """More items joined the run (queued while it ran)."""
        self.s["items"] += items
        self.s["total"] += size
        self.write(force=True)

    def item(self, n: int, identifier: str, size: int = 0, title: str = "", kind: str = "") -> None:
        self.s.update(state="downloading", item=n, identifier=identifier, title=title, kind=kind, item_size=size,
                      item_done=0, files=0, file=0, name="", file_size=0, file_done=0)
        self._item_base = 0
        self.write(force=True)

    def file(self, n: int, total: int, name: str, size: int | None) -> None:
        self._item_base += self.s["file_done"]
        self.s.update(files=total, file=n, name=name, file_size=size or 0, file_done=0)
        self._refresh()

    def have_file(self, size: int) -> None:
        """A file already downloaded by an earlier, interrupted run."""
        self._item_base += size
        self.s["file_done"] = 0
        self._refresh()

    def at(self, offset: int) -> None:
        """A download (re)starts at this offset into the file."""
        self.s["file_done"] = offset
        self._refresh()

    def add(self, n: int) -> None:
        self.s["file_done"] += n
        self.s["bytes"] += n
        now = self.clock()
        self._samples.append((now, self.s["bytes"]))
        while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW:
            self._samples.popleft()
        self._refresh()

    def done(self, ok: bool) -> None:
        """The item ended (downloaded and filed, or not)."""
        self._run_base += self.s["item_size"]
        self.s["got" if ok else "failed"] += 1
        self.s.update(state="between", item_done=self.s["item_size"], file_done=0)
        self.s["done"] = self._run_base
        self.write(force=True)

    def finish(self, state: str = "finished") -> None:
        self.s["state"] = state
        self.write(force=True)

    def _refresh(self) -> None:
        self.s["item_done"] = min(self.s["item_size"] or 1 << 62, self._item_base + self.s["file_done"])
        self.s["done"] = self._run_base + self.s["item_done"]
        if len(self._samples) >= 2:
            (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
            self.s["speed"] = (b1 - b0) / (t1 - t0) if t1 - t0 >= MIN_SPEED_TIME else 0.0
        self.write()

    def write(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._written < 1.0:
            return
        self._written = self.s["updated"] = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(self.s), encoding="utf-8")
        tmp.replace(self.path)
