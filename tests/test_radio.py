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


class PodcastTest(unittest.TestCase):
    """The shapes of real podcast and spoken-word items."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, identifier, files, **meta):
        from iafetch.handlers.radio import Podcast
        meta = {"mediatype": "audio", "collection": ["podcasts_compilations", "podcasts"], **meta}
        it = Item.from_json(identifier, item_json(identifier, files, **meta))
        h = choose(it, self.config)[0]
        self.assertIsInstance(h, Podcast)
        return h.plan(it, self.root / "x", selection(h, it, {}, self.config), self.config)

    def moves(self, plan):
        return [m[1] for m in plan.moves if not m[1].startswith(".iafetch") and m[1] != "cover.jpg"]

    def test_dated_episodes_day_month_year(self):
        plan = self.plan("elpodcastdelbuho", [mp3("EPDB001-El_Muro_de_Berlin_14_11_2009.mp3", "1. El Muro de Berlín"),
                                              mp3("EPDB002-Bob_Marley_21_11_2009.mp3", "2. Bob Marley"),
                                              mp3("Promo_de_El_Podcast_del_Buho.mp3", "Promo")],
                         title="El Podcast del Búho")
        self.assertEqual(plan.dest, str((self.root / "Podcasts" / "El Podcast del Búho").resolve()))
        self.assertEqual(self.moves(plan), ["2009/El Podcast del Búho 2009-11-14 (001) El Muro de Berlín.mp3",
                                            "2009/El Podcast del Búho 2009-11-21 (002) Bob Marley.mp3",
                                            "Extras/Promo de El Podcast del Buho.mp3"])  # the tag reads as cut off

    def test_numbered_episodes_sit_in_the_shows_folder(self):
        plan = self.plan("rr12016", [mp3("CaseClosed472.mp3", "CC472: Barrie Craig", "Case Closed!"),
                                     mp3("CaseClosed473.mp3", "CC473: The Crime Club", "Case Closed!")], title="rr12016")
        self.assertTrue(plan.dest.endswith("Podcasts/Case Closed!"))  # the title is only the identifier: the album tag
        self.assertEqual(self.moves(plan), ["Case Closed! (472) Barrie Craig.mp3", "Case Closed! (473) The Crime Club.mp3"])

    def test_a_wav_upload_gets_archive_orgs_mp3(self):
        files = [file("MLKDream.wav", fmt="WAVE", size=900, title="I Have a Dream"),
                 mp3("MLKDream.mp3", "I Have a Dream", source="derivative", fmt="VBR MP3"),
                 mp3("MLKDream_64kb.mp3", "I Have a Dream", source="derivative", fmt="64Kbps MP3")]
        files[1]["original"] = files[2]["original"] = "MLKDream.wav"
        plan = self.plan("MLKDream", files, title="I Have a Dream, Martin Luther King Jr.")
        self.assertEqual([m[0] for m in plan.moves if m[0].endswith((".mp3", ".wav"))], ["MLKDream.mp3"])

    def test_spoken_word(self):
        plan = self.plan("TvQuran.com__Maher", [mp3("001.mp3", "Al-Fatihah"), mp3("002.mp3", "Al-Baqarah")],
                         title="Maher Al-Muaiqly- TvQuran.com", collection=["audio_islamic", "audio_sermons", "audio_religion"])
        self.assertEqual(plan.dest, str((self.root / "Spoken Word" / "Maher Al-Muaiqly").resolve()))
        self.assertEqual(self.moves(plan), ["Maher Al-Muaiqly (001) Al-Fatihah.mp3", "Maher Al-Muaiqly (002) Al-Baqarah.mp3"])

    def test_a_folder_names_the_show(self):
        plan = self.plan("englishpod_B0127pb", [mp3("English Podcast Elementary/englishpod_0149pb.mp3")],
                         title="englishpod_B0127pb")
        self.assertTrue(plan.dest.endswith("Podcasts/English Podcast Elementary"))
        self.assertEqual(self.moves(plan)[0][:34], "English Podcast Elementary (149) E")
