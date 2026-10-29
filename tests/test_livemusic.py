import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.livemusic import LiveMusic, _file_title, clean_title, finish, show_date, source_type
from iafetch.livemusic import audio
from tests.support import file, item_json

ETREE = {"mediatype": "etree", "collection": ["TeaLeafGreen", "etree"], "creator": "Tea Leaf Green", "date": "2006-10-21",
         "venue": "The Vic Theatre", "coverage": "Chicago,IL", "source": "(fob) Schoeps MK4v > KC5 > Tascam HD-P2"}


def flac(name, title="", fmt="Flac"):
    return file(name, fmt=fmt, size=500, title=title)


# The shape of a real show: titled tracks, an info .txt, an .ffp, archive.org's MP3 and Ogg copies
SHOW = [flac("tlg2006-10-21d1t01.flac", "(band intro)"), flac("tlg2006-10-21d1t02.flac", "Sex in the 70's"),
        flac("tlg2006-10-21d1t03.flac", "One Reason >"), flac("tlg2006-10-21d2t01.flac", "Flight of the Sea Monkey* >"),
        file("tlg2006-10-21-16bit.txt", fmt="Text"), file("tlg2006-10-21-16bitffp.txt", fmt="Text"),
        file("tlg2006-10-21d1t01.mp3", "derivative", "VBR MP3"), file("tlg2006-10-21d1t01.ogg", "derivative", "Ogg Vorbis"),
        file("tlg2006-10-21.flac16-flac.torrent", fmt="BitTorrent")]
INFO = """Tea Leaf Green
October 21, 2006
The Vic Theatre
Chicago, IL

Source: Schoeps MK4v > KC5

Disc 1
d1t01 Band Intro
d1t02 Sex in the 70's
d1t03 One Reason >

Disc 2
d2t01 Flight of the Sea Monkey
"""


def item(identifier, files, **meta):
    return Item.from_json(identifier, item_json(identifier, files, **{**ETREE, **meta}))


class LiveMusicTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, it, staged=None):
        sel = selection(LiveMusic(), it, {}, self.config)
        return sel, LiveMusic().plan(it, staged or self.root / "_Inbox" / it.identifier, sel, self.config)

    def test_etree_items_get_live_music_and_the_dead_still_go_to_deadfetch(self):
        self.assertEqual(choose(item("x", []), self.config)[0].name, "livemusic")
        self.assertEqual(choose(item("gd77", [], collection=["GratefulDead", "etree"]), self.config)[0].name, "deadtag")

    def test_the_show_with_its_own_titles(self):
        sel, plan = self.plan(item("tlg2006-10-21.mk4v.flac16", SHOW))
        self.assertEqual(sorted(f["name"] for f in sel.files),
                         sorted(f["name"] for f in SHOW[:6]))  # FLAC, the info .txt and the .ffp; no copies or torrents
        self.assertEqual(plan.dest, str((self.root / "Live Music" / "Tea Leaf Green" / "2006" /
                                         "2006-10-21 The Vic Theatre, Chicago, IL [AUD]").resolve()))
        self.assertEqual([m[1] for m in plan.moves if m[1].endswith(".flac")],
                         ["2006-10-21 d1t01 (band intro).flac", "2006-10-21 d1t02 Sex in the 70's.flac",
                          "2006-10-21 d1t03 One Reason.flac", "2006-10-21 d2t01 Flight of the Sea Monkey.flac"])
        self.assertEqual(plan.tags["2006-10-21 d1t03 One Reason.flac"], {"title": "One Reason >", "discnumber": "1", "tracknumber": "3"})
        self.assertIn(["tlg2006-10-21-16bit.txt", "tlg2006-10-21-16bit.txt"], plan.moves)
        self.assertTrue(plan.approved)

    def test_titles_from_the_info_text_when_the_listing_has_none(self):
        untitled = [flac(f["name"]) if f["name"].endswith(".flac") else f for f in SHOW]
        staged = self.root / "_Inbox" / "x"
        staged.mkdir(parents=True)
        (staged / "tlg2006-10-21-16bit.txt").write_text(INFO)
        sel, plan = self.plan(item("x", untitled), staged)
        self.assertTrue(plan.approved, plan.review)
        self.assertEqual([plan.tags[m[1]]["title"] for m in plan.moves if m[1].endswith(".flac")],
                         ["Band Intro", "Sex in the 70's", "One Reason >", "Flight of the Sea Monkey"])

    def test_a_setlist_that_doesnt_match_goes_to_review(self):
        untitled = [flac(f["name"]) if f["name"].endswith(".flac") else f for f in SHOW] + [flac("tlg2006-10-21d2t02.flac")]
        staged = self.root / "_Inbox" / "x"
        staged.mkdir(parents=True)
        (staged / "info.txt").write_text(INFO)
        sel, plan = self.plan(item("x", untitled), staged)
        self.assertFalse(plan.approved)
        self.assertIn("doesn't match the files", plan.review[0])

    def test_formats(self):
        both = [flac("a.flac", "A", "24bit Flac"), flac("b.flac", "A")]
        self.assertEqual([f["name"] for f in selection(LiveMusic(), item("x", both), {}, self.config).files], ["b.flac"])
        prefers24 = parse_config({"root": str(self.root), "livemusic": {"prefer": "24bit"}})
        self.assertEqual([f["name"] for f in selection(LiveMusic(), item("x", both), {}, prefers24).files], ["a.flac"])
        shn = [file("gfc2004-02-12d1t1.shn", fmt="Shorten", title="Groovebagg"), file("gfc2004-02-12d1t1.skt", fmt="Unknown")]
        sel, plan = self.plan(item("gfc2004-02-12", shn, source="Matrix"))
        self.assertEqual([f["name"] for f in sel.files], ["gfc2004-02-12d1t1.shn"])
        self.assertTrue(plan.dest.endswith("[MTX]"))

    def test_names(self):
        self.assertEqual(show_date(item("x", [], date="2006-10-21T00:00:00Z")), "2006-10-21")
        self.assertEqual(show_date(item("jj2008-06-14.mk4", [], date="")), "2008-06-14")
        self.assertEqual(source_type(item("Rad2006-04-21.sbd.flac", [], source="SBD>XLR")), "SBD")
        self.assertEqual(source_type(item("x", [], source="Mix of Jason's audience and Ed's soundboard")), "MTX")
        self.assertEqual(clean_title("Bogo>Up Here"), "Bogo > Up Here")
        self.assertEqual(_file_title("Flight of the Sea Monkey* >"), "Flight of the Sea Monkey")
        self.assertEqual(_file_title("Jack of Diamonds>Make it thru"), "Jack of Diamonds-Make it thru")


