"""iafetch command line: search archive.org, look at items, queue them, download and file them."""

import argparse
import json
import re
import signal
import socket
import sys
import time
from pathlib import Path

from iacore.download import DownloadError, Throttle, parse_rate
from iacore.http import FetchError

from . import __version__, control, rights
from .api import Archive, build_query, identifier_of
from .config import ConfigError, acknowledge_terms, load_config
from .download import DiskFull, download_item
from .handlers import choose, selection
from .handlers.video import picture
from .handlers.base import Plan, PLAN_SUFFIX
from .handlers.deadtag import HandOffError
from .net import Client, Stopped
from .progress import Progress, log_path, read_status, running
from .queue import DONE, FAILED, HANDED, PENDING, REVIEW, UNAVAILABLE, Entry, Queue, data_dir
from .select import junk, size_of

EXIT_OK, EXIT_ERROR = 0, 1
LARGE_BYTES, LARGE_ITEMS = 500 * 1024 ** 3, 10_000  # R19
DASHBOARD_PORT = 8643  # deadfetch's dashboard is on 8642, so both can run at once


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
    add.add_argument("--no-manual", action="store_true", help="don't also queue a game's manual")
    _selection_args(add)
    qsub.add_parser("list", help="what's queued, and what happened to the rest")
    rm = qsub.add_parser("remove", help="take waiting items out of the queue")
    rm.add_argument("items", nargs="*")
    rm.add_argument("--from", dest="from_file", type=Path, help="a text file of identifiers, one a line (the first "
                    "word of each line; # starts a comment)")
    man = qsub.add_parser("manuals", help="queue the manuals of the games already queued or downloaded")
    mv = qsub.add_parser("move", help="change where waiting items are in the queue")
    mv.add_argument("where", choices=["top", "up", "down", "bottom"])
    mv.add_argument("items", nargs="+", help="identifiers (several move together, keeping their order)")
    retry = qsub.add_parser("retry", help="queue failed items again")
    retry.add_argument("items", nargs="*", help="(default: every failed one)")
    qsub.add_parser("clear", help="remove everything still waiting (queue restore --last puts it back)")
    qsub.add_parser("removed", help="what was taken out of the queue, most recent first")
    rs = qsub.add_parser("restore", help="put removed items back where they were")
    rs.add_argument("items", nargs="*", help="identifiers")
    rs.add_argument("--last", action="store_true", help="the last removal (a whole clear, or the last remove)")
    ex = qsub.add_parser("export", help="save the queue to a file (identifiers, options and order), to import later")
    ex.add_argument("file", type=Path, help="where to write it; - for the terminal")
    ex.add_argument("--all", action="store_true", help="every entry (done, failed, removed…), not only what's waiting")
    im = qsub.add_parser("import", help="queue the items in a file: an export, or identifiers one a line")
    im.add_argument("file", type=Path)
    im.add_argument("--again", action="store_true", help="also items already downloaded (else they're skipped)")

    get = sub.add_parser("get", help="download the queue, filing each item in the library")
    _run_args(get)
    get.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    get.add_argument("--background", action="store_true", help="run detached; follow it with iafetch status")

    start = sub.add_parser("start", help="download the queue in the background (iafetch get --background)")
    _run_args(start)
    stop = sub.add_parser("stop", help="stop the background download; iafetch start resumes it")
    stop.add_argument("--after-current", action="store_true", help="let the item it's on finish and be filed first")

    proc = sub.add_parser("process", help="file a downloaded item from the inbox (after reviewing its plan)")
    proc.add_argument("item", help=f"identifier, or its {PLAN_SUFFIX} file")

    pk = sub.add_parser("picks", help="the best-rated films on archive.org (by IMDb), each with its sharpest copy",
                        description="Films Wikidata links to archive.org, ranked by IMDb rating, each with its sharpest "
                        "plausible upload. By default only films Wikidata records as public domain in the US: a "
                        "volunteer-kept record, a good guide but not legal advice.")
    pk.add_argument("--top", type=int, default=25, help="how many films (default 25)")
    pk.add_argument("--min-votes", type=int, default=5000, help="IMDb votes a rating needs to count (default 5000)")
    pk.add_argument("--year", help="1940, or a range: 1920-1949")
    pk.add_argument("--country", help="of origin, e.g. Japan, France, Sweden (Germany includes Weimar, East and "
                    "West Germany; Russia the Soviet Union)")
    pk.add_argument("--copies", action="store_true", help="list every upload of each film, not only the best")
    pk.add_argument("--queue", action="store_true", help="queue the best copy of each film listed (never a doubtful one)")
    pk.add_argument("--any-rights", action="store_true", help="not only films Wikidata says are public domain in "
                    "the US; whether you may download the rest is up to you")
    pk.add_argument("--refresh", action="store_true", help="read Wikidata's and IMDb's lists again")

    sub.add_parser("status", help="what a running get is doing")

    dash = sub.add_parser("dashboard", help="a live page of the downloads, in a browser on this computer")
    dash.add_argument("--port", type=int, default=DASHBOARD_PORT, help=f"(default {DASHBOARD_PORT})")
    dash.add_argument("--lan", action="store_true", help="open it to the home network, not only this computer")
    dash.add_argument("--html", metavar="FILE", type=Path, help="write a snapshot page instead (it doesn't refresh)")
    sub.add_parser("terms", help="read the Internet Archive's terms notice and acknowledge it")
    sub.add_parser("tui", help="the terminal UI: search, filter and queue; look after the queue")

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


