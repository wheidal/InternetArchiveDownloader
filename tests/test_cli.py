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


class CliBase(unittest.TestCase):
    """A fake archive.org and a root in a temp folder, for running iafetch's commands."""

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


class CliTest(CliBase):
    def test_show(self):
        code, out = self.run_cli("show", "https://archive.org/details/film", "--as", "generic")
        self.assertEqual(code, 0)
        self.assertIn("license: public domain", out)
        self.assertIn("handler: generic (chosen with --as)", out)
        self.assertRegex(out, r"\* film.mpeg")
        self.assertRegex(out, r"   film.mp4")
        code, out = self.run_cli("show", "film", "--as", "generic", "--format", "h.264")
        self.assertRegex(out, r"\* film.mp4")

    def test_show_a_film(self):
        code, out = self.run_cli("show", "film")
        self.assertIn("handler: video (mediatype movies)", out)
        self.assertIn(f"filed as ({self.root / 'Movies' / 'A Film (1935)'})", out)
        self.assertIn("A Film (1935).mp4", out)

    def test_restricted_items_are_refused(self):  # R14
        code, out = self.run_cli("show", "lent")
        self.assertIn("won't download", out)
        code, out = self.run_cli("queue", "add", "lent")
        self.assertIn("not queued", out)
        self.assertEqual(Queue().pending(), [])

    def test_queue_get_and_file(self):
        code, out = self.run_cli("queue", "add", "film", "--as", "generic", "--format", "h.264")
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

    def test_a_film_lands_in_movies(self):  # §6.7
        self.run_cli("queue", "add", "film")
        code, out = self.run_cli("get")
        self.assertEqual(code, 0, out)
        dest = self.root / "Movies" / "A Film (1935)"
        self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()),
                         [".iafetch/film/film_files.xml", ".iafetch/film/film_meta.xml", ".iafetch/film/iafetch.json",
                          "A Film (1935).mp4", "movie.nfo", "poster.jpg"])
        self.assertIn("<title>A Film</title>", (dest / "movie.nfo").read_text())

    def test_a_film_without_a_year_waits_for_review(self):  # F19
        self.fake.add("noyear", [file("noyear.mp4", fmt="MPEG4")], date="", title="Mystery")
        self.run_cli("queue", "add", "noyear")
        code, out = self.run_cli("get")
        self.assertIn("NEEDS REVIEW (1)", out)
        plan_path = self.root / "_Inbox" / "noyear.iafetch-plan.json"
        plan = json.loads(plan_path.read_text())
        self.assertFalse(plan["approved"])
        self.assertTrue((self.root / "_Inbox" / "noyear" / "noyear.mp4").is_file())
        plan["dest"] = str(self.root / "Movies" / "Mystery (1950)")
        plan["moves"] = [[a, "Mystery (1950).mp4" if b == "Mystery.mp4" else b] for a, b in plan["moves"]]
        plan["approved"] = True
        plan_path.write_text(json.dumps(plan))
        code, out = self.run_cli("process", str(plan_path))
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / "Movies" / "Mystery (1950)" / "Mystery (1950).mp4").is_file())
        self.assertFalse(plan_path.exists())
        self.assertEqual(Queue().get("noyear").status, "done")

    def test_queue_a_search(self):  # R8: through the scrape API, with the restriction filter
        code, out = self.run_cli("queue", "add", "--search", "film", "--kind", "movies")
        self.assertIn("3 item(s) match", out)
        self.assertIn("--yes", out)
        self.assertEqual(Queue().pending(), [])
        scrape = [c for c in self.fake.calls if "/scrape" in c]
        self.assertIn("access-restricted-item%3Atrue", scrape[0])
        code, out = self.run_cli("queue", "add", "--search", "film", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(e.identifier for e in Queue().pending()), ["film", "gd77"])  # lent: refused
        self.assertIn("lent: not queued", out)

    def test_items_queued_during_a_run_join_it(self):
        self.run_cli("queue", "add", "film", "--as", "generic")
        real = cli._get_one

        def get_one(e, *a, **kw):
            if e.identifier == "film":  # someone queues another item while this one downloads
                Queue().add(cli.Entry("noyear", "Mystery", 1))
            return real(e, *a, **kw)
        self.fake.add("noyear", [file("noyear.mp4", fmt="MPEG4")], date="", title="Mystery", mediatype="data")
        with mock.patch.object(cli, "_get_one", get_one):
            code, out = self.run_cli("get")
        self.assertIn("== noyear", out)
        self.assertEqual(Queue().pending(), [])

    def test_a_removed_item_becomes_unavailable(self):
        self.run_cli("queue", "add", "film")
        del self.fake.items["film"]
        code, out = self.run_cli("get")
        self.assertIn("UNAVAILABLE (1)", out)


