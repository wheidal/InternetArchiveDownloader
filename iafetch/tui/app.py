"""The terminal UI: choose what to download, and look after the queue.

Choose: a search (sent when you press Enter) fetches its results once, with everything worth filtering on;
the filters (three-state: include, exclude, neither), the words box, hiding what you have, and sorting then
work on this computer, with no more requests. Tick items and queue them, the same way `iafetch queue add` does.

Queue: what's waiting, in the order `get` takes it; move items, remove them, retry failures, start and stop
the download. The item downloading now can't be moved or removed.

Every request to archive.org goes through the same paced connection as the command line. Text from
archive.org is always shown as plain text, never as markup (titles contain brackets).
"""

import threading

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .. import control, facets as fx
from ..api import Archive, build_query
from ..dashboard import Kinds
from ..net import Client
from ..progress import read_status, running, time_left
from ..queue import FAILED, REVIEW, Queue, data_dir

SEARCH_LIMIT = 10_000  # results fetched for one search (ten requests); narrow the search for more
SHOWN_LIMIT = 3_000  # rows put in the table at once
VALUES_SHOWN = 25  # values listed per filter (the most common; chosen ones always)
SORTS = ("downloads", "rating", "year", "title")
MARK = {1: "+", -1: "−", 0: " "}


def size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class IafetchApp(App):
    TITLE = "iafetch"
    CSS = """
    #searchbar { height: 3; }
    #query { width: 2fr; }
    #collection { width: 1fr; }
    #side { width: 40; }
    #words { margin-bottom: 1; }
    #facets { height: 1fr; }
    #status, #run { padding: 0 1; background: $boost; height: auto; }
    DataTable { height: 1fr; }
    ModalScreen { align: center middle; }
    .dialog { width: 110; max-width: 95%; height: auto; max-height: 90%; border: thick $primary;
              background: $surface; padding: 1 2; }
    #itemtext { height: auto; }
    """

    def __init__(self, config, archive: Archive | None = None, starter=None):
        super().__init__()
        self.config = config
        self.archive = archive or Archive(Client(config.net.api_pause, config.net.download_pause),
                                          data_dir() / "metadata.db")
        self.starter = starter or control.start
        self.busy = threading.Lock()  # one request-making job at a time: archive.org is asked one thing at once

    def on_mount(self) -> None:
        self.push_screen(ChooseScreen())

    def say(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, markup=False)

    def had(self) -> set[str]:
        """Everything queued, downloaded or handed on: what "hide what I have" hides."""
        return {e.identifier for e in Queue().all()}


# --- choose ----------------------------------------------------------------------------------------------