def tiny_wav(path: Path, frames: int = 4410) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<hh", i % 300, -(i % 200)) for i in range(frames)))


@unittest.skipUnless(shutil.which("flac"), "the flac program isn't installed")
class ConvertTest(unittest.TestCase):
    def test_a_wav_becomes_flac_losslessly_and_is_checked_and_tagged(self):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("mutagen isn't installed")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            src = dest / '2006-10-21 d1t01 _Intro_.wav'  # filed NAS-safe: the plan says "Intro"
            tiny_wav(src)
            pcm = audio.wav_md5(src)
            (dest / "show.ffp").write_text(f"tlg2006-10-21d1t01.wav:{pcm}\n")
            (dest / ".iafetch" / "x").mkdir(parents=True)
            (dest / ".iafetch" / "x" / "iafetch.json").write_text("{}")
            it = item("x", [file("tlg2006-10-21d1t01.wav", fmt="WAVE", title="Intro"), file("show.ffp", fmt="Flac FingerPrint")])
            sel = selection(LiveMusic(), it, {}, parse_config({"root": tmp}))
            from iafetch.handlers.base import Plan
            rel = '2006-10-21 d1t01 "Intro".wav'
            plan = Plan("x", "livemusic", "", str(dest), moves=[["tlg2006-10-21d1t01.wav", rel], ["show.ffp", "show.ffp"]],
                        tags={rel: {"title": "Intro", "discnumber": "1", "tracknumber": "1"}}, sidecar=".iafetch/x/iafetch.json")
            result = finish(plan, it, sel, dest)
            out = src.with_suffix(".flac")
            self.assertFalse(src.exists())
            self.assertEqual(audio.flac_audio_md5(out), pcm)  # lossless
            self.assertEqual(result, {"problems": [], "checked": 1, "tagged": 1})
            from mutagen.flac import FLAC
            self.assertEqual((FLAC(out)["title"], FLAC(out)["artist"], FLAC(out)["album"]),
                             (["Intro"], ["Tea Leaf Green"], ["2006-10-21 The Vic Theatre, Chicago, IL"]))
            self.assertEqual(json.loads((dest / ".iafetch" / "x" / "iafetch.json").read_text())["checksums_checked"], 1)

    def test_checksum_files(self):
        self.assertEqual(audio.kind_of("x.ffp.txt"), "ffp")
        self.assertEqual(audio.kind_of("tlg2006-10-21-16bitffp.txt"), "ffp")
        self.assertEqual(audio.kind_of("gd75-08-13.md5"), "md5")
        self.assertIsNone(audio.kind_of("info.txt"))
        self.assertEqual(audio.parse_checksums("d1t01.flac:0123456789abcdef0123456789ABCDEF\n", "ffp"),
                         [("d1t01.flac", "0123456789abcdef0123456789abcdef")])


if __name__ == "__main__":
    unittest.main()
