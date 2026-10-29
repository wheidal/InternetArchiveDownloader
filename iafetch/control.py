"""Starting and stopping a background `iafetch get`, for `iafetch start`/`stop` and the dashboard's buttons, and
pausing it for a while (`iafetch stop --for 3h` / `--until 22:00`).

Stopping now sends the run SIGTERM, which it handles like Ctrl-C: the file in progress keeps its bytes and the
next run resumes it. Stopping after the current item leaves a note (stop-after, naming the run's process) that
the run reads between items: it finishes and files the item it's on, then ends.

Pausing for a while leaves a resume plan (resume.json: when, and the run's options) and a small detached waiter
process that starts the download again then. It wakes every minute, so a sleeping computer resumes soon after it
wakes; `iafetch start` by hand, or a new stop, cancels or replaces the plan, and the waiter then just ends. A
reboot ends the waiter too: start the download by hand then.
"""

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
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


def start(options: list[str] | None = None) -> tuple[bool, str]:
    """`iafetch start` (with `options`, e.g. ["--rate", "2M"]), as from the command line, with all its checks (the
    terms, the root, a run already going); (started, what it said). For the dashboard's and the terminal UI's
    Start, and for resuming a pause."""
    done = subprocess.run([sys.executable, "-m", "iafetch", "start", *(options or [])], capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL)
    said = (done.stdout + done.stderr).strip().removeprefix("iafetch: ")
    return done.returncode == 0, said.splitlines()[0] if said else ("Started." if done.returncode == 0 else "It didn't start.")



# --- pausing for a while -------------------------------------------------------------------------------------

DURATION_PART = r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m)"
DURATION = rf"(?:\s*{DURATION_PART}\s*)+"


def resume_path() -> Path:
    return data_dir() / "resume.json"


def parse_when(text: str, now: datetime | None = None, *, duration: bool) -> datetime:
    """When to resume. With duration: "90m", "3h", "1h30m", "2d" from now. Else a time: "22:00" (today, or
    tomorrow if it's passed), "2026-10-30 08:00", or "tomorrow 08:00". Raises ValueError."""
    now = now or datetime.now()
    text = text.strip().lower()
    if duration:
        if not re.fullmatch(DURATION, text):
            raise ValueError(f"--for {text!r}: give a length, e.g. 90m, 3h, 1h30m or 2d")
        units = {"d": "days", "h": "hours", "m": "minutes"}
        return now + sum((timedelta(**{units[unit[0]]: float(amount)}) for amount, unit in re.findall(DURATION_PART, text)),
                         timedelta())
    m = re.fullmatch(r"(?:(tomorrow)\s+|(\d{4}-\d{2}-\d{2})[ t])?(\d{1,2}):(\d{2})", text)
    if not m:
        raise ValueError(f"--until {text!r}: give a time, e.g. 22:00, \"tomorrow 08:00\" or \"2026-10-30 08:00\"")
    day = datetime.strptime(m.group(2), "%Y-%m-%d") if m.group(2) else now
    at = day.replace(hour=int(m.group(3)), minute=int(m.group(4)), second=0, microsecond=0)
    if m.group(1):
        at += timedelta(days=1)
    elif not m.group(2) and at <= now:
        at += timedelta(days=1)  # 22:00 when it's 23:00 means tomorrow's
    if at <= now:
        raise ValueError(f"--until {text!r} has already passed")
    return at


def read_resume() -> dict | None:
    try:
        plan = json.loads(resume_path().read_text(encoding="utf-8"))
        return plan if isinstance(plan, dict) and "at" in plan else None
    except (OSError, ValueError):
        return None


def cancel_resume() -> bool:
    """Forget a planned resume (its waiter sees that and ends). True if there was one."""
    had = resume_path().exists()
    resume_path().unlink(missing_ok=True)
    return had


def schedule_resume(at: datetime, options: list[str], spawn=None) -> dict:
    """Plan to start the download again at `at` with `options`, and start the waiter that does it."""
    plan = {"at": at.timestamp(), "options": options, "token": secrets.token_hex(8), "planned": datetime.now().timestamp()}
    resume_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = resume_path().with_name("resume.json.part")
    tmp.write_text(json.dumps(plan), encoding="utf-8")
    tmp.replace(resume_path())
    (spawn or _spawn_waiter)(plan["token"])
    return plan


def _spawn_waiter(token: str) -> int:
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        child = subprocess.Popen([sys.executable, "-m", "iafetch.control", "resume", token], stdout=out,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**os.environ, "PYTHONUNBUFFERED": "1"})
    return child.pid


def wait_and_resume(token: str, *, clock=time.time, sleep=time.sleep, starter=None, say=print) -> str:
    """The waiter: sleep until the plan's time (a minute at a time, so a computer waking from sleep resumes soon
    after), then start the download, unless the plan was cancelled or replaced meanwhile. What happened."""
    while True:
        plan = read_resume()
        if not plan or plan.get("token") != token:
            return "cancelled"
        left = plan["at"] - clock()
        if left <= 0:
            break
        sleep(min(left, 60))
    cancel_resume()
    if running(read_status()):
        return "already running"
    ok, message = (starter or start)(plan.get("options") or [])
    say(f"--- pause over at {time.strftime('%Y-%m-%d %H:%M:%S')}: {message}")
    return "started" if ok else f"didn't start: {message}"


def run_options(status: dict | None) -> list[str]:
    """The options a run was started with (recorded in its status), for starting it again."""
    argv = list((status or {}).get("argv") or [])
    return [a for a in argv if a not in ("get", "--background", "--dry-run")]


if __name__ == "__main__":  # the waiter: python -m iafetch.control resume <token>
    if len(sys.argv) == 3 and sys.argv[1] == "resume":
        wait_and_resume(sys.argv[2])