class ChooseScreen(Screen):
    BINDINGS = [
        Binding("s", "focus_search", "Search"),
        Binding("slash", "focus_words", "Words"),
        Binding("space", "tick", "Tick"),
        Binding("a", "tick_shown", "Tick all shown"),
        Binding("A", "untick_all", "Untick all", show=False),
        Binding("plus", "queue", "Queue ticked"),
        Binding("h", "hide_had", "Hide had"),
        Binding("o", "sort", "Sort"),
        Binding("x", "clear_filters", "Clear filters"),
        Binding("d", "downloads", "Queue"),
        Binding("escape", "leave_input", "To the list", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.records: list[fx.Record] = []
        self.shown: list[fx.Record] = []
        self.filters = fx.Filters()
        self.ticked: set[str] = set()
        self.sort = "downloads"
        self.had: set[str] = set()
        self.searching = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="searchbar"):
            yield Input(placeholder="Search archive.org: words, or a query such as title:(basic) — Enter to search",
                        id="query")
            yield Input(placeholder="collection (optional)", id="collection")
        with Horizontal():
            with Vertical(id="side"):
                yield Input(placeholder="words: +basic -games \"machine language\"", id="words")
                yield OptionList(id="facets")
            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results", DataTable)
        table.add_column(" ", key="tick", width=1)
        table.add_column("Title", key="title", width=60)
        table.add_column("Kind", key="kind")
        table.add_column("Year", key="year")
        table.add_column("Language", key="language")
        table.add_column("Size", key="size")
        table.add_column("Views", key="views")
        self.had = self.app.had()
        self.query_one("#query", Input).focus()
        self.show_status()

    # --- searching ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("query", "collection"):
            self.search()
        elif event.input.id == "words":
            self.query_one("#results", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "words":
            self.filters.words = event.value
            self.refilter()

    def search(self) -> None:
        text = self.query_one("#query", Input).value.strip()
        collection = self.query_one("#collection", Input).value.strip()
        try:
            query = build_query(text, collection=collection)
        except ValueError as exc:
            self.app.say(str(exc), "warning")
            return
        if not self.app.busy.acquire(blocking=False):
            self.app.say("Still busy with archive.org; try again in a moment.", "warning")
            return
        self.searching = text or collection
        self.show_status()
        self.run_worker(lambda: self._search(query), thread=True, exclusive=True, group="search")

    def _search(self, query: str) -> None:
        try:
            docs = self.app.archive.scrape(query, fx.FIELDS, limit=SEARCH_LIMIT)
            records = [fx.record(d) for d in docs if d.get("identifier")]
            self.app.call_from_thread(self.loaded, records, len(docs) >= SEARCH_LIMIT)
        except Exception as exc:  # the network, archive.org: say so, and keep the screen usable
            self.app.call_from_thread(self.app.say, f"The search failed: {exc}", "error")
        finally:
            self.app.busy.release()
            self.searching = ""
            self.app.call_from_thread(self.show_status)

    def loaded(self, records: list[fx.Record], capped: bool) -> None:
        self.records, self.ticked = records, set()
        self.had = self.app.had()
        if capped:
            self.app.say(f"Showing the first {SEARCH_LIMIT:,} results; narrow the search for the rest.", "warning")
        self.refilter()
        self.query_one("#results", DataTable).focus()

    # --- filtering ---

    def refilter(self, keep_facet_cursor: bool = True) -> None:
        self.shown = fx.apply(self.records, self.filters, self.had, self.sort)
        table = self.query_one("#results", DataTable)
        current = self._current_id()
        table.clear()
        for r in self.shown[:SHOWN_LIMIT]:
            table.add_row(*self._cells(r), key=r.identifier)
        if current and current in {r.identifier for r in self.shown[:SHOWN_LIMIT]}:
            table.move_cursor(row=table.get_row_index(current))
        self.fill_facets(keep_facet_cursor)
        self.show_status()

    def _cells(self, r: fx.Record) -> list:
        lang = ", ".join(sorted(r.facets["language"])) + ("*" if r.language_guessed else "")
        return ["✓" if r.identifier in self.ticked else " ", Text(r.title), ", ".join(sorted(r.facets["kind"])),
                str(r.year or ""), lang, size(r.size), f"{r.downloads:,}"]

    def fill_facets(self, keep_cursor: bool = True) -> None:
        options = self.query_one("#facets", OptionList)
        highlighted = options.highlighted if keep_cursor else None
        options.clear_options()
        if not self.records:
            return
        items = []
        for facet in fx.FACETS:
            states = self.filters.chosen.get(facet, {})
            counted = fx.counts(self.records, self.filters, facet, self.had)
            values = [v for v, _ in counted.most_common(VALUES_SHOWN)]
            values += [v for v in states if v not in values]
            if len(values) <= 1 and not states:
                continue  # one value for everything: nothing to choose
            items.append(Option(Text(facet.upper(), style="bold"), id=f"head:{facet}", disabled=True))
            for v in values:
                state = states.get(v, 0)
                style = "green" if state > 0 else "red" if state < 0 else ""
                items.append(Option(Text(f" {MARK[state]} {v} ({counted.get(v, 0):,})", style=style),
                                    id=f"{facet}\x00{v}"))
        options.add_options(items)
        if highlighted is not None and highlighted < len(items):
            options.highlighted = highlighted

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option.id or "\x00" not in event.option.id:
            return
        facet, value = event.option.id.split("\x00", 1)
        self.filters.cycle(facet, value)
        self.refilter()

    def show_status(self) -> None:
        status = self.query_one("#status", Static)
        if self.searching:
            status.update(Text(f"Searching archive.org for {self.searching}…"))
            return
        if not self.records:
            status.update(Text("Type a search and press Enter (escape leaves a text box). Then filter on the left "
                               "(Enter on a value: include, again: exclude, again: clear), tick with space, queue with +. "
                               "d: the queue."))
            return
        ticked = [r for r in self.records if r.identifier in self.ticked]
        more = f" (the first {SHOWN_LIMIT:,} listed)" if len(self.shown) > SHOWN_LIMIT else ""
        status.update(Text(f"{len(self.shown):,} of {len(self.records):,} shown{more} · {len(ticked)} ticked, "
                           f"{size(sum(r.size for r in ticked))} as whole items · sort: {self.sort} · "
                           f"{self.filters.summary()}"
                           + (" · * language from the title's region tag" if any(r.language_guessed for r in self.shown) else "")))

    # --- actions ---

    def _current_id(self) -> str | None:
        table = self.query_one("#results", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_leave_input(self) -> None:
        """Escape from a text box: to the results, where the one-key commands work."""
        self.query_one("#results", DataTable).focus()

    def action_focus_search(self) -> None:
        self.query_one("#query", Input).focus()

    def action_focus_words(self) -> None:
        self.query_one("#words", Input).focus()

    def action_tick(self) -> None:
        ident = self._current_id()
        if not ident:
            return
        self.ticked.symmetric_difference_update({ident})
        table = self.query_one("#results", DataTable)
        table.update_cell(ident, "tick", "✓" if ident in self.ticked else " ")
        table.action_cursor_down()
        self.show_status()

    def action_tick_shown(self) -> None:
        self.ticked |= {r.identifier for r in self.shown}
        self.refilter()

    def action_untick_all(self) -> None:
        self.ticked.clear()
        self.refilter()

    def action_hide_had(self) -> None:
        self.filters.hide_had = not self.filters.hide_had
        self.had = self.app.had()
        self.refilter()

    def action_sort(self) -> None:
        self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        self.refilter()

    def action_clear_filters(self) -> None:
        self.filters.clear()
        self.query_one("#words", Input).value = ""
        self.refilter(keep_facet_cursor=False)

    def action_downloads(self) -> None:
        self.app.push_screen(QueueScreen())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "results" and event.row_key.value:
            self.app.push_screen(ItemScreen(event.row_key.value), self._item_closed)

    def _item_closed(self, ticked: bool | None) -> None:
        ident = self._current_id()
        if ticked is not None and ident:
            (self.ticked.add if ticked else self.ticked.discard)(ident)
            self.query_one("#results", DataTable).update_cell(ident, "tick", "✓" if ticked else " ")
            self.show_status()

    def action_queue(self) -> None:
        ticked = [r for r in self.records if r.identifier in self.ticked]
        if not ticked:
            self.app.say("Tick items first (space), then queue them with +.", "warning")
            return
        self.app.push_screen(Confirm(f"Queue {len(ticked):,} item(s)? They're {size(sum(r.size for r in ticked))} as whole "
                                     f"items; iafetch takes only the files each handler wants, usually much less. "
                                     f"Each item's details are read first, about one a second."),
                             lambda yes: yes and self._queue([r.identifier for r in ticked]))

    def _queue(self, identifiers: list[str]) -> None:
        if not self.app.busy.acquire(blocking=False):
            self.app.say("Still busy with archive.org; try again in a moment.", "warning")
            return
        self.run_worker(lambda: self._queue_worker(identifiers), thread=True, group="queue")

    def _queue_worker(self, identifiers: list[str]) -> None:
        from ..cli import queue_identifiers
        done = [0]
        status = self.query_one("#status", Static)

        def say(line: str) -> None:  # one line per item; its manual's line is indented
            done[0] += not line.startswith(" ")
            self.app.call_from_thread(status.update, Text(f"Queuing {done[0]} of {len(identifiers)}: {line.strip()}"))
        try:
            added = queue_identifiers(identifiers, self.app.config, self.app.archive, Queue(), say=say)
            self.app.call_from_thread(self.app.say, f"{added} added to the queue. Start downloading from the queue (d, then g).")
            self.app.call_from_thread(self._queued, set(identifiers))
        except Exception as exc:
            self.app.call_from_thread(self.app.say, f"Queuing stopped: {exc}", "error")
        finally:
            self.app.busy.release()

    def _queued(self, identifiers: set[str]) -> None:
        self.ticked -= identifiers
        self.had = self.app.had()
        self.refilter()


# --- an item --------------------------------------------------------------------------------------------

class ItemScreen(ModalScreen[bool | None]):
    """What `iafetch show` says about an item: loaded when opened (one request, unless it's cached)."""
    BINDINGS = [Binding("escape", "close", "Close"), Binding("space", "tick", "Tick and close")]

    def __init__(self, identifier: str):
        super().__init__()
        self.identifier = identifier

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(Text(f"Reading {self.identifier} from archive.org…"), id="itemtext")
            yield Label("space: tick it and close · escape: close")

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True)

    def _load(self) -> None:
        from ..cli import show_lines
        try:
            item = self.app.archive.item(self.identifier)
            text = "\n".join(show_lines(item, self.app.config, archive=self.app.archive))
        except Exception as exc:
            text = f"{self.identifier}: {exc}"
        self.app.call_from_thread(self.query_one("#itemtext", Static).update, Text(text))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_tick(self) -> None:
        self.dismiss(True)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y", "answer(True)", "Yes"), Binding("n", "answer(False)", "No"),
                Binding("escape", "answer(False)", "No")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.question))
            yield Label("y: yes · n: no")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


