"""The downloads dashboard: a page about the downloads, served from this machine and kept current.

The page asks /data.json for a fresh snapshot every few seconds: the status file a running `get` writes and
the queue (what's waiting, what was filed where, what needs you). Served on this computer only, it also has
Start and Stop buttons (as `iafetch start`/`stop`); opened to the home network (--lan) it only watches.
Queuing stays in the CLI. The library numbers walk the folders on the NAS, so they're worked out at start and
every LIBRARY_EVERY seconds, not on every request. Covers of filed items are served from their folders.
"""

import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import control
from .progress import read_status, running, time_left
from .queue import DONE, FAILED, HANDED, PENDING, REVIEW, UNAVAILABLE, Queue, data_dir
from .rights import SIDECAR

TEMPLATE = Path(__file__).with_name("dashboard.html")
PORT = 8643  # deadfetch's is 8642: both can run at once (the CLI's DASHBOARD_PORT)
REFRESH = 3  # seconds between the page's requests
LIBRARY_EVERY = 600  # seconds
HISTORY = 500  # finished items listed, newest first
COVERS = ("poster.jpg", "cover.jpg", "folder.jpg", "cover.png")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}
# archive.org mediatype -> the kind the page filters by
KINDS = {"movies": "video", "texts": "books", "software": "games", "audio": "audio", "etree": "audio",
         "image": "images", "data": "data"}
# library folder (a key of Config.library) -> the same kinds
FOLDER_KINDS = {"movies": "video", "tv": "video", "books": "books", "magazines": "books", "comics": "books",
                "games": "games", "music": "audio", "livemusic": "audio", "radio": "audio", "audiobooks": "audio",
                "podcasts": "audio", "spoken": "audio",
                "images": "images", "data": "data", "other": "other"}


def library(config) -> dict:
    """Items and bytes in each library folder, and the free space on the root's drive."""
    if not config.root.is_dir():
        return {"error": f"{config.root} isn't there (is the NAS mounted?)"}
    folders = []
    for key, folder in config.library.items():
        if not folder.is_dir():
            continue
        items = size = 0
        for dirpath, dirnames, filenames in os.walk(folder):
            items += SIDECAR in filenames  # one record per item filed, wherever its handler keeps it
            for name in filenames:
                try:
                    size += os.stat(os.path.join(dirpath, name)).st_size
                except OSError:
                    pass
        folders.append({"key": key, "name": folder.name, "kind": FOLDER_KINDS.get(key, "other"),
                        "items": items, "size": size})
    usage = shutil.disk_usage(config.root)
    return {"folders": folders, "free": usage.free, "disk": usage.total, "root": str(config.root),
            "at": time.time()}


class Kinds:
    """Each queued item's kind, from the metadata cache (no request to archive.org)."""

    def __init__(self, cache: Path | None = None):
        self.cache = cache or data_dir() / "metadata.db"
        self.known: dict[str, str] = {}

    def of(self, identifiers: list[str]) -> dict[str, str]:
        missing = [i for i in identifiers if i not in self.known]
        if missing and self.cache.is_file():
            db = sqlite3.connect(f"file:{self.cache}?mode=ro", uri=True)
            try:
                for i in missing:
                    row = db.execute("SELECT json FROM metadata WHERE identifier = ?", (i,)).fetchone()
                    mediatype = (json.loads(row[0]).get("metadata") or {}).get("mediatype") if row else ""
                    self.known[i] = KINDS.get(mediatype or "", "other")
            finally:
                db.close()
        return {i: self.known.get(i, "other") for i in identifiers}


def _folder_kind(config, result: str) -> str:
    try:
        path = Path(result)
    except TypeError:
        return ""
    for key, folder in config.library.items():
        if path.is_relative_to(folder):
            return FOLDER_KINDS.get(key, "other")
    return ""


def snapshot(config, lib: dict | None = None, kinds: Kinds | None = None, writes: bool = False) -> dict:
    """Everything the page shows, as of now."""
    s = read_status()
    live = running(s)
    status = None
    if s:
        status = dict(s, running=live, left=time_left(s) if live else "",
                      quiet=round(time.time() - s.get("updated", time.time())))
    current = s.get("identifier") if live else None
    entries = Queue().all()
    kind_of = (kinds or Kinds()).of([e.identifier for e in entries])

    def row(e) -> dict:
        out = {"id": e.identifier, "title": e.title, "size": e.size, "kind": kind_of[e.identifier], "status": e.status}
        if e.options.get("manual_for"):
            out["manual_for"] = e.options["manual_for"]
        return out

    finished = sorted((e for e in entries if e.status != PENDING), key=lambda e: e.finished or 0, reverse=True)
    history = []
    for e in finished[:HISTORY]:
        r = row(e)
        r.update(when=e.finished, error=e.error, folder="", cover=False)
        if e.status == DONE and e.result:
            r["kind"] = _folder_kind(config, e.result) or r["kind"]
            r["folder"] = _relative(config, e.result)
            r["cover"] = True
        history.append(r)
    return {
        "at": time.time(),
        "status": status,
        "queue": [dict(row(e), now=e.identifier == current) for e in entries if e.status == PENDING],
        "history": history,
        "needs": [dict(row(e), why=e.error) for e in finished if e.status in (REVIEW, FAILED)],
        "counts": {k: sum(1 for e in entries if e.status == k) for k in (DONE, REVIEW, FAILED, UNAVAILABLE, HANDED)},
        "library": lib,
        "control": {"writes": writes, "stop_after": bool(live and control.stop_after_requested(int(s["pid"])))},
    }


