import json
import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.radio import Radio, episode, parse_ranges, series_name, tag_episodes
from tests.support import file, item_json

OTR = {"mediatype": "audio", "collection": ["oldtimeradio", "radioprograms"]}


def mp3(name, title="", album="", fmt="VBR MP3", source="original"):
    return file(name, source, fmt, size=400, title=title, album=album)


# The naming styles of real items, as archive.org lists them
GUNSMOKE = [mp3("Gunsmoke 52-04-26 (001) Billy the Kid.mp3", "Billy the Kid", "04/26/52, episode 1"),
            mp3("Gunsmoke 52-05-03 (002) Ben Thompson (act 1).mp3", "Ben Thompson", "05/03/52, episode 2"),
            mp3("Gunsmoke_58-11-02_343_Old_Mans_Gold.MP3", "Old Man's Gold", "11/02/58, episode 343"),
            mp3("Howard McNear Biography.mp3", "Howard McNear Biography", "Biography"),
            mp3("Gunsmoke 52-04-26 (001) Billy the Kid.ogg", fmt="Ogg Vorbis", source="derivative"),
            file("Gunsmoke 52-04-26 (001) Billy the Kid.txt", fmt="Text"),
            file("Gunsmoke 52-04-26 (001) Billy the Kid.srt", "derivative", "SubRip")]


def item(identifier, files, **meta):
    return Item.from_json(identifier, item_json(identifier, files, **{**OTR, **meta}))


class EpisodeTest(unittest.TestCase):
    def ep(self, series, **f):
        e = episode({"name": f.pop("name"), **f}, series)
        return (e.date, e.number, e.title)

    def test_naming_styles(self):
        self.assertEqual(self.ep("Gunsmoke", name="Gunsmoke 52-04-26 (001) Billy the Kid.mp3", title="Billy the Kid"),
                         ("1952-04-26", 1, "Billy the Kid"))
        # the name keeps "(act 1)"; the tag's punctuation wins when they agree
        self.assertEqual(self.ep("Gunsmoke", name="Gunsmoke 52-05-03 (002) Ben Thompson (act 1).mp3", title="Ben Thompson")[2],
                         "Ben Thompson (act 1)")
        self.assertEqual(self.ep("Gunsmoke", name="Gunsmoke_58-11-02_343_Old_Mans_Gold.MP3", title="Old Man's Gold"),
                         ("1958-11-02", 343, "Old Man's Gold"))
        # a tag cut off at 40 characters, with the date run into it: the name has the whole title
        self.assertEqual(self.ep("Yours Truly, Johnny Dollar", name="YTJD 1948-12-07 000 Milford Brooks III (Dick Powell Audition).mp3",
                                 title="481207 000 Milford Brooks III (Dick Powe"),
                         ("1948-12-07", 0, "Milford Brooks III (Dick Powell Audition)"))
        self.assertEqual(self.ep("Yours Truly, Johnny Dollar", name="ytjd 1961-07-16 747 the old fashioned murder matter.mp3")[2],
                         "The Old Fashioned Murder Matter")
        self.assertEqual(self.ep("Dragnet", name="Dragnet_49-06-17_ep003_The_Werewolf.mp3"), ("1949-06-17", 3, "The Werewolf"))
        # run together; an unknown number "(xxx)" gives way to the album tag's
        self.assertEqual(self.ep("X Minus One", name="XMinusOne55-04-24001NoContact.mp3",
                                 title="X Minus One 55-04-24 (001) No Contact", album="04/24/55, episode 1"),
                         ("1955-04-24", 1, "No Contact"))
        self.assertEqual(self.ep("X Minus One", name="XMinusOne73-01-27xxxTheIronChancellor.mp3",
                                 title="X Minus One 73-01-27 (xxx) The Iron Ch", album="01/27/73, episode 126"),
                         ("1973-01-27", 126, "The Iron Chancellor"))
        self.assertEqual(self.ep("Gunsmoke", name="Howard McNear Biography.mp3", title="Howard McNear Biography"),
                         ("", None, "Howard McNear Biography"))

    def test_series_names(self):
        for title, want in (("Gunsmoke - Single Episodes", "Gunsmoke"), ("Dragnet the 50's radio show", "Dragnet"),
                            ("Big Story 1952-04-09 Printed Word Can Ruin a Life", "Big Story"),
                            ("The Shadow (OTR)", "The Shadow"), ("Yours Truly, Johnny Dollar - Single Episodes",
                                                                 "Yours Truly, Johnny Dollar")):
            self.assertEqual(series_name(item("x", [], title=title)), want)

    def test_ranges(self):
        self.assertEqual(parse_ranges("1-3,7"), {1, 2, 3, 7})
        self.assertIsNone(parse_ranges(""))
        with self.assertRaises(ValueError):
            parse_ranges("one")


class RadioPlanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})
        self.item = item("OTRR_Gunsmoke_Singles", GUNSMOKE, title="Gunsmoke - Single Episodes")

    def test_radio_items_get_the_radio_handler(self):
        self.assertEqual(choose(self.item, self.config)[0].name, "radio")

    def test_the_episodes_only(self):
        sel = selection(Radio(), self.item, {}, self.config)
        self.assertEqual([f["name"] for f in sel.files], ["Gunsmoke 52-04-26 (001) Billy the Kid.mp3",
                                                          "Gunsmoke 52-05-03 (002) Ben Thompson (act 1).mp3",
                                                          "Gunsmoke_58-11-02_343_Old_Mans_Gold.MP3",
                                                          "Howard McNear Biography.mp3"])  # no Ogg, transcripts or subtitles

    def test_choosing_episodes(self):
        names = lambda opts: [f["name"][:24] for f in selection(Radio(), self.item, opts, self.config).files]  # noqa: E731
        self.assertEqual(names({"episodes": "1-2"}), ["Gunsmoke 52-04-26 (001) ", "Gunsmoke 52-05-03 (002) "])
        self.assertEqual(names({"years": "1958"}), ["Gunsmoke_58-11-02_343_Ol"])
        with self.assertRaises(ValueError):  # not for other kinds of item
            selection(choose(Item.from_json("f", item_json("f", [])), self.config)[0], Item.from_json("f", item_json("f", [])),
                      {"episodes": "1"}, self.config)

    def test_plan(self):
        sel = selection(Radio(), self.item, {}, self.config)
        plan = Radio().plan(self.item, self.root / "_Inbox" / "x", sel, self.config)
        self.assertEqual(plan.dest, str((self.root / "Radio" / "Gunsmoke").resolve()))
        self.assertEqual([m[1] for m in plan.moves if not m[1].startswith(".iafetch")],
                         ["1952/Gunsmoke 1952-04-26 (001) Billy the Kid.mp3", "1952/Gunsmoke 1952-05-03 (002) Ben Thompson (act 1).mp3",
                          "1958/Gunsmoke 1958-11-02 (343) Old Man's Gold.mp3", "Extras/Howard McNear Biography.mp3"])
        self.assertTrue(plan.approved)


def tiny_mp3() -> bytes:
    """A few silent MPEG-1 Layer III frames: enough for mutagen to read and tag."""
    frame = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)  # 128 kbit/s, 44.1 kHz: 417 bytes a frame
    return frame * 20


class TaggingTest(unittest.TestCase):
    def test_episodes_are_tagged_and_recorded(self):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("mutagen isn't installed (pip install -e '.[audio]')")
        from mutagen import File as AudioFile
        from iafetch.handlers.base import Plan
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "1952").mkdir()
            (dest / "1952" / "ep.mp3").write_bytes(tiny_mp3())
            (dest / ".iafetch" / "g").mkdir(parents=True)
            (dest / ".iafetch" / "g" / "iafetch.json").write_text(json.dumps({"identifier": "g", "processed": {}}))
            it = item("g", GUNSMOKE, title="Gunsmoke - Single Episodes")
            sel = selection(Radio(), it, {"episodes": "1"}, parse_config({"root": tmp}))
            plan = Plan("g", "radio", "", str(dest), moves=[[sel.files[0]["name"], "1952/ep.mp3"]], sidecar=".iafetch/g/iafetch.json")
            self.assertEqual(tag_episodes(plan, it, sel, dest), 1)
            tags = AudioFile(dest / "1952" / "ep.mp3", easy=True)
            self.assertEqual((tags["album"], tags["title"], tags["date"], tags["tracknumber"]),
                             (["Gunsmoke"], ["Billy the Kid"], ["1952-04-26"], ["1"]))
            record = json.loads((dest / ".iafetch" / "g" / "iafetch.json").read_text())
            self.assertEqual(list(record["processed"]), ["1952/ep.mp3"])  # the tagged file's MD5, for verify


if __name__ == "__main__":
    unittest.main()