# --- the queue --------------------------------------------------------------------------------------------

class QueueScreen(Screen):
    BINDINGS = [
        Binding("t", "move('top')", "Top"),
        Binding("plus", "move('up')", "Up"),
        Binding("minus", "move('down')", "Down"),
        Binding("b", "move('bottom')", "Bottom"),
        Binding("delete", "remove", "Remove"),
        Binding("u", "restore", "Undo remove"),
        Binding("e", "retry", "Retry failed"),
        Binding("g", "start", "Start"),
        Binding("p", "stop(False)", "Stop now"),
        Binding("P", "stop(True)", "Stop after this item"),
        Binding("r", "reload", "Refresh"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self):
        super().__init__()
        self.kinds = Kinds()
        self.drawn: tuple = ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="run")
        yield DataTable(id="queue", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue", DataTable)
        table.add_column("#", key="n")
        table.add_column("Title", key="title", width=70)
        table.add_column("Kind", key="kind")
        table.add_column("Size", key="size")
        table.add_column("", key="note")
        self.action_reload()
        table.focus()
        self.set_interval(3, self.action_reload)

    def busy_id(self) -> str:
        s = read_status()
        return s.get("identifier", "") if running(s) else ""

    def action_reload(self) -> None:
        s = read_status()
        live = running(s)
        q = Queue()
        entries = q.pending()
        failed = [e for e in q.all() if e.status == FAILED]
        review = [e for e in q.all() if e.status == REVIEW]
        if live:
            line = (f"Downloading item {s.get('item')} of {s.get('items')}: {s.get('title') or s.get('identifier')}"
                    + (f" · {size(s.get('speed'))}/s" if s.get("speed") else "")
                    + (f" · {time_left(s)}" if time_left(s) else "")
                    + (" · stopping after this item" if control.stop_after_requested(int(s["pid"])) else ""))
        elif control.read_resume():
            import time as _time
            line = f"Paused until {_time.strftime('%a %H:%M', _time.localtime(control.read_resume()['at']))}, when it starts again by itself. g starts it now."
        else:
            line = "Not downloading. g starts."
        line += (f"\n{len(entries):,} waiting, {size(sum(e.size for e in entries))}"
                 + (f" · {len(failed)} failed (e retries them)" if failed else "")
                 + (f" · {len(review)} need review" if review else ""))
        self.query_one("#run", Static).update(Text(line))
        busy = s.get("identifier", "") if live else ""
        shape = tuple((e.identifier, e.identifier == busy) for e in entries)
        if shape == self.drawn:
            return
        table = self.query_one("#queue", DataTable)
        current = self._current_id()
        table.clear()
        kinds = self.kinds.of([e.identifier for e in entries])
        for n, e in enumerate(entries, 1):
            note = "downloading" if e.identifier == busy else (f"manual for {e.options['manual_for']}"
                                                               if e.options.get("manual_for") else "")
            table.add_row(str(n), Text(e.title or e.identifier), kinds[e.identifier], size(e.size), note, key=e.identifier)
        self.drawn = shape
        if current and current in {e.identifier for e in entries}:
            table.move_cursor(row=table.get_row_index(current))

    def _current_id(self) -> str | None:
        table = self.query_one("#queue", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_move(self, where: str) -> None:
        ident = self._current_id()
        if not ident:
            return
        if ident == self.busy_id():
            self.app.say("That one is downloading now; it stays first.", "warning")
            return
        if Queue().move(ident, where, self.busy_id()):
            self.drawn = ()
            self.action_reload()

    def action_remove(self) -> None:
        ident = self._current_id()
        if not ident:
            return
        if ident == self.busy_id():
            self.app.say("That one is downloading now: stop the download first (p).", "warning")
            return
        Queue().remove(ident)
        self.drawn = ()
        self.action_reload()

    def action_restore(self) -> None:
        back = Queue().restore()
        self.app.say(f"Put back {len(back)} item(s)." if back else "Nothing has been removed.")
        self.drawn = ()
        self.action_reload()

    def action_retry(self) -> None:
        n = Queue().retry()
        self.app.say(f"{n} failed item(s) queued again, at the end." if n else "Nothing has failed.")
        self.drawn = ()
        self.action_reload()

    def action_start(self) -> None:
        if running(read_status()):
            self.app.say("It's already downloading.")
            return
        self.run_worker(self._start, thread=True, group="control")

    def _start(self) -> None:
        ok, message = self.app.starter()
        self.app.call_from_thread(self.app.say, message, "information" if ok else "error")

    def action_stop(self, after: bool) -> None:
        if not running(read_status()):
            self.app.say("Nothing is downloading.")
            return
        if after:
            ok, message = control.stop(after_current=True)
            self.app.say(message, "information" if ok else "error")
            self.action_reload()
            return
        self.app.push_screen(Confirm("Stop the download now? The file in progress keeps what it has, and g resumes it."),
                             lambda yes: yes and self.run_worker(self._stop, thread=True, group="control"))

    def _stop(self) -> None:
        ok, message = control.stop()
        self.app.call_from_thread(self.app.say, message, "information" if ok else "error")

    def action_back(self) -> None:
        self.app.pop_screen()


def run(config) -> None:
    IafetchApp(config).run()