def _run_args(p) -> None:
    p.add_argument("--limit", type=int, help="at most this many items")
    p.add_argument("--max-size", help="stop before passing about this much, e.g. 50G")
    p.add_argument("--rate", help="bandwidth cap, e.g. 4M, or none (default: rate in the config)")
    p.add_argument("--no-process", action="store_true", help="download into the inbox only; don't file")
    p.add_argument("--large-job", action="store_true", help=f"go ahead with more than {_size(LARGE_BYTES)} or "
                   f"{LARGE_ITEMS:,} items (write to info@archive.org first)")


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
    p.add_argument("--episodes", help="a radio series' episodes by number: 1-52,60")
    p.add_argument("--years", help="a radio series' episodes by year: 1952 or 1952-1953")


def _options(args) -> dict:
    return {k: v for k, v in (("formats", args.format), ("globs", args.glob), ("exclude", args.exclude),
                              ("all", args.all), ("episodes", args.episodes), ("years", args.years)) if v}


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
    print("\n".join(show_lines(item, config, _options(args), args.handler, archive=_archive(config))))
    return EXIT_OK


def show_lines(item, config, options: dict | None = None, handler_name: str = "", archive=None) -> list[str]:
    """What `iafetch show` prints about an item: its metadata, license, handler, the files it would take and
    where they'd be filed. The terminal UI's item view shows the same."""
    out: list[str] = []
    say = out.append
    identifier = item.identifier
    say(f"{item.title}\n  {identifier}  https://archive.org/details/{identifier}")
    say(f"  kind: {item.mediatype}   collections: {', '.join(item.collections)}   year: {item.year or '?'}")
    say(f"  license: {rights.license_text(item)}")
    blocked = rights.restriction(item)
    if blocked:
        say(f"\niafetch won't download this: {blocked}.")
        return out
    handler, why = choose(item, config, handler_name)
    say(f"  handler: {handler.name} ({why})")
    if not handler.downloads:
        say("\nQueued, this goes to deadfetch, which downloads and files it with deadtag.")
        return out
    sel = selection(handler, item, options or {}, config)
    chosen = {f["name"] for f in sel.files}
    say(f"\n  files ({len(item.files)} listed; * = would be downloaded):")
    for f in sorted(item.files, key=lambda f: f.get("name", "")):
        if junk(f) and f["name"] not in chosen:
            continue
        mark = "*" if f["name"] in chosen else " "
        say(f"   {mark} {f['name']:<50} {f.get('source', ''):<10} {f.get('format', ''):<22} {_size(size_of(f)):>9}"
            + (f"  {picture(f)}" if picture(f) else ""))
    hidden = sum(1 for f in item.files if junk(f))
    say(f"\n  {len(sel.files)} file(s), {_size(sel.size)} with the item's records"
        f" -> {config.folder(handler.kind(item))}")
    notes = [f"{hidden} generated file(s) not shown (thumbnails, logs, indexes)"] if hidden else []
    if sel.private:
        notes.append(f"{sel.private} private file(s) skipped")
    if sel.encrypted:
        notes.append(f"{sel.encrypted} encrypted lending file(s) skipped")
    for n in notes:
        say(f"  {n}")
    if sel.files and handler.name != "generic":
        if archive is not None:
            handler.prepare(item, archive)
        plan = handler.plan(item, config.inbox / identifier, sel, config)
        moves = [m for m in plan.moves if not m[1].startswith(".iafetch/")]
        say(f"\n  filed as ({plan.dest}):")
        for _, rel in moves[:SHOW_MOVES]:
            say(f"    {rel}")
        if len(moves) > SHOW_MOVES:
            say(f"    ... and {len(moves) - SHOW_MOVES} more")
        for rel in plan.writes:
            say(f"    {rel}")
        for why in plan.review:
            say(f"  needs review: {why}")
    return out


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
        keys = list(args.items) + (_lines(args.from_file) if args.from_file else [])
        if not keys:
            return _fail("remove what? Give identifiers, or --from a file of them")
        when = time.time()  # one removal, for queue restore --last
        n = sum(queue.remove(identifier_of(x.split()[0])[0], when) for x in keys)
        print(f"Removed {n} of {len(keys)} (the rest weren't waiting in the queue). Undo: iafetch queue restore --last")
    elif args.action == "manuals":
        return _queue_manuals(config, queue)
    elif args.action == "move":
        s = read_status()
        busy = s.get("identifier", "") if running(s) else ""
        ids = [identifier_of(x)[0] for x in args.items]
        # top and down move the last first, so several keep their order
        moved = [i for i in (ids[::-1] if args.where in ("top", "down") else ids) if queue.move(i, args.where, busy)]
        for i in ids:
            if i not in moved:
                print(f"{i}: not moved ({'it is downloading now' if i == busy else 'not waiting, or already there'})")
        print(f"Moved {len(moved)}. The queue's order is what get follows next.")
    elif args.action == "retry":
        print(f"Queued {queue.retry([identifier_of(x)[0] for x in args.items] or None)} again.")
    elif args.action == "clear":
        print(f"Removed {queue.clear()} waiting item(s). Undo: iafetch queue restore --last")
    elif args.action == "removed":
        gone = queue.removed()
        if not gone:
            print("Nothing has been removed from the queue.")
        for e in gone:
            print(f"{_clock(e.finished or 0)}  {e.identifier:<40} {_size(e.size):>9}  {e.title[:50]}")
        if gone:
            print(f"\n{len(gone)} removed. Put back: iafetch queue restore <identifier> ..., or --last for the last removal")
    elif args.action == "restore":
        if not args.items and not args.last:
            return _fail("restore what? Give identifiers, or --last for the last removal (see: iafetch queue removed)")
        back = queue.restore(None if args.last else [identifier_of(x)[0] for x in args.items])
        print(f"Put back {len(back)} item(s), where they were in the queue.")
    elif args.action == "export":
        return _queue_export(args, queue)
    elif args.action == "import":
        return _queue_import(args, config, queue)
    return EXIT_OK