if __name__ == "__main__":
    unittest.main()


class GameManualTest(CliBase):
    def setUp(self):
        super().setUp()
        self.fake.add("impossible_mission", [file("Impossible_Mission.d64", fmt="Unknown"),
                                             file("00_coverscreenshot.jpg", fmt="JPEG")],
                      mediatype="software", title="Commodore 64: Impossible Mission (Clean)", date="1984",
                      collection=["softwarelibrary_c64_games", "softwarelibrary_c64"])
        self.fake.add("im_manual", [file("im.pdf", fmt="Text PDF")], mediatype="texts",
                      title="Commodore C64 Manual: Impossible Mission (1984)(Epyx)", collection=["commodore_c64_manuals"])
        self.fake.search_docs = [
            {"identifier": "im_manual_it", "title": "Commodore C64 Manual: Impossible Mission (1984)(Epyx)(IT)", "language": "ita"},
            {"identifier": "im2_manual", "title": "Commodore C64 Manual: Impossible Mission II (1988)(Epyx)", "language": "eng"},
            {"identifier": "im_manual_a", "title": "Commodore C64 Manual: Impossible Mission (1984)(Epyx)[a]", "language": "eng"},
            {"identifier": "im_manual", "title": "Commodore C64 Manual: Impossible Mission (1984)(Epyx)", "language": "eng"},
        ]

    def test_a_game_brings_its_manual_into_its_folder(self):
        code, out = self.run_cli("queue", "add", "impossible_mission")
        self.assertIn("with its manual: im_manual", out)
        self.assertEqual([e.identifier for e in Queue().pending()], ["impossible_mission", "im_manual"])
        code, out = self.run_cli("get")
        self.assertEqual(code, 0, out)
        game = self.root / "Games" / "Commodore 64" / "Commodore 64 - Impossible Mission (Clean) (1984)"
        self.assertTrue((game / "Impossible_Mission.d64").is_file(), out)
        self.assertTrue((game / "Manual.pdf").is_file(), out)
        self.assertTrue((game / ".iafetch" / "im_manual" / "iafetch.json").is_file())
        self.assertFalse((self.root / "Books").exists())

    def test_no_manual_when_asked(self):
        code, out = self.run_cli("queue", "add", "impossible_mission", "--no-manual")
        self.assertEqual([e.identifier for e in Queue().pending()], ["impossible_mission"])

    def test_manuals_for_games_already_queued(self):
        self.run_cli("queue", "add", "impossible_mission", "--no-manual")
        code, out = self.run_cli("queue", "manuals")
        self.assertIn("1 manual(s) queued", out)
        code, out = self.run_cli("queue", "manuals")
        self.assertIn("0 manual(s) queued", out)

    def test_a_manual_already_queued_moves_to_its_game(self):
        self.run_cli("queue", "add", "im_manual")
        code, out = self.run_cli("queue", "add", "impossible_mission")
        self.assertIn("already queued; it will go in the game's folder", out)
        self.assertEqual(Queue().get("im_manual").options, {"manual_for": "impossible_mission"})

    def test_remove_from_a_file(self):
        self.run_cli("queue", "add", "impossible_mission", "film", "--no-manual")
        listing = self.root.parent / "drop.txt"
        listing.write_text("# to drop\nfilm\tA Film\tnot wanted\nnot-queued\n")
        code, out = self.run_cli("queue", "remove", "--from", str(listing))
        self.assertIn("Removed 1 of 2", out)
        self.assertEqual([e.identifier for e in Queue().pending()], ["impossible_mission"])
