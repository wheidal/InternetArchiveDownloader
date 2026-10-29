import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iafetch import cli
from iafetch.livemusic.rank import Recording, rank_date, source_kind
from iafetch.net import Client
from iafetch.queue import Queue
from tests.support import FakeArchive


def rec(identifier, source="", formats=("Flac",), rating=None, reviews=0, downloads=100):
    return Recording(identifier, "2006-10-21", source=source, formats=list(formats), rating=rating, reviews=reviews,
                     downloads=downloads)


class RankTest(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(source_kind(rec("tlg2006-10-21.sbd.flac16")), "sbd")
        self.assertEqual(source_kind(rec("tlg2006-10-21.mk4v.flac16")), "aud")  # named by its microphones
        self.assertEqual(source_kind(rec("x", source="Matrix of audience and board")), "matrix")
        self.assertEqual(source_kind(rec("x")), "unknown")

    def test_the_better_tape_first(self):
        tapes = [rec("aud", "audience", rating=4.0, reviews=2), rec("sbd.partial", "soundboard", rating=5, reviews=1),
                 rec("sbd", "soundboard", rating=4.6, reviews=9, downloads=900), rec("shn", "soundboard", ("Shorten",))]
        self.assertEqual([x.recording.identifier for x in rank_date(tapes, 4.2)][:2], ["sbd", "shn"])
        self.assertEqual(rank_date(tapes, 4.2)[-1].score.notes, ["partial"])


class ShowsCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "root").mkdir()
        (base / "config.toml").write_text(f'root = "{base / "root"}"\n')
        self.fake = fake = FakeArchive()
        for ident, source, rating in (("tlg2006-10-21.mk4v.flac16", "Schoeps MK4v", 5), ("tlg2006-10-21.sbd.flac16", "soundboard", 4.8),
                                      ("tlg2006-10-22.flac16", "audience", None)):
            fake.add(ident, [], mediatype="etree", collection=["TeaLeafGreen", "etree"], creator="Tea Leaf Green",
                     date=ident[3:13], venue="The Vic Theatre", coverage="Chicago, IL", source=source,
                     title=ident)
            if rating:
                fake.items[ident].update(avg_rating=rating, num_reviews=4)
        for p in (mock.patch.dict(os.environ, {"IAFETCH_CONFIG": str(base / "config.toml"), "XDG_DATA_HOME": str(base)}),
                  mock.patch.object(cli, "Client", lambda *a, **kw: Client(*a, get=fake.get, opener=fake.open,
                                                                         sleep=lambda s: None, **kw))):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_shows_and_queue(self):
        code, out = self.run_cli("shows", "TeaLeafGreen", "--queue")
        self.assertEqual(code, 0, out)
        self.assertIn("2006-10-21  The Vic Theatre, Chicago, IL", out)
        self.assertIn("tlg2006-10-21.sbd.flac16  (+1 other tape)", out)  # the soundboard beats the audience tape
        self.assertIn("2 show(s), 3 tape(s)", out)
        self.assertEqual(sorted(e.identifier for e in Queue().pending()), ["tlg2006-10-21.sbd.flac16", "tlg2006-10-22.flac16"])

    def test_the_dead_are_deadfetchs(self):
        code, out = self.run_cli("shows", "Grateful Dead")
        self.assertEqual(code, 1)
        self.assertIn("deadfetch", out)


if __name__ == "__main__":
    unittest.main()