def _queue_add(args, config, queue) -> int:
    texts = list(args.items) + (_lines(args.from_file) if args.from_file else [])
    archive = _archive(config, log=print)
    if args.search is not None or any(_filters(args).values()):
        found = _search_to_queue(args, archive)
        if found is None:
            return EXIT_OK
        texts += found
    if not texts:
        return _fail("queue what? Give identifiers or URLs, --from a file of them, or --search")
    added = queue_identifiers(texts, config, archive, queue, _options(args), args.handler,
                              manuals=not args.no_manual, say=print)
    print(f"{added} added. Download with: iafetch start")
    return EXIT_OK


def queue_identifiers(texts: list[str], config, archive, queue, options: dict | None = None, handler_name: str = "",
                      *, manuals: bool = True, say=print) -> int:
    """Queue items (identifiers or URLs), as `queue add` does: restricted ones refused, each sized by what its
    handler would take, a game with its manual. How many were added."""
    added = 0
    for text in texts:
        identifier, one_file = identifier_of(text)
        try:
            item = archive.item(identifier)
        except FetchError as exc:
            say(f"{identifier}: {exc}")
            continue
        blocked = rights.restriction(item)
        if blocked:
            say(f"{identifier}: not queued: {blocked}")
            continue
        opts = dict(options or {})
        if one_file:
            opts["globs"] = [one_file]
        handler, _ = choose(item, config, handler_name)
        size = selection(handler, item, opts, config).size if handler.downloads else int(item.raw.get("item_size") or 0)
        if queue.add(Entry(item.identifier, item.title, size, handler_name, opts)):
            added += 1
            say(f"queued {identifier} ({_size(size)}, {handler.name}; {rights.license_text(item)})")
            if handler.name == "software" and manuals:
                added += _queue_manual(item, config, archive, queue, say)
        else:
            say(f"{identifier}: already queued")
    return added


