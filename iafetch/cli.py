"""iafetch command line: search archive.org, look at items, queue them, download and file them."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from iacore.download import DownloadError, Throttle, parse_rate
from iacore.http import FetchError

from . import __version__, rights
from .api import Archive, build_query, identifier_of
from .config import ConfigError, acknowledge_terms, load_config
from .download import DiskFull, download_item
from .handlers import choose, selection
from .handlers.base import Plan, PLAN_SUFFIX
from .handlers.deadtag import HandOffError
from .net import Client, Stopped
from .progress import Progress, log_path, read_status, running
from .queue import DONE, FAILED, HANDED, PENDING, REVIEW, UNAVAILABLE, Entry, Queue, data_dir
from .select import junk, size_of

EXIT_OK, EXIT_ERROR = 0, 1
LARGE_BYTES, LARGE_ITEMS = 500 * 1024 ** 3, 10_000  # R19


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iafetch", description="Find, download, verify and file media from the "
                                     "Internet Archive, politely.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="find items (restricted ones are never shown)")
    s.add_argument("words", nargs="*")
    _filter_args(s)
    s.add_argument("--sort", choices=["downloads", "date", "title", "rating"], default="downloads")
    s.add_argument("--limit", type=int, default=25)

    show = sub.add_parser("show", help="an item: its metadata, license, handler and what would be downloaded")
    show.add_argument("item", help="identifier or archive.org URL")
    show.add_argument("--refresh", action="store_true", help="fetch the metadata again, not from the cache")
    _selection_args(show)

    q = sub.add_parser("queue", help="the download queue")
    qsub = q.add_subparsers(dest="action", required=True)
    add = qsub.add_parser("add", help="queue items, by identifier or archive.org URL")
    add.add_argument("items", nargs="*")
    add.add_argument("--from", dest="from_file", type=Path, help="a text file of identifiers or URLs, one a line")
    add.add_argument("--search", metavar="QUERY", help="every item matching an archive.org query (with the filters "
                     "below); shows how many and how big first, and queues them only with --yes")
    _filter_args(add)
    add.add_argument("--yes", action="store_true", help="with --search: queue them")
    _selection_args(add)
    qsub.add_parser("list", help="what's queued, and what happened to the rest")
    rm = qsub.add_parser("remove", help="take waiting items out of the queue")
    rm.add_argument("items", nargs="+")
    retry = qsub.add_parser("retry", help="queue failed items again")
    retry.add_argument("items", nargs="*", help="(default: every failed one)")
    qsub.add_parser("clear", help="remove everything still waiting")

    get = sub.add_parser("get", help="download the queue, filing each item in the library")
    get.add_argument("--limit", type=int, help="at most this many items")
    get.add_argument("--max-size", help="stop before passing about this much, e.g. 50G")
    get.add_argument("--rate", help="bandwidth cap, e.g. 4M, or none (default: rate in the config)")
    get.add_argument("--no-process", action="store_true", help="download into the inbox only; don't file")
    get.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    get.add_argument("--background", action="store_true", help="run detached; follow it with iafetch status")
    get.add_argument("--large-job", action="store_true", help=f"go ahead with more than {_size(LARGE_BYTES)} or "
                     f"{LARGE_ITEMS:,} items (write to info@archive.org first)")

    proc = sub.add_parser("process", help="file a downloaded item from the inbox (after reviewing its plan)")
    proc.add_argument("item", help=f"identifier, or its {PLAN_SUFFIX} file")

    sub.add_parser("status", help="what a running get is doing")
    sub.add_parser("terms", help="read the Internet Archive's terms notice and acknowledge it")

    args = parser.parse_args(argv)
    try:
        config = load_config()
        return COMMANDS[args.command](args, config)
    except Stopped as exc:
        return _fail(str(exc))
    except (ConfigError, FetchError, DownloadError, HandOffError, ValueError) as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\nStopped. A download in progress resumes where it left off next time.", file=sys.stderr)
        return EXIT_ERROR


def _filter_args(p) -> None:
    p.add_argument("--collection")
    p.add_argument("--kind", help="archive.org mediatype: movies, audio, texts, image, software, data, etree")
    p.add_argument("--creator")
    p.add_argument("--year", help="1935, or a range: 1930-1939")
    p.add_argument("--subject")
    p.add_argument("--license", help="pd (public domain), cc, by, any, or a licenseurl pattern")


def _filters(args) -> dict:
    return {"collection": args.collection or "", "mediatype": args.kind or "", "creator": args.creator or "",
            "year": args.year or "", "subject": args.subject or "", "license": args.license or ""}


def _selection_args(p) -> None:
    p.add_argument("--as", dest="handler", default="", help="the handler to use instead of the usual one")
    p.add_argument("--format", action="append", default=[], help='archive.org format to take, e.g. "h.264" (repeatable)')
    p.add_argument("--glob", action="append", default=[], help="only files whose names match (repeatable)")
    p.add_argument("--exclude", action="append", default=[], help="never files whose names match (repeatable)")
    p.add_argument("--all", action="store_true", help="derivatives too, not only the originals")


def _options(args) -> dict:
    return {k: v for k, v in (("formats", args.format), ("globs", args.glob), ("exclude", args.exclude),
                              ("all", args.all)) if v}


def _archive(config, log=lambda line: None) -> Archive:
    client = Client(config.net.api_pause, config.net.download_pause, log=log)
    return Archive(client, data_dir() / "metadata.db")


# --- search and show -------------------------------------------------------------------------------

def cmd_search(args, config) -> int:
    archive = _archive(config)
    docs = archive.search(" ".join(args.words), sort=args.sort, limit=args.limit, **_filters(args))
    if not docs:
        print("Nothing found.")
        return EXIT_OK
    for d in docs:
        title = d.get("title") or ""
        title = "; ".join(title) if isinstance(title, list) else str(title)
        lic = "license" if d.get("licenseurl") else "no license"
        print(f"{d['identifier']:<40} {d.get('mediatype', ''):<8} {_size(d.get('item_size') or 0):>9}  "
              f"{str(d.get('year') or ''):<4}  {lic:<10}  {title[:60]}")
    print(f"\n{len(docs)} shown. Look closer with: iafetch show <identifier>")
    return EXIT_OK


def cmd_show(args, config) -> int:
    identifier, _ = identifier_of(args.item)
    item = _archive(config).item(identifier, refresh=args.refresh)
    print(f"{item.title}\n  {identifier}  https://archive.org/details/{identifier}")
    print(f"  kind: {item.mediatype}   collections: {', '.join(item.collections)}   year: {item.year or '?'}")
    print(f"  license: {rights.license_text(item)}")
    blocked = rights.restriction(item)
    if blocked:
        print(f"\niafetch won't download this: {blocked}.")
        return EXIT_OK
    handler, why = choose(item, config, args.handler)
    print(f"  handler: {handler.name} ({why})")
    if not handler.downloads:
        print("\nQueued, this goes to deadfetch, which downloads and files it with deadtag.")
        return EXIT_OK
    sel = selection(handler, item, _options(args), config)
    chosen = {f["name"] for f in sel.files}
    print(f"\n  files ({len(item.files)} listed; * = would be downloaded):")
    for f in sorted(item.files, key=lambda f: f.get("name", "")):
        if junk(f) and f["name"] not in chosen:
            continue
        mark = "*" if f["name"] in chosen else " "
        print(f"   {mark} {f['name']:<50} {f.get('source', ''):<10} {f.get('format', ''):<22} {_size(size_of(f)):>9}")
    hidden = sum(1 for f in item.files if junk(f))
    print(f"\n  {len(sel.files)} file(s), {_size(sel.size)} with the item's records"
          f" -> {config.folder(handler.kind(item))}")
    notes = [f"{hidden} generated file(s) not shown (thumbnails, logs, indexes)"] if hidden else []
    if sel.private:
        notes.append(f"{sel.private} private file(s) skipped")
    if sel.encrypted:
        notes.append(f"{sel.encrypted} encrypted lending file(s) skipped")
    for n in notes:
        print(f"  {n}")
    if sel.files and handler.name != "generic":
        plan = handler.plan(item, config.inbox / identifier, sel, config)
        moves = [m for m in plan.moves if not m[1].startswith(".iafetch/")]
        print(f"\n  filed as ({plan.dest}):")
        for _, rel in moves[:SHOW_MOVES]:
            print(f"    {rel}")
        if len(moves) > SHOW_MOVES:
            print(f"    ... and {len(moves) - SHOW_MOVES} more")
        for rel in plan.writes:
            print(f"    {rel}")
        for why in plan.review:
            print(f"  needs review: {why}")
    return EXIT_OK


SHOW_MOVES = 12


# --- the queue ------------------------------------------------------------------------------------

def cmd_queue(args, config) -> int:
    queue = Queue()
    if args.action == "add":
        return _queue_add(args, config, queue)
    if args.action == "list":
        entries = queue.all()
        if not entries:
            print("The queue is empty. Add items with: iafetch queue add <identifier>")
            return EXIT_OK
        for e in entries:
            note = e.error or e.result
            print(f"{e.status:<11} {e.identifier:<40} {_size(e.size):>9}  {e.title[:40]}" + (f"  ({note})" if note else ""))
        waiting = queue.pending()
        print(f"\n{len(waiting)} waiting, about {_size(sum(e.size for e in waiting))}")
        return EXIT_OK
    if args.action == "remove":
        n = sum(queue.remove(identifier_of(x)[0]) for x in args.items)
        print(f"Removed {n}.")
    elif args.action == "retry":
        print(f"Queued {queue.retry([identifier_of(x)[0] for x in args.items] or None)} again.")
    elif args.action == "clear":
        print(f"Removed {queue.clear()} waiting item(s).")
    return EXIT_OK


def _queue_add(args, config, queue) -> int:
    texts = list(args.items)
    if args.from_file:
        texts += [x.strip() for x in args.from_file.read_text(encoding="utf-8").splitlines()
                  if x.strip() and not x.lstrip().startswith("#")]
    archive = _archive(config, log=print)
    if args.search is not None or any(_filters(args).values()):
        found = _search_to_queue(args, archive)
        if found is None:
            return EXIT_OK
        texts += found
    if not texts:
        return _fail("queue what? Give identifiers or URLs, --from a file of them, or --search")
    added = 0
    for text in texts:
        identifier, one_file = identifier_of(text)
        try:
            item = archive.item(identifier)
        except FetchError as exc:
            print(f"{identifier}: {exc}")
            continue
        blocked = rights.restriction(item)
        if blocked:
            print(f"{identifier}: not queued: {blocked}")
            continue
        options = _options(args)
        if one_file:
            options["globs"] = [one_file]
        handler, _ = choose(item, config, args.handler)
        size = selection(handler, item, options, config).size if handler.downloads else int(item.raw.get("item_size") or 0)
        if queue.add(Entry(item.identifier, item.title, size, args.handler, options)):
            added += 1
            print(f"queued {identifier} ({_size(size)}, {handler.name}; {rights.license_text(item)})")
        else:
            print(f"{identifier}: already queued")
    print(f"{added} added. Download with: iafetch get")
    return EXIT_OK


def _search_to_queue(args, archive) -> list[str] | None:
    """The identifiers a --search finds; without --yes, only what it would queue (and None)."""
    query = build_query(args.search or "", **_filters(args))
    docs = archive.scrape(query, ["identifier", "title", "item_size"])
    total = sum(int(d.get("item_size") or 0) for d in docs)
    print(f"{len(docs):,} item(s) match, {_size(total)} in all (whole items: what's downloaded is usually much "
          f"less, only the files each handler takes).")
    for d in docs[:10]:
        print(f"  {d['identifier']:<40} {_size(d.get('item_size') or 0):>9}  {str(d.get('title') or '')[:60]}")
    if len(docs) > 10:
        print(f"  ... and {len(docs) - 10:,} more")
    if len(docs) > LARGE_ITEMS:
        print(f"More than {LARGE_ITEMS:,} items: archive.org asks to hear about jobs this big first "
              f"(info@archive.org); get will need --large-job.")
    if not args.yes:
        if docs:
            print("Nothing queued yet. Run it again with --yes to queue them (each item's metadata is read, "
                  "about one a second).")
        return None
    return [d["identifier"] for d in docs]


# --- get ------------------------------------------------------------------------------------------

def cmd_get(args, config) -> int:
    queue = Queue()
    pending = queue.pending()[: args.limit] if args.limit else queue.pending()
    if not pending:
        print("Nothing queued. Add items with: iafetch queue add <identifier>")
        return EXIT_OK
    rate = parse_rate(args.rate) if args.rate is not None else config.net.rate
    budget = _parse_size(args.max_size) if args.max_size else None
    if args.dry_run:
        for e in pending:
            print(f"would download {e.identifier} ({_size(e.size)})")
        print(f"{len(pending)} item(s), about {_size(sum(e.size for e in pending))}")
        return EXIT_OK
    total = sum(e.size for e in pending)
    if (total > LARGE_BYTES or len(pending) > LARGE_ITEMS) and not args.large_job:
        return _fail(f"this is a large job ({len(pending):,} items, about {_size(total)}). archive.org asks bulk "
                     f"users to start slowly and to write to info@archive.org about big projects first. When "
                     f"you have, run it with --large-job (or use --limit / --max-size)")
    if not _terms_ok(config):
        return EXIT_ERROR
    config.check_root()
    other = read_status()
    if running(other):
        return _fail(f"another iafetch get is already running (process {other['pid']}); see it with: iafetch status")
    if args.background:
        return _start_background(args)
    inbox = config.inside(config.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    say = _say
    archive = _archive(config, log=say)
    throttle = Throttle(rate)
    progress = Progress(len(pending), rate)
    progress.write(force=True)
    before = _stop_on_signals()
    results: dict[str, list[str]] = {k: [] for k in (DONE, REVIEW, HANDED, UNAVAILABLE, FAILED)}
    used = 0
    try:
        for n, e in enumerate(pending, 1):
            if not queue.still_pending(e.identifier):
                continue  # removed from the queue while this ran
            if budget is not None and used + e.size > budget and used:
                say(f"Stopping: the next item would pass --max-size {args.max_size}.")
                break
            say(f"\n== {e.identifier} ({_size(e.size)})")
            progress.item(n, e.identifier)
            status, detail, got = _get_one(e, config, archive, inbox, throttle, progress, args.no_process, say)
            used += got
            queue.set(e.identifier, status, detail if status != DONE else "", detail if status == DONE else "")
            results[status].append(f"{e.identifier}: {detail}")
            progress.done(status in (DONE, REVIEW, HANDED))
            say(f"   {status}: {detail}")
    except (Stopped, DiskFull) as exc:
        progress.finish("stopped")
        _summary(results, queue)
        return _fail(str(exc) + ("" if isinstance(exc, Stopped) else "\nThe queue is paused; free some space and "
                                 "run iafetch get again."))
    except BaseException:
        progress.finish("stopped")
        raise
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)
        archive.close()
    progress.finish()
    _summary(results, queue)
    return EXIT_ERROR if results[FAILED] else EXIT_OK


def _get_one(e: Entry, config, archive, inbox: Path, throttle, progress, no_process: bool, say) -> tuple[str, str, int]:
    """(status, detail, bytes downloaded) for one queued item. Stopped and DiskFull end the run."""
    try:
        item = archive.item(e.identifier, refresh=True)  # what's there now, not last week's listing
    except FetchError as exc:
        gone = "no such item" in str(exc) or "HTTP 404" in str(exc)
        return (UNAVAILABLE if gone else FAILED), str(exc), 0
    blocked = rights.restriction(item)
    if blocked:
        return UNAVAILABLE, blocked, 0
    handler, _ = choose(item, config, e.handler)
    if not handler.downloads:
        try:
            return HANDED, f"queued with deadfetch: {handler.hand_off(e.identifier)}", 0
        except HandOffError as exc:
            return FAILED, str(exc), 0
    sel = selection(handler, item, e.options, config)
    if not sel.files:
        return FAILED, "nothing to download with this selection (see iafetch show)", 0
    say(f"   {item.title}; {rights.license_text(item)}")
    staged_folder = inbox / e.identifier
    got = 0
    if not staged_folder.is_dir():
        try:
            staged = download_item(e.identifier, sel, inbox, archive.client, throttle=throttle, reserve=config.reserve,
                                   log=say, progress=progress)
        except DownloadError as exc:
            return FAILED, str(exc), 0
        got = staged.bytes
        say(f"   {staged.files} file(s) verified" + (f" ({staged.reused} from an earlier run)" if staged.reused else ""))
    if no_process:
        return DONE, f"downloaded to {staged_folder}", got
    plan = handler.plan(item, staged_folder, sel, config)
    if plan.review:
        path = plan.write(inbox)
        return REVIEW, f"{'; '.join(plan.review)}. Edit {path}, set \"approved\": true, then: iafetch process {path}", got
    return DONE, str(handler.process(plan, item, sel, config)), got


def _summary(results: dict[str, list[str]], queue) -> None:
    labels = {DONE: "DONE", REVIEW: "NEEDS REVIEW", HANDED: "HANDED TO DEADFETCH", UNAVAILABLE: "UNAVAILABLE",
              FAILED: "FAILED"}
    print()
    for status, label in labels.items():
        if results[status]:
            print(f"{label} ({len(results[status])})")
            for line in results[status]:
                print(f"  {line}")
    print(f"{len(queue.pending())} still waiting.")
    if results[FAILED]:
        print("Queue the failed ones again with: iafetch queue retry")


def cmd_process(args, config) -> int:
    config.check_root()
    inbox = config.inside(config.inbox)
    path = Path(args.item)
    if not path.name.endswith(PLAN_SUFFIX):
        path = inbox / f"{identifier_of(args.item)[0]}{PLAN_SUFFIX}"
    archive = _archive(config)
    if path.is_file():
        plan = Plan.read(path)
        if not plan.approved:
            return _fail(f"{path} isn't approved: check it, set \"approved\": true, and run this again")
        item = archive.item(plan.identifier)
        handler, _ = choose(item, config, plan.handler)
    else:
        identifier = identifier_of(args.item)[0]
        item = archive.item(identifier)
        handler, _ = choose(item, config, (Queue().get(identifier) or Entry(identifier)).handler)
        plan = None
    entry = Queue().get(item.identifier)
    sel = selection(handler, item, entry.options if entry else {}, config)
    plan = plan or handler.plan(item, inbox / item.identifier, sel, config)
    if plan.review and not plan.approved:
        return _fail(f"{item.identifier} needs review: {'; '.join(plan.review)}. See {plan.write(inbox)}")
    dest = handler.process(plan, item, sel, config)
    path.unlink(missing_ok=True)
    Queue().set(item.identifier, DONE, result=str(dest))
    print(f"Filed: {dest}")
    return EXIT_OK


# --- status, terms ----------------------------------------------------------------------------------

def cmd_status(args, config) -> int:
    s = read_status()
    if not s:
        print("No download has run yet. Start one with: iafetch get")
        return EXIT_OK
    if running(s):
        cap = f", capped at {_size(s['rate'])}/s" if s.get("rate") else ", no rate cap"
        print(f"iafetch get is running (process {s['pid']}, started {_clock(s['started'])}{cap}).")
        print(f"  item {s['item']} of {s['items']}: {s['identifier']}")
        if s.get("name"):
            size = f" of {_size(s['file_size'])}" if s.get("file_size") else ""
            print(f"  file {s['file']} of {s['files']}: {s['name']}, {_size(s['file_done'])}{size}")
        print(f"  this run: {s['got']} done, {s['failed']} failed, {_size(s['bytes'])} downloaded")
    else:
        print(f"No get running. The last one {s.get('state', 'ended')} at {_clock(s.get('updated', 0))}: "
              f"{s.get('got', 0)} done, {s.get('failed', 0)} failed.")
    print(f"{len(Queue().pending())} waiting in the queue.")
    return EXIT_OK


def cmd_terms(args, config) -> int:
    print(rights.TERMS)
    if config.acknowledged_terms:
        print(f"You acknowledged this on {config.acknowledged_terms}.")
        return EXIT_OK
    return EXIT_OK if _ask_terms(config) else EXIT_ERROR


def _terms_ok(config) -> bool:
    """R18: the notice is read and acknowledged once before the first download."""
    if config.acknowledged_terms:
        return True
    print(rights.TERMS)
    return _ask_terms(config)


def _ask_terms(config) -> bool:
    if not sys.stdin.isatty():
        _fail("acknowledge the terms first, in a terminal: iafetch terms")
        return False
    if input("Type yes to acknowledge: ").strip().lower() != "yes":
        print("Not acknowledged; nothing downloaded.")
        return False
    acknowledge_terms(config, time.strftime("%Y-%m-%d"))
    print(f"Thank you. Recorded in {config.path}.")
    return True


# --- helpers -----------------------------------------------------------------------------------------

def _start_background(args) -> int:
    argv = ["get"]
    for flag, value in (("--limit", args.limit), ("--max-size", args.max_size), ("--rate", args.rate)):
        if value is not None:
            argv += [flag, str(value)]
    argv += ["--no-process"] if args.no_process else []
    argv += ["--large-job"] if args.large_job else []
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n--- iafetch {' '.join(argv)}, started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        out.flush()
        child = subprocess.Popen([sys.executable, "-m", "iafetch", *argv], stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**os.environ, "PYTHONUNBUFFERED": "1"})
    print(f"Downloading in the background (process {child.pid}).\n"
          f"  progress:  iafetch status\n  the log:   tail -f {log}\n"
          f"  pause:     kill {child.pid}   (iafetch get picks up where it stopped)")
    return EXIT_OK


def _stop_on_signals() -> dict:
    """SIGTERM (kill, a logout) stops a run the way Ctrl-C does, so the status file says it stopped."""
    def stop(signum, frame):
        raise KeyboardInterrupt
    before = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            before[sig] = signal.signal(sig, stop)
        except ValueError:  # not the main thread (tests)
            pass
    return before


def _say(line: str) -> None:
    print(line, flush=True)


def _fail(message: str) -> int:
    print(f"iafetch: {message}", file=sys.stderr)
    return EXIT_ERROR


def _size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _parse_size(text: str) -> int:
    rate = parse_rate(text)
    if rate is None:
        raise ValueError(f"--max-size {text}: give a size such as 50G")
    return int(rate)


def _clock(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


COMMANDS = {"search": cmd_search, "show": cmd_show, "queue": cmd_queue, "get": cmd_get, "process": cmd_process,
            "status": cmd_status, "terms": cmd_terms}
