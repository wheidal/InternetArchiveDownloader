"""The terminal UI, driven with Textual's test pilot against the fake archive.org."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from textual.widgets import DataTable, OptionList, Static
    from iafetch.tui.app import IafetchApp, ItemScreen, QueueScreen
except ImportError:  # Textual is optional
    IafetchApp = None

from iafetch.api import Archive
from iafetch.config import parse_config
from iafetch.net import Client
from iafetch.queue import FAILED, Entry, Queue
from tests.support import FakeArchive, file


@unittest.skipIf(IafetchApp is None, "Textual isn't installed (pip install -e '.[tui]')")
class TuiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        root = Path(tmp.name) / "InternetArchive"
        root.mkdir()
        self.config = parse_config({"root": str(root)})
        self.fake = fake = FakeArchive()
        fake.add("prg-ref", [file("prg.pdf", fmt="Text PDF")], mediatype="texts", language="eng", year="1982",
                 title="Programmer's Reference Guide", collection=["commodore_c64_books", "fav-someone"])
        fake.add("basic-it", [file("b.zip", fmt="Single Page Processed JP2 ZIP")], mediatype="texts", language="ita",
                 title="Manuale del BASIC", collection=["commodore_c64_books"])
        fake.add("manual-de", [file("m.pdf", fmt="Text PDF")], mediatype="texts", language="eng",
                 title="Disk Drive 1541-II (198x)(Commodore)(DE)", collection=["commodore_c64_manuals"])
        self.started = []
        self.app = IafetchApp(self.config, Archive(Client(1.0, 1.0, get=fake.get, opener=fake.open, sleep=lambda s: None)),
                              starter=lambda: (self.started.append(1), (True, "Downloading in the background."))[1])

    async def search(self, pilot, text="", collection=""):
        screen = self.app.screen
        screen.query_one("#query").value = text
        screen.query_one("#collection").value = collection
        screen.query_one("#query").focus()
        await pilot.press("enter")
        await self.app.workers.wait_for_complete()
        await pilot.pause()
        return screen

    def shown(self, screen):
        return [r.identifier for r in screen.shown]

    async def test_search_filter_tick_and_queue(self):
        async with self.app.run_test(size=(180, 50)) as pilot:
            screen = await self.search(pilot, collection="commodore_c64_books")
            self.assertEqual(sorted(self.shown(screen)), ["basic-it", "manual-de", "prg-ref"])
            options = screen.query_one("#facets", OptionList)
            ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
            self.assertIn("language\x00German", ids)  # the (DE) in the title, not the field's "eng"
            self.assertFalse(any(i.endswith("fav-someone") for i in ids))  # favourites lists aren't collections
            # Enter on "English": include it
            options.focus()
            options.highlighted = ids.index("language\x00English")
            await pilot.press("enter")
            self.assertEqual(self.shown(screen), ["prg-ref"])
            # again: exclude it
            await pilot.press("enter")
            self.assertEqual(sorted(self.shown(screen)), ["basic-it", "manual-de"])
            await pilot.press("enter")  # and clear
            self.assertEqual(len(self.shown(screen)), 3)
            # the words box
            screen.query_one("#words").value = "-basic"
            await pilot.pause()
            self.assertNotIn("basic-it", self.shown(screen))
            # tick one, queue it
            table = screen.query_one("#results", DataTable)
            table.focus()
            table.move_cursor(row=table.get_row_index("prg-ref"))
            await pilot.press("space")
            self.assertEqual(screen.ticked, {"prg-ref"})
            await pilot.press("plus")
            await pilot.press("y")
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual([e.identifier for e in Queue().pending()], ["prg-ref"])
            # hide what's queued
            await pilot.press("h")
            self.assertNotIn("prg-ref", self.shown(screen))

    async def test_an_item(self):
        async with self.app.run_test(size=(180, 50)) as pilot:
            screen = await self.search(pilot, collection="commodore_c64_books")
            table = screen.query_one("#results", DataTable)
            table.focus()
            table.move_cursor(row=table.get_row_index("prg-ref"))
            await pilot.press("enter")
            self.assertIsInstance(self.app.screen, ItemScreen)
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            text = str(self.app.screen.query_one("#itemtext", Static).render())
            self.assertIn("handler: texts", text)
            await pilot.press("space")  # tick it and close
            await pilot.pause()
            self.assertEqual(screen.ticked, {"prg-ref"})

    async def test_the_queue_screen(self):
        q = Queue()
        for i in ("a", "b", "c"):
            q.add(Entry(i, f"Item {i}", 10))
        q.add(Entry("broken", "Broken", 10))
        q.set("broken", FAILED, "HTTP 404")
        async with self.app.run_test(size=(180, 50)) as pilot:
            await pilot.press("d")  # in the search box: just a letter
            self.assertNotIsInstance(self.app.screen, QueueScreen)
            await pilot.press("escape", "d")
            self.assertIsInstance(self.app.screen, QueueScreen)
            table = self.app.screen.query_one("#queue", DataTable)
            table.move_cursor(row=2)  # c
            await pilot.press("t")
            self.assertEqual([e.identifier for e in Queue().pending()], ["c", "a", "b"])
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "c")  # cursor follows
            await pilot.press("minus")
            self.assertEqual([e.identifier for e in Queue().pending()], ["a", "c", "b"])
            await pilot.press("delete")
            self.assertEqual([e.identifier for e in Queue().pending()], ["a", "b"])
            await pilot.press("e")  # retry the failed one
            self.assertEqual([e.identifier for e in Queue().pending()], ["a", "b", "broken"])
            await pilot.press("g")
            await self.app.workers.wait_for_complete()
            self.assertEqual(self.started, [1])
            await pilot.press("escape")
            self.assertNotIsInstance(self.app.screen, QueueScreen)


if __name__ == "__main__":
    unittest.main()