def _relative(config, result: str) -> str:
    try:
        return str(Path(result).relative_to(config.root))
    except ValueError:
        return result


def cover(config, identifier: str) -> Path | None:
    """The cover image of a filed item (its poster, cover or folder image), only from inside the root."""
    entry = Queue().get(identifier)
    if not entry or entry.status != DONE or not entry.result:
        return None
    folder = Path(entry.result)
    for name in COVERS:
        path = folder / name
        if path.is_file():
            resolved = path.resolve()
            return resolved if resolved.is_relative_to(config.root.resolve()) else None
    return None


def page(data: dict, *, live: bool, token: str = "") -> str:
    """The whole page, with `data` inside it. A live page then keeps itself current from /data.json."""
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    body = (TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob)
            .replace("__LIVE__", "true" if live else "false").replace("__REFRESH__", str(REFRESH * 1000))
            .replace("__TOKEN__", json.dumps(token)))
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + body + "\n</html>\n")


class Dashboard:
    """The data behind the served page: a fresh snapshot per request, the library refreshed in the background."""

    def __init__(self, config, writes: bool = False, starter=None):
        """writes: the page may start and stop the download (only when served on this computer)."""
        self.config = config
        self.writes = writes
        self.token = secrets.token_urlsafe(18)  # only pages this dashboard served can start or stop
        self.starter = starter or control.start
        self.lib: dict | None = None
        self.kinds = Kinds()
        self._stop = threading.Event()

    def refresh_library(self) -> None:
        try:
            self.lib = library(self.config)
        except OSError as exc:  # the NAS away: the rest of the page still works
            self.lib = {"error": str(exc)}

    def keep_library_fresh(self) -> threading.Thread:
        def loop():
            while not self._stop.is_set():
                self.refresh_library()
                self._stop.wait(LIBRARY_EVERY)
        thread = threading.Thread(target=loop, name="library", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        return snapshot(self.config, self.lib, self.kinds, self.writes)

    def act(self, action: str, body: dict) -> dict:
        if action == "stop":
            ok, message = control.stop(after_current=bool(body.get("after")))
        elif running(read_status()):
            ok, message = False, "A download is already running."
        else:
            ok, message = self.starter()
        return {"ok": ok, "message": message}

    def server(self, host: str = "127.0.0.1", port: int = PORT) -> ThreadingHTTPServer:
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (the http.server name)
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    self._send(page(dash.snapshot(), live=True, token=dash.token if dash.writes else "").encode(),
                               "text/html; charset=utf-8")
                elif path == "/data.json":
                    self._send(json.dumps(dash.snapshot(), separators=(",", ":")).encode(), "application/json")
                elif path.startswith("/cover/"):
                    image = cover(dash.config, urllib.parse.unquote(path.removeprefix("/cover/")))
                    if image is None or image.suffix.lower() not in IMAGE_TYPES:
                        self.send_error(404)
                        return
                    self._send(image.read_bytes(), IMAGE_TYPES[image.suffix.lower()], cache="max-age=3600")
                else:
                    self.send_error(404)

            def do_POST(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path not in ("/api/start", "/api/stop"):
                    self.send_error(404)
                    return
                origin = self.headers.get("Origin")
                if (not dash.writes or self.headers.get("X-Iafetch-Token") != dash.token
                        or (origin and origin.split("://", 1)[-1] != self.headers.get("Host"))):
                    self._send(json.dumps({"ok": False, "message": "this page can't start or stop downloads"}).encode(),
                               "application/json", 403)
                    return
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    body = body if isinstance(body, dict) else {}
                except ValueError:
                    body = {}
                self._send(json.dumps(dash.act(path.removeprefix("/api/"), body)).encode(), "application/json")

            def _send(self, body: bytes, kind: str, code: int = 200, cache: str = "no-store") -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # quiet: a request every 3 seconds would flood the terminal
                pass

        return ThreadingHTTPServer((host, port), Handler)

