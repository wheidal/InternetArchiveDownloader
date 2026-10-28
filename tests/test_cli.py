import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iafetch import cli
from iafetch.net import Client
from iafetch.queue import Queue
from tests.support import FakeArchive, file


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "InternetArchive"
        self.root.mkdir()
        self.config = base / "config.toml"
        self.config.write_text(f'acknowledged_terms = "2026-10-28"\nroot = "{self.root}"\n')
        self.fake = FakeArchive()
        self.fake.add("film", [file("film.mpeg"), file("film.mp4", "derivative", "h.264", size=400),
                               file("__ia_thumb.jpg", fmt="Item Tile", size=80)])
        self.fake.add("gd77", [file("gd77.flac")], collection=["GratefulDead", "etree"], mediatype="etree")
        self.fake.add("lent", [file("lent.pdf")], **{"access-restricted-item": "true"})
        fake = self.fake
        patches = [
            mock.patch.dict("os.environ", {"IAFETCH_CONFIG": str(self.config), "XDG_DATA_HOME": str(base / "data")}),
            mock.patch.object(cli, "Client", lambda *a, **kw: Client(*a, get=fake.get, opener=fake.open,
                                                                     sleep=lambda s: None, **kw)),
            mock.patch("iafetch.download.shutil.disk_usage", lambda p: mock.Mock(free=10 ** 12)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_show(self):
        code, out = self.run_cli("show", "https://archive.org/details/film")
        self.assertEqual(code, 0)
        self.assertIn("license: public domain", out)
        self.assertIn("handler: generic", out)
        self.assertRegex(out, r"\* film.mpeg")
        self.assertRegex(out, r"   film.mp4")
        code, out = self.run_cli("show", "film", "--format", "h.264")
        self.assertRegex(out, r"\* film.mp4")

    def test_restricted_items_are_refused(self):  # R14
        code, out = self.run_cli("show", "lent")
        self.assertIn("won't download", out)
        code, out = self.run_cli("queue", "add", "lent")
        self.assertIn("not queued", out)
        self.assertEqual(Queue().pending(), [])

    def test_queue_get_and_file(self):
        code, out = self.run_cli("queue", "add", "film", "--format", "h.264")
        self.assertEqual(code, 0, out)
        self.assertIn("queued film", out)
        code, out = self.run_cli("get")
        self.assertEqual(code, 0, out)
        dest = self.root / "Other" / "film"
        self.assertTrue((dest / "film.mp4").is_file(), out)
        self.assertFalse((dest / "film.mpeg").exists())
        self.assertTrue((dest / "folder.jpg").is_file())
        self.assertEqual(json.loads((dest / "iafetch.json").read_text())["identifier"], "film")
        self.assertIn("DONE (1)", out)
        self.assertEqual(Queue().get("film").status, "done")
        code, out = self.run_cli("status")
        self.assertIn("1 done", out)

    def test_grateful_dead_goes_to_deadfetch(self):  # §6.2, R21
        self.run_cli("queue", "add", "gd77")
        with mock.patch("iafetch.handlers.deadtag.Deadtag.hand_off", return_value="queued 1") as hand_off:
            code, out = self.run_cli("get")
        hand_off.assert_called_once_with("gd77")
        self.assertIn("HANDED TO DEADFETCH (1)", out)
        self.assertEqual([p.name for p in self.root.iterdir()], ["_Inbox"])
        self.assertEqual(list((self.root / "_Inbox").iterdir()), [])
        self.assertFalse(any("/download/" in c for c in self.fake.calls))

    def test_a_missing_root_stops_get(self):  # R22
        self.run_cli("queue", "add", "film")
        self.root.rmdir()
        code, out = self.run_cli("get")
        self.assertEqual(code, 1)
        self.assertIn("create it by hand", out)
        self.assertFalse(self.root.exists())

    def test_the_terms_come_first(self):  # R18
        self.config.write_text(f'root = "{self.root}"\n')
        self.run_cli("queue", "add", "film")
        with mock.patch("sys.stdin", io.StringIO("")):
            code, out = self.run_cli("get")
        self.assertEqual(code, 1)
        self.assertIn("scholarship and research", out)
        self.assertFalse(any("/download/" in c for c in self.fake.calls))

    def test_a_large_job_needs_saying_so(self):  # R19
        self.run_cli("queue", "add", "film")
        with mock.patch.object(cli, "LARGE_BYTES", 10):
            code, out = self.run_cli("get")
        self.assertEqual(code, 1)
        self.assertIn("info@archive.org", out)

    def test_a_removed_item_becomes_unavailable(self):
        self.run_cli("queue", "add", "film")
        del self.fake.items["film"]
        code, out = self.run_cli("get")
        self.assertIn("UNAVAILABLE (1)", out)


if __name__ == "__main__":
    unittest.main()