EXPORT_FORMAT = "iafetch-queue/1"


def _queue_export(args, queue) -> int:
    entries = (queue.all() + queue.removed()) if args.all else queue.pending()
    data = {"format": EXPORT_FORMAT, "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "entries": [{"identifier": e.identifier, "title": e.title, "size": e.size, "handler": e.handler,
                         "options": e.options, "status": e.status} for e in entries]}
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if str(args.file) == "-":
        sys.stdout.write(text)
        return EXIT_OK
    path = args.file.expanduser()
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {len(entries)} item(s) to {path}. Queue them again with: iafetch queue import {path}")
    return EXIT_OK


def _queue_import(args, config, queue) -> int:
    """Queue what a file lists: an export (each item with its options and handler, in its order; only those
    waiting when it was exported), or identifiers or URLs one a line. Items already queued are left as they
    are; ones already downloaded are skipped unless --again."""
    text = args.file.expanduser().read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        if data.get("format") != EXPORT_FORMAT:
            return _fail(f"{args.file} isn't an iafetch queue export")
        wanted = [e for e in data.get("entries", []) if e.get("status", PENDING) == PENDING]
    else:
        wanted = [{"identifier": identifier_of(x.split()[0])[0]} for x in _lines(args.file)]
    have = {e.identifier: e.status for e in queue.all()}
    todo = [e for e in wanted if have.get(e["identifier"]) != PENDING and (args.again or have.get(e["identifier"]) != DONE)]
    skipped = len(wanted) - len(todo)
    archive = _archive(config, log=print)
    added = 0
    for e in todo:
        added += queue_identifiers([e["identifier"]], config, archive, queue, e.get("options") or {},
                                   e.get("handler", ""), manuals=False, say=print)
    print(f"{added} added" + (f"; {skipped} skipped (already queued or downloaded; --again takes downloaded ones)"
                              if skipped else "") + ". Download with: iafetch start")
    return EXIT_OK


def _queue_manual(game, config, archive, queue, say=print) -> int:
    """Queue the game's English manual, if archive.org has one, to be filed in the game's folder. 1 if queued."""
    from .handlers.software import platform
    from .handlers.texts import manual_docs
    docs = manual_docs(archive, game, platform(game))
    if not docs:
        return 0
    d = docs[0]
    manual = archive.item(d["identifier"])
    size = selection(choose(manual, config)[0], manual, {}, config).size
    if queue.add(Entry(d["identifier"], manual.title, size, "texts", {"manual_for": game.identifier})):
        say(f"  with its manual: {d['identifier']} ({_size(size)})")
        return 1
    queued = queue.get(d["identifier"])
    if queued and queued.status == PENDING:  # already waiting (e.g. queued with a whole collection)
        queue.set_options(d["identifier"], {**queued.options, "manual_for": game.identifier})
        say(f"  its manual {d['identifier']} is already queued; it will go in the game's folder")
        return 1
    say(f"  its manual {d['identifier']} was downloaded before ({queued.result if queued else ''})")
    return 0


def _queue_manuals(config, queue) -> int:
    from .handlers.software import Software
    archive = _archive(config)
    games = [e for e in queue.all() if e.status in (PENDING, DONE)]
    added = 0
    for e in games:
        item = archive.item(e.identifier)
        if choose(item, config, e.handler)[0].name != Software.name:
            continue
        if any(x.options.get("manual_for") == e.identifier for x in queue.all()):
            continue
        print(f"{item.title}:")
        n = _queue_manual(item, config, archive, queue)
        if not n:
            print("  no English manual on archive.org")
        added += n
    print(f"{added} manual(s) queued. Download with: iafetch get")
    return EXIT_OK


