"""The one way iafetch talks to archive.org (R1-R7, R13).

Every request goes through a Client: it carries iafetch's User-Agent, waits its turn (a pause between API
calls, a longer one between downloads), backs off for everyone when archive.org says to slow down, and stops
the whole run when archive.org keeps refusing us. Downloads themselves are iacore's (resumable, MD5-checked);
the Client is the opener they go through.
"""

import os
import random
import threading
import time
import urllib.error
import urllib.parse

from iacore import http as _http
from iacore.http import FetchError

from . import __version__

PROJECT_URL = "https://github.com/wheidal/InternetArchiveDownloader"
HOSTS = ("archive.org",)
DISALLOWED = ("/control/", "/report/")  # archive.org's robots.txt (R13)
STOP_AFTER = 5  # consecutive 429s or 403s that stop the run (R5)
RETRIES = 3

BLOCKED_ADVICE = ("archive.org is refusing our requests. Don't restart this right away, and never work around it "
                  "(another address or User-Agent). If it keeps happening, write to info@archive.org and say "
                  "what you were downloading.")


class Stopped(Exception):
    """The circuit breaker tripped: the run must end (R5). Not an OSError, so no download retry catches it."""


def user_agent() -> str:
    """R1: who we are. A run started by an AI agent says so, as archive.org's bots page asks."""
    agent = os.environ.get("IAFETCH_AGENT") or ("claude-code" if os.environ.get("CLAUDECODE") else "")
    return f"iafetch/{__version__} (+{PROJECT_URL}; personal collection{'; agent=' + agent if agent else ''})"


class Client:
    def __init__(self, api_pause: float = 1.0, download_pause: float = 2.0, *, ua: str | None = None,
                 get=_http.http_get, opener=_http.http_open, clock=time.monotonic, sleep=time.sleep,
                 log=lambda line: None):
        self.ua = ua or user_agent()
        self.api_pause, self.download_pause = api_pause, download_pause
        self._get, self._open = get, opener
        self.clock, self.sleep, self.log = clock, sleep, log
        self._lock = threading.Lock()
        self._last = None  # when the last request started
        self._until = 0.0  # no request before this (a global backoff, R4)
        self.refusals = 0  # consecutive 429/403s
        self.requests = 0

    # --- pacing ---

    def _turn(self, pause: float) -> None:
        """Wait until this request may start: `pause` after the last one, and after any backoff."""
        with self._lock:
            now = self.clock()
            ready = max(self._until, (self._last + pause) if self._last is not None else now)
            if ready > now:
                self.sleep(ready - now)
            self._last = max(now, ready)
            self.requests += 1

    def back_off(self, seconds: float) -> None:
        """Every request waits at least `seconds` from now, whichever worker was told to slow down."""
        with self._lock:
            self._until = max(self._until, self.clock() + seconds)

    def _refused(self, code: int) -> None:
        self.refusals += 1
        if self.refusals >= STOP_AFTER:
            raise Stopped(f"archive.org refused {self.refusals} requests in a row (last: HTTP {code}). "
                          + BLOCKED_ADVICE)

    def _ok(self) -> None:
        self.refusals = 0

    # --- requests ---

    def get(self, url: str, limit: int = _http.MAX_GET_BYTES) -> bytes:
        """A small reply read whole (metadata, search), paced, retried when archive.org says to wait."""
        check_url(url)
        for attempt in range(1, RETRIES + 1):
            self._turn(self.api_pause)
            try:
                data = self._get(url, limit=limit, user_agent=self.ua)
            except FetchError as exc:
                code = getattr(exc.__cause__, "code", None)
                if code in (429, 503):
                    self._refused(code)
                    wait = _http.retry_after(exc.__cause__, 30 * attempt) * random.uniform(1.0, 1.2)
                    if attempt < RETRIES:
                        self.log(f"  archive.org asked us to slow down (HTTP {code}); waiting {wait:.0f} s")
                        self.back_off(wait)
                        continue
                elif code == 403:
                    self._refused(code)
                elif code in (500, 502, 504) or code is None:  # server trouble or the network: try again
                    if attempt < RETRIES:
                        self.back_off(2 * 4 ** (attempt - 1))  # R6: 2, 8 s
                        continue
                raise
            self._ok()
            return data
        raise AssertionError("unreachable")

    def open(self, url: str, start: int = 0):
        """A download response, for iacore.download.fetch_file. iacore retries and waits; this paces each
        attempt, tells every other request to wait too, and counts refusals for the breaker."""
        check_url(url)
        self._turn(self.download_pause)
        try:
            response = self._open(url, start, user_agent=self.ua)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503):
                self.back_off(_http.retry_after(exc, 30))
                self._refused(exc.code)
            elif exc.code == 403:
                self._refused(exc.code)
            raise
        self._ok()
        return response


def check_url(url: str) -> None:
    """Only archive.org, only over HTTPS, and never a path its robots.txt disallows (R13)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in HOSTS):
        raise ValueError(f"iafetch only fetches from archive.org over HTTPS, not {url}")
    if any(parts.path.startswith(p) for p in DISALLOWED):
        raise ValueError(f"{url}: archive.org's robots.txt asks robots not to fetch {parts.path}")
