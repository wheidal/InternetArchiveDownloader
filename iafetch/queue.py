"""The download queue and its state, in SQLite (~/.local/share/iafetch/iafetch.db), so a second iafetch
(a `queue add` while `get` runs) and an interrupted run both see the same queue (F10, F13).

Removing an item (or clearing the queue) only marks it removed, with the time: `restore` puts it back where it
was, and the removed ones are left out of everything else."""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

PENDING, DONE, FAILED, UNAVAILABLE, REVIEW, HANDED = "pending", "done", "failed", "unavailable", "review", "handed"
REMOVED = "removed"  # taken out of the queue by hand; can be restored
STATES = (PENDING, DONE, FAILED, UNAVAILABLE, REVIEW, HANDED, REMOVED)


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "iafetch"


@dataclass
class Entry:
    identifier: str
    title: str = ""
    size: int = 0
    handler: str = ""  # "" : chosen when it downloads
    options: dict = field(default_factory=dict)  # the selection: formats, globs, exclude, originals
    status: str = PENDING
    added: float = 0.0
    error: str = ""
    result: str = ""  # where it was filed
    id: int = 0
    finished: float | None = None  # when it was downloaded, filed or given up on


class Queue:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "iafetch.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("""CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY, identifier TEXT NOT NULL, title TEXT, size INTEGER, handler TEXT,
            options TEXT, status TEXT, added REAL, error TEXT, result TEXT)""")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(queue)")}
        if "finished" not in columns:  # queues made before items recorded when they ended
            self.db.execute("ALTER TABLE queue ADD COLUMN finished REAL")
        if "position" not in columns:  # queues made before items could be reordered: the order they were added
            self.db.execute("ALTER TABLE queue ADD COLUMN position REAL")
            self.db.execute("UPDATE queue SET position = added")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass

    def _entries(self, where: str = "", args: tuple = ()) -> list[Entry]:
        rows = self.db.execute(f"SELECT identifier, title, size, handler, options, status, added, error, result, id, "
                               f"finished FROM queue {where} ORDER BY position, added, id", args).fetchall()
        return [Entry(r[0], r[1] or "", r[2] or 0, r[3] or "", json.loads(r[4] or "{}"), r[5], r[6], r[7] or "",
                      r[8] or "", r[9], r[10]) for r in rows]

    def all(self) -> list[Entry]:
        """Every entry but the removed ones."""
        return self._entries("WHERE status != ?", (REMOVED,))

    def removed(self) -> list[Entry]:
        """Entries taken out of the queue, most recently removed first."""
        return sorted(self._entries("WHERE status = ?", (REMOVED,)), key=lambda e: e.finished or 0, reverse=True)

    def pending(self) -> list[Entry]:
        return self._entries("WHERE status = ?", (PENDING,))

    def get(self, identifier: str) -> Entry | None:
        found = self._entries("WHERE identifier = ?", (identifier,))
        return found[-1] if found else None

    def add(self, e: Entry) -> bool:
        """Queue an item; False if it's already waiting. One that's done or failed is queued again."""
        with self.db:
            if self.db.execute("SELECT 1 FROM queue WHERE identifier = ? AND status = ?",
                               (e.identifier, PENDING)).fetchone():
                return False
            self.db.execute("DELETE FROM queue WHERE identifier = ?", (e.identifier,))
            added = e.added or time.time()
            self.db.execute("INSERT INTO queue (identifier, title, size, handler, options, status, added, error, result, "
                            "position) VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?)",
                            (e.identifier, e.title, e.size, e.handler, json.dumps(e.options), PENDING, added,
                             max(added, self._last_position() + 1)))
        return True

    def set(self, identifier: str, status: str, error: str = "", result: str = "") -> None:
        assert status in STATES
        with self.db:
            self.db.execute("UPDATE queue SET status = ?, error = ?, result = ?, finished = ? WHERE identifier = ?",
                            (status, error, result, None if status == PENDING else time.time(), identifier))

    def set_options(self, identifier: str, options: dict) -> None:
        with self.db:
            self.db.execute("UPDATE queue SET options = ? WHERE identifier = ?", (json.dumps(options), identifier))

    def still_pending(self, identifier: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM queue WHERE identifier = ? AND status = ?",
                                    (identifier, PENDING)).fetchone())

    def remove(self, identifier: str, when: float | None = None) -> int:
        """Take a waiting item out of the queue (restore puts it back)."""
        with self.db:
            return self.db.execute("UPDATE queue SET status = ?, finished = ? WHERE identifier = ? AND status = ?",
                                   (REMOVED, when or time.time(), identifier, PENDING)).rowcount

    def restore(self, identifiers: list[str] | None = None) -> list[Entry]:
        """Put removed items back where they were in the queue: these, or (None) the last removal, which may
        be a whole clear. What was put back."""
        gone = self.removed()
        if identifiers is None:
            last = gone[0].finished if gone else None
            back = [e for e in gone if e.finished == last]
        else:
            wanted = set(identifiers)
            back = [e for e in gone if e.identifier in wanted]
        with self.db:
            for e in back:
                self.db.execute("UPDATE queue SET status = ?, finished = NULL WHERE id = ?", (PENDING, e.id))
        return sorted(back, key=lambda e: (e.added, e.id))

    def retry(self, identifiers: list[str] | None = None) -> int:
        """Queue failed entries again, at the end."""
        with self.db:
            if identifiers:
                marks = ",".join("?" * len(identifiers))
                return self.db.execute(f"UPDATE queue SET status = ?, error = '', added = ?, position = ? WHERE status = ? "
                                       f"AND identifier IN ({marks})",
                                       (PENDING, time.time(), self._last_position() + 1, FAILED, *identifiers)).rowcount
            return self.db.execute("UPDATE queue SET status = ?, error = '', added = ?, position = ? WHERE status = ?",
                                   (PENDING, time.time(), self._last_position() + 1, FAILED)).rowcount

    def _last_position(self) -> float:
        return self.db.execute("SELECT COALESCE(MAX(position), 0) FROM queue").fetchone()[0]

    def move(self, identifier: str, where: str, busy: str = "") -> bool:
        """Move a waiting item to the "top" or "bottom" of the queue, or one place "up" or "down". `busy`, the item
        being downloaded, stays first and isn't moved. False if the item isn't waiting or can't go further."""
        order = [e for e in self.pending() if e.identifier != busy]
        i = next((n for n, e in enumerate(order) if e.identifier == identifier), None)
        if i is None:
            return False
        j = {"top": 0, "bottom": len(order) - 1, "up": i - 1, "down": i + 1}[where]
        if j == i or not 0 <= j < len(order):
            return False
        order.insert(j, order.pop(i))
        first = min((e.added for e in self.pending()), default=time.time())  # keep `busy` ahead of everything
        with self.db:
            for n, e in enumerate(order):
                self.db.execute("UPDATE queue SET position = ? WHERE identifier = ? AND status = ?",
                                (first + 1 + n, e.identifier, PENDING))
            if busy:
                self.db.execute("UPDATE queue SET position = ? WHERE identifier = ?", (first, busy))
        return True

    def clear(self) -> int:
        """Take everything waiting out of the queue, as one removal (restore puts it all back)."""
        with self.db:
            return self.db.execute("UPDATE queue SET status = ?, finished = ? WHERE status = ?",
                                   (REMOVED, time.time(), PENDING)).rowcount