def _lines(path: Path) -> list[str]:
    """The non-blank, non-comment lines of a text file."""
    return [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]


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
    progress = Progress(len(pending), sum(e.size for e in pending), rate)
    progress.write(force=True)
    before = _stop_on_signals()
    results: dict[str, list[str]] = {k: [] for k in (DONE, REVIEW, HANDED, UNAVAILABLE, FAILED)}
    used = 0
    try:
        tried: set[str] = set()
        only = {e.identifier for e in pending} if args.limit else None  # --limit: just these; else whatever is queued
        n = 0
        while True:
            # The queue as it is now, in its order: items queued, moved or removed while this runs count
            waiting = [x for x in queue.pending() if x.identifier not in tried and (only is None or x.identifier in only)]
            if not waiting:
                break
            progress.plan(n + len(waiting), sum(x.size for x in waiting))
            if control.stop_after_requested():
                say("\nStopping here, as asked (iafetch stop --after-current). iafetch start carries on.")
                break
            e = waiting[0]
            tried.add(e.identifier)
            n += 1
            if budget is not None and used + e.size > budget and used:
                say(f"Stopping: the next item would pass --max-size {args.max_size}.")
                break
            say(f"\n== {e.identifier} ({_size(e.size)})")
            progress.item(n, e.identifier, e.size, e.title)
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
    game = e.options.get("manual_for")
    game_entry = Queue().get(game) if game else None
    if game_entry and game_entry.status == DONE and game_entry.result:
        from .handlers.texts import manual_plan
        plan = manual_plan(item, staged_folder, sel, config, game_entry.result)
    else:
        handler.prepare(item, archive)
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
    if plan is None:
        handler.prepare(item, archive)
        plan = handler.plan(item, inbox / item.identifier, sel, config)
    if plan.review and not plan.approved:
        return _fail(f"{item.identifier} needs review: {'; '.join(plan.review)}. See {plan.write(inbox)}")
    dest = handler.process(plan, item, sel, config)
    path.unlink(missing_ok=True)
    Queue().set(item.identifier, DONE, result=str(dest))
    print(f"Filed: {dest}")
    return EXIT_OK


# --- picks --------------------------------------------------------------------------------------------

def cmd_picks(args, config) -> int:
    from . import picks
    if not 1 <= args.top <= 500:
        raise ValueError("--top must be 1 to 500")
    years = _years(args.year) if args.year else None
    every = picks.Lists(log=print).films(refresh=args.refresh)
    countries = picks.country_names(args.country, every) if args.country else None
    films = picks.ranked(every, min_votes=args.min_votes, years=years, any_rights=args.any_rights, countries=countries)
    if not args.any_rights:
        others = len(picks.ranked(every, min_votes=args.min_votes, years=years, any_rights=True,
                                  countries=countries)) - len(films)
        if others:
            print(f"({others:,} more rated films match but aren't known to be public domain; --any-rights shows them.)")
    if not films:
        print("No rated films match" + ("" if args.min_votes <= 100 else "; try a lower --min-votes") + ".")
        return EXIT_OK
    archive = _archive(config)
    which = "rated films" if args.any_rights else "rated films in the US public domain (per Wikidata)"
    print(f"Finding the best copy of each, among {len(films):,} {which} (archive.org metadata, about one a second "
          f"the first time)...")
    found = picks.best_copies(films, archive, top=args.top, config=config)
    queue = Queue()
    queued = 0
    print()
    for n, (film, copies) in enumerate(found, 1):
        year = f" ({film.year})" if film.year else ""
        rights = "" if film.public_domain else "   (not known to be public domain)"
        print(f"{n:>3}. {film.title}{year}   IMDb {film.rating:.1f} ({film.votes:,} votes){rights}")
        for i, c in enumerate(copies if args.copies else copies[:1]):
            entry = queue.get(c.identifier)
            mark = f" [{entry.status}]" if entry else ""
            stars = f"  {c.stars:.1f}/5 ({c.reviews})" if c.stars else ""
            parts = f", {c.parts} parts" if c.parts > 1 else ""
            doubt = f"  ? {c.doubt}" if c.doubt else ""
            print(f"       {c.identifier:<40} {c.picture:<26} {_size(c.size):>9}{parts}{stars}{mark}{doubt}")
        if not args.copies and len(copies) > 1:
            print(f"       ({len(copies) - 1} other cop{'y' if len(copies) == 2 else 'ies'}: --copies lists them)")
        best = copies[0]
        if args.queue and not best.doubt and not queue.get(best.identifier):
            item = archive.item(best.identifier)
            sel = selection(choose(item, config)[0], item, {}, config)
            if queue.add(Entry(best.identifier, item.title, sel.size)):
                queued += 1
    print("\nRatings: IMDb (personal, non-commercial use); films and their archive.org IDs: Wikidata.")
    print("A ? marks an upload that may be a cut version, a fragment or another film: its length, year or title "
          "disagrees with Wikidata's. It's never queued.")
    if args.any_rights:
        print("Films not known to be public domain may be uploads without the rights holder's permission; "
              "archive.org's terms ask for non-infringing use.")
    if args.queue:
        print(f"{queued} queued. Download with: iafetch get")
    else:
        print("Queue the best copies with --queue, or one at a time with: iafetch queue add <identifier>")
    return EXIT_OK


