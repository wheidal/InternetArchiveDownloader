import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from iafetch import dashboard
from iafetch.config import parse_config
from iafetch.progress import Progress, read_status, time_left
from iafetch.queue import DONE, FAILED, REVIEW, Entry, Queue


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ProgressTest(unittest.TestCase):
    def test_run_item_and_file_totals_and_speed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            p = Progress(2, 3000, 4 * 1024 ** 2, path=Path(tmp) / "s.json", clock=clock)
            p.item(1, "film", 1000, "A Film")
            p.file(1, 2, "film.mp4", 800)
            p.at(0)
            for _ in range(8):
                clock.now += 1
                p.add(100)
            self.assertEqual((p.s["item_done"], p.s["done"], p.s["speed"]), (800, 800, 100.0))
            p.file(2, 2, "film.srt", 200)
            p.have_file(200)  # already there from an earlier run
            p.done(True)
            p.grow(1, 500)  # queued while it ran
            s = read_status(Path(tmp) / "s.json")
            self.assertEqual((s["done"], s["total"], s["items"], s["got"]), (1000, 3500, 3, 1))
            self.assertEqual(time_left({"speed": 100, "total": 3500, "done": 1000}), "under a minute left")
            self.assertEqual(time_left({"speed": 1, "total": 7500, "done": 0}), "about 2 h 5 min left")
            self.assertEqual(time_left({"speed": 0, "total": 10, "done": 0}), "")


class DashboardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root = base / "InternetArchive"
        film = self.root / "Movies" / "A Film (1935)"
        (film / ".iafetch" / "film").mkdir(parents=True)
        (film / "A Film (1935).mp4").write_bytes(b"x" * 1000)
        (film / "poster.jpg").write_bytes(b"\xff\xd8jpeg")
        (film / ".iafetch" / "film" / "iafetch.json").write_text("{}")
        (base / "secret.jpg").write_bytes(b"not yours")
        self.config = parse_config({"root": str(self.root)})
        patch = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(base / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        q = Queue()
        q.add(Entry("film", "A Film", 1000))
        q.set("film", DONE, result=str(film))
        q.add(Entry("book", "A Book", 500))
        q.set("book", REVIEW, "no year for the film")
        q.add(Entry("gone", "Gone", 10))
        q.set("gone", FAILED, "HTTP 404")
        q.add(Entry("game", "A Game", 300, options={"manual_for": "x"}))
        q.add(Entry("escape", "Escape", 1))
        q.set("escape", DONE, result=str(base))  # a folder outside the root: its cover is never served

    def test_library(self):
        lib = dashboard.library(self.config)
        movies = next(f for f in lib["folders"] if f["key"] == "movies")
        self.assertEqual((movies["items"], movies["name"]), (1, "Movies"))
        self.assertGreaterEqual(movies["size"], 1000)
        self.assertGreater(lib["free"], 0)
        self.assertIn("error", dashboard.library(parse_config({"root": str(self.root / "missing")})))

    def test_snapshot(self):
        snap = dashboard.snapshot(self.config, None, dashboard.Kinds(Path("/nonexistent")))
        self.assertEqual([e["id"] for e in snap["queue"]], ["game"])
        self.assertEqual(snap["queue"][0]["manual_for"], "x")
        filed = next(h for h in snap["history"] if h["id"] == "film")
        self.assertEqual((filed["kind"], filed["folder"], filed["cover"]), ("video", "Movies/A Film (1935)", True))
        self.assertEqual(sorted(n["id"] for n in snap["needs"]), ["book", "gone"])
        self.assertEqual(snap["counts"]["done"], 2)
        page = dashboard.page(snap, live=False)
        self.assertIn("Internet Archive <span>downloads</span>", page)
        self.assertIn('"id":"film"', page)
        self.assertNotIn("__DATA__", page)

    def test_covers_only_from_inside_the_root(self):
        self.assertEqual(dashboard.cover(self.config, "film").name, "poster.jpg")
        self.assertIsNone(dashboard.cover(self.config, "escape"))
        self.assertIsNone(dashboard.cover(self.config, "book"))

    def test_the_server(self):
        dash = dashboard.Dashboard(self.config)
        dash.refresh_library()
        server = dash.server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/") as r:
            self.assertIn(b"Internet Archive <span>downloads</span>", r.read())
        with urllib.request.urlopen(base + "/data.json") as r:
            self.assertEqual(json.loads(r.read())["queue"][0]["id"], "game")
        with urllib.request.urlopen(base + "/cover/film") as r:
            self.assertEqual((r.headers["Content-Type"], r.read()), ("image/jpeg", b"\xff\xd8jpeg"))
        for path in ("/cover/escape", "/cover/..%2Fsecret.jpg", "/nothing"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + path)
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
