"""The download queue and its state, in SQLite (~/.local/share/iafetch/iafetch.db), so a second iafetch
(a `queue add` while `get` runs) and an interrupted run both see the same queue (F10, F13)."""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

PENDING, DONE, FAILED, UNAVAILABLE, REVIEW, HANDED = "pending", "done", "failed", "unavailable", "review", "handed"
STATES = (PENDING, DONE, FAILED, UNAVAILABLE, REVIEW, HANDED)


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


class Queue:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "iafetch.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("""CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY, identifier TEXT NOT NULL, title TEXT, size INTEGER, handler TEXT,
            options TEXT, status TEXT, added REAL, error TEXT, result TEXT)""")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass

    def _entries(self, where: str = "", args: tuple = ()) -> list[Entry]:
        rows = self.db.execute(f"SELECT identifier, title, size, handler, options, status, added, error, result, id "
                               f"FROM queue {where} ORDER BY added, id", args).fetchall()
        return [Entry(r[0], r[1] or "", r[2] or 0, r[3] or "", json.loads(r[4] or "{}"), r[5], r[6], r[7] or "",
                      r[8] or "", r[9]) for r in rows]

    def all(self) -> list[Entry]:
        return self._entries()

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
            self.db.execute("INSERT INTO queue (identifier, title, size, handler, options, status, added, error, result) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, '', '')",
                            (e.identifier, e.title, e.size, e.handler, json.dumps(e.options), PENDING,
                             e.added or time.time()))
        return True

    def set(self, identifier: str, status: str, error: str = "", result: str = "") -> None:
        assert status in STATES
        with self.db:
            self.db.execute("UPDATE queue SET status = ?, error = ?, result = ? WHERE identifier = ?",
                            (status, error, result, identifier))

    def set_options(self, identifier: str, options: dict) -> None:
        with self.db:
            self.db.execute("UPDATE queue SET options = ? WHERE identifier = ?", (json.dumps(options), identifier))

    def still_pending(self, identifier: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM queue WHERE identifier = ? AND status = ?",
                                    (identifier, PENDING)).fetchone())

    def remove(self, identifier: str) -> int:
        with self.db:
            return self.db.execute("DELETE FROM queue WHERE identifier = ? AND status = ?",
                                   (identifier, PENDING)).rowcount

    def retry(self, identifiers: list[str] | None = None) -> int:
        """Queue failed entries again, at the end."""
        with self.db:
            if identifiers:
                marks = ",".join("?" * len(identifiers))
                return self.db.execute(f"UPDATE queue SET status = ?, error = '', added = ? WHERE status = ? "
                                       f"AND identifier IN ({marks})", (PENDING, time.time(), FAILED, *identifiers)).rowcount
            return self.db.execute("UPDATE queue SET status = ?, error = '', added = ? WHERE status = ?",
                                   (PENDING, time.time(), FAILED)).rowcount

    def clear(self) -> int:
        with self.db:
            return self.db.execute("DELETE FROM queue WHERE status = ?", (PENDING,)).rowcount