def _years(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", text.strip())
    if not m:
        raise ValueError(f"--year {text}: give a year (1940) or a range (1920-1949)")
    return int(m.group(1)), int(m.group(2) or m.group(1))


# --- dashboard ----------------------------------------------------------------------------------------

def cmd_dashboard(args, config) -> int:
    from . import dashboard as dashboard_mod
    dash = dashboard_mod.Dashboard(config, writes=not args.lan)
    if args.html:
        dash.refresh_library()
        path = args.html.expanduser()
        path.write_text(dashboard_mod.page(dash.snapshot(), live=False), encoding="utf-8")
        print(f"Wrote the downloads page: {path}")
        return EXIT_OK
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = dash.server(host, args.port)
    except OSError as exc:
        return _fail(f"can't serve on port {args.port}: {exc.strerror or exc}. Is a dashboard already running? "
                     "Pick another port with --port")
    dash.keep_library_fresh()
    where = f"http://{socket.gethostname()}:{args.port}  (on the home network)" if args.lan else \
        f"http://localhost:{args.port}"
    print(f"The downloads dashboard is at {where}\nIt refreshes itself every {dashboard_mod.REFRESH} s"
          + ("; on the home network it only watches (Start and Stop are on this computer's page)" if args.lan else "")
          + ". Ctrl-C stops it (downloads carry on).", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        dash.stop()
        server.server_close()
    return EXIT_OK


def cmd_tui(args, config) -> int:
    try:
        from .tui.app import run
    except ImportError:
        return _fail("the terminal UI needs Textual: .venv/bin/pip install -e \".[tui]\" (in iafetch's folder)")
    run(config)
    return EXIT_OK


# --- status, terms ----------------------------------------------------------------------------------

def cmd_status(args, config) -> int:
    s = read_status()
    if not s:
        print("No download has run yet. Start one with: iafetch get")
        return EXIT_OK
    if running(s):
        cap = f", capped at {_size(s['rate'])}/s" if s.get("rate") else ", no rate cap"
        print(f"iafetch get is running (process {s['pid']}, started {_clock(s['started'])}{cap}). "
              f"Stop it with: iafetch stop")
        print(f"  item {s['item']} of {s['items']}: {s['identifier']}")
        if s.get("name"):
            size = f" of {_size(s['file_size'])}" if s.get("file_size") else ""
            print(f"  file {s['file']} of {s['files']}: {s['name']}, {_size(s['file_done'])}{size}")
        print(f"  this run: {s['got']} done, {s['failed']} failed, {_size(s['bytes'])} downloaded")
        if control.stop_after_requested(int(s["pid"])):
            print("  it will stop when this item is filed (iafetch stop --after-current)")
    else:
        print(f"No get running. The last one {s.get('state', 'ended')} at {_clock(s.get('updated', 0))}: "
              f"{s.get('got', 0)} done, {s.get('failed', 0)} failed. Start one with: iafetch start")
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
    pid = control.start_background(argv)
    print(f"Downloading in the background (process {pid}).\n"
          f"  progress:  iafetch status, or iafetch dashboard\n  the log:   tail -f {log_path()}\n"
          f"  pause:     iafetch stop   (or --after-current); iafetch start picks up where it stopped")
    return EXIT_OK


def cmd_start(args, config) -> int:
    args.background, args.dry_run = True, False
    return cmd_get(args, config)


def cmd_stop(args, config) -> int:
    ok, message = control.stop(after_current=args.after_current)
    print(message)
    return EXIT_OK if ok else EXIT_ERROR


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


COMMANDS = {"search": cmd_search, "show": cmd_show, "picks": cmd_picks, "queue": cmd_queue, "get": cmd_get, "process": cmd_process,
            "status": cmd_status, "terms": cmd_terms, "dashboard": cmd_dashboard, "start": cmd_start, "stop": cmd_stop,
            "tui": cmd_tui}
