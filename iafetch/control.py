"""Starting and stopping a background `iafetch get`, for `iafetch start`/`stop` and the dashboard's buttons.

Stopping now sends the run SIGTERM, which it handles like Ctrl-C: the file in progress keeps its bytes and the
next run resumes it. Stopping after the current item leaves a note (stop-after, naming the run's process) that
the run reads between items: it finishes and files the item it's on, then ends.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .progress import log_path, read_status, running
from .queue import data_dir

STOP_WAIT = 30  # seconds `stop` waits for the run to say it has stopped


def stop_after_path() -> Path:
    return data_dir() / "stop-after"


def start_background(argv: list[str]) -> int:
    """Start `iafetch <argv>` in its own session, writing to the log; its process id."""
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    stop_after_path().unlink(missing_ok=True)  # an old request mustn't stop the new run
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n--- iafetch {' '.join(argv)}, started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        out.flush()
        child = subprocess.Popen([sys.executable, "-m", "iafetch", *argv], stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**os.environ, "PYTHONUNBUFFERED": "1"})
    return child.pid


def request_stop_after(pid: int) -> None:
    stop_after_path().parent.mkdir(parents=True, exist_ok=True)
    stop_after_path().write_text(str(pid), encoding="utf-8")


def stop_after_requested(pid: int | None = None) -> bool:
    """True if a stop after the current item was asked for this run (`pid`, default this process)."""
    try:
        return int(stop_after_path().read_text(encoding="utf-8").strip()) == (pid or os.getpid())
    except (OSError, ValueError):
        return False


def clear_stop_after() -> None:
    stop_after_path().unlink(missing_ok=True)


def stop(after_current: bool = False, wait: float = STOP_WAIT, *, kill=os.kill, sleep=time.sleep,
         clock=time.monotonic) -> tuple[bool, str]:
    """Stop the running get, now or after its current item. (stopped or asked, what to tell the user)."""
    s = read_status()
    if not running(s):
        return False, "No iafetch get is running."
    pid, what = int(s["pid"]), s.get("title") or s.get("identifier") or "the current item"
    if after_current:
        request_stop_after(pid)
        return True, (f"It will stop after {what} is downloaded and filed (process {pid}). "
                      f"To stop at once instead: iafetch stop")
    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True, "It had already ended."
    except PermissionError:
        return False, f"Not allowed to stop process {pid} (it belongs to another user)."
    end = clock() + wait
    while clock() < end:
        now = read_status()
        if not running(now):
            break
        sleep(0.5)
    else:
        return False, f"Process {pid} hasn't stopped after {wait:.0f} s; it may be finishing a write. Try again shortly."
    now = read_status() or s
    done = ""
    if now.get("name") and now.get("file_size"):
        done = f", {now['file_done'] / 1024 ** 3:.1f} of {now['file_size'] / 1024 ** 3:.1f} GB of {now['name']}"
    return True, f"Stopped in {what}{done}. iafetch start picks up where it left off."
