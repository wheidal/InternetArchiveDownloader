import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.video import Video, episode, pick, runtime, series_name
from tests.support import file, item_json

# The real shapes of archive.org video items, trimmed
ABOUT_BANANAS = [  # one film, five versions
    file("AboutBan1935.avi", fmt="Cinepack", size=3670), file("AboutBan1935.mpeg", fmt="MPEG2", size=29240),
    file("AboutBan1935.mp4", "derivative", "h.264", size=6530, original="AboutBan1935.mpeg"),
    file("AboutBan1935.ogv", "derivative", "Ogg Video", size=4430, original="AboutBan1935.mpeg"),
    file("AboutBan1935_512kb.mp4", "derivative", "512Kb MPEG4", size=4380, original="AboutBan1935.mpeg"),
    file("AboutBan1935_256kb.rm", fmt="256Kb Real Media", size=11070),
    file("AboutBan1935_edit.mp4", fmt="HiRes MPEG4", size=20180, length="642.5"),
    file("AboutBan1935.gif", "derivative", "Animated GIF"), file("AboutBan1935.asr.srt", fmt="SubRip", size=0),
    file("__ia_thumb.jpg", fmt="Item Tile"),
]
SHOGUN = [file(f"Shogun {n}.avi", fmt="Cinepack", size=8000) for n in range(1, 5)] + \
         [file(f"Shogun {n}.mp4", "derivative", "h.264", size=3000, original=f"Shogun {n}.avi") for n in range(1, 5)]
GET_SMART = [file("Get Smart S01E01 (Mr. Big).mp4", fmt="MPEG4", size=900),
             file("Get Smart S01E01 (Mr. Big).ia.mp4", "derivative", "h.264 IA", size=800,
                  original="Get Smart S01E01 (Mr. Big).mp4"),
             file("Get Smart S01E02 (Diplomat's Daughter).mp4", fmt="MPEG4", size=900),
             file("Get Smart S01E02 (Diplomat's Daughter).ia.mp4", "derivative", "h.264 IA", size=800,
                  original="Get Smart S01E02 (Diplomat's Daughter).mp4")]
BONANZA = [file("Bonanza s01e19.mp4", fmt="MPEG4"), file("Bonanza s01e19.vtt", fmt="Web Video Text Tracks", size=50),
           file("Bonanza s01e19.txt", fmt="Text", size=50), file("Bonanza s01e20.mp4", fmt="MPEG4"),
           file("Bonanza s01e20.ogv", "derivative", "Ogg Video", original="Bonanza s01e20.mp4")]


def item(identifier, files, **meta):
    return Item.from_json(identifier, item_json(identifier, files, **meta))


def names(sel):
    return [f["name"] for f in sel.files]


class PickTest(unittest.TestCase):
    def test_one_film_of_five_versions(self):
        it = item("AboutBan1935", ABOUT_BANANAS)
        self.assertEqual(Video().kind(it), "movies")
        self.assertEqual(names(selection(Video(), it, {})), ["AboutBan1935_edit.mp4"])  # the uploaded MP4

    def test_quality_settings(self):
        group = [f for f in ABOUT_BANANAS if f["name"].endswith((".avi", ".mpeg", ".mp4", ".ogv", ".rm"))]
        self.assertEqual(pick(group, "original")["name"], "AboutBan1935.mpeg")
        self.assertEqual(pick(group, "smallest")["name"], "AboutBan1935_512kb.mp4")
        no_mp4_original = [f for f in group if f["name"] != "AboutBan1935_edit.mp4"]
        self.assertEqual(pick(no_mp4_original, "h264")["name"], "AboutBan1935.mp4")  # archive.org's h.264

    def test_the_uploaded_mp4_over_archive_orgs_reencode(self):
        it = item("get-smart", GET_SMART)
        self.assertEqual(names(selection(Video(), it, {})),
                         ["Get Smart S01E01 (Mr. Big).mp4", "Get Smart S01E02 (Diplomat's Daughter).mp4"])

    def test_avi_originals_get_their_h264(self):
        it = item("Shogun_Miniseries", SHOGUN, title="Shōgun (miniseries)", date="1980")
        self.assertEqual(Video().kind(it), "tv")
        self.assertEqual(names(selection(Video(), it, {})), [f"Shogun {n}.mp4" for n in range(1, 5)])

    def test_subtitles_come_along_and_text_does_not(self):
        sel = selection(Video(), item("Bonanza_pd", BONANZA), {})
        self.assertEqual(names(sel), ["Bonanza s01e19.mp4", "Bonanza s01e19.vtt", "Bonanza s01e20.mp4"])

    def test_named_formats_are_taken_as_asked(self):
        sel = selection(Video(), item("AboutBan1935", ABOUT_BANANAS), {"formats": ["Ogg Video"]})
        self.assertEqual(names(sel), ["AboutBan1935.ogv"])

    def test_movies_go_to_video_unless_a_collection_says_otherwise(self):
        config = parse_config({"handlers": {"prelinger": "generic"}})
        self.assertEqual(choose(item("x", []), parse_config({}))[0].name, "video")
        self.assertEqual(choose(item("x", []), config)[0].name, "generic")


class NamesTest(unittest.TestCase):
    def test_episodes(self):
        cases = {
            "s01e01_EntertheLoneRanger.mp4": (1, 1, "Enterthe Lone Ranger"),
            "s01e03TheLoneRangersTriumph.mp4": (1, 3, "The Lone Rangers Triumph"),
            "Bonanza s01e19.mp4": (1, 19, ""),
            "BonanzaS02e02.mp4": (2, 2, ""),
            "Get Smart S01E05 (Now You See Him... Now You Don't).mp4": (1, 5, "Now You See Him... Now You Don't"),
            "Get Smart S02E10 (Mr. Big).ia.mp4": (2, 10, "Mr. Big"),
            "Show 2x05 The Title.mp4": (2, 5, "The Title"),
            "Shogun 3.mp4": (1, 3, ""),
            "Part 2.mp4": (1, 2, ""),
            "S01/Green Acres S01E02 - Lisa's First Day.mp4": (1, 2, "Lisa's First Day"),
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(episode(name, "Green Acres"), want)
        self.assertIsNone(episode("Interview.mp4"))

    def test_series_names(self):
        for title, want in (("The Lone Ranger TV Show", "The Lone Ranger"), ("Bonanza public domain episodes", "Bonanza"),
                            ("Shōgun (miniseries)", "Shōgun"), ("Green Acres Complete Series", "Green Acres"),
                            ("Get Smart", "Get Smart"), ("Batman: The Movie Serial", "Batman - The Movie Serial")):
            self.assertEqual(series_name(item("x", [], title=title)), want)

    def test_runtime(self):
        self.assertEqual(runtime({"length": "1519.65"}), 25)
        self.assertEqual(runtime({"length": "1:02:40"}), 63)
        self.assertIsNone(runtime({"length": "?"}))
        self.assertIsNone(runtime(None))


class PlanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, it):
        sel = selection(Video(), it, {}, self.config)
        return Video().plan(it, self.root / "_Inbox" / it.identifier, sel, self.config)

    def test_a_film(self):
        it = item("AboutBan1935", ABOUT_BANANAS, title="About Bananas",
                  description="<div>Banana <b>plantations</b> &amp; shipping.</div>", creator="Castle Films")
        plan = self.plan(it)
        self.assertTrue(plan.approved, plan.review)
        self.assertEqual(plan.dest, str((self.root / "Movies" / "About Bananas (1935)").resolve()))
        self.assertIn(["AboutBan1935_edit.mp4", "About Bananas (1935).mp4"], plan.moves)
        self.assertIn(["__ia_thumb.jpg", "poster.jpg"], plan.moves)
        self.assertIn(["AboutBan1935_meta.xml", ".iafetch/AboutBan1935/AboutBan1935_meta.xml"], plan.moves)
        nfo = plan.writes["movie.nfo"]
        for part in ("<title>About Bananas</title>", "<year>1935</year>", "<plot>Banana plantations &amp; shipping.</plot>",
                     "<studio>Castle Films</studio>", "<runtime>11</runtime>",
                     '<uniqueid type="archive.org" default="true">AboutBan1935</uniqueid>'):
            self.assertIn(part, nfo)

    def test_a_film_with_subtitles_in_two_languages(self):
        it = item("f", [file("f.mp4", fmt="MPEG4"), file("f.en.srt", fmt="SubRip", size=40),
                        file("f.fr.srt", fmt="SubRip", size=40)], title="F")
        moves = dict(self.plan(it).moves)
        self.assertEqual((moves["f.en.srt"], moves["f.fr.srt"]), ("F (1935).en.srt", "F (1935).fr.srt"))

    def test_a_series(self):
        plan = self.plan(item("get-smart", GET_SMART, title="Get Smart", date=""))
        self.assertTrue(plan.approved, plan.review)
        self.assertEqual(plan.dest, str((self.root / "TV" / "Get Smart").resolve()))
        self.assertIn(["Get Smart S01E01 (Mr. Big).mp4", "Season 01/Get Smart S01E01 - Mr. Big.mp4"], plan.moves)
        self.assertIn("<title>Get Smart</title>", plan.writes["tvshow.nfo"])

    def test_episode_subtitles_follow_their_episode(self):
        plan = self.plan(item("Bonanza_pd", BONANZA, title="Bonanza public domain episodes"))
        self.assertIn(["Bonanza s01e19.vtt", "Season 01/Bonanza S01E19.vtt"], plan.moves)

    def test_a_number_twice_needs_review(self):
        files = [file("Show S01E01.mp4", fmt="MPEG4"), file("Show S01E01 again.mp4", fmt="MPEG4")]
        plan = self.plan(item("show", files, title="Show"))
        self.assertFalse(plan.approved)
        self.assertIn("S01E01 is more than one file", plan.review[0])

    def test_an_unnumbered_episode_needs_review(self):
        files = [file("Show S01E01.mp4", fmt="MPEG4"), file("Show S01E02.mp4", fmt="MPEG4"),
                 file("Behind the scenes.mp4", fmt="MPEG4")]
        plan = self.plan(item("show", files, title="Show"))
        self.assertIn("no episode number in Behind the scenes.mp4", plan.review)

    def test_a_second_item_adds_to_a_series(self):
        series = self.root / "TV" / "Bonanza"
        (series / "Season 01").mkdir(parents=True)
        (series / "poster.jpg").write_bytes(b"x")
        (series / "Season 01" / "Bonanza S01E02.mp4").write_bytes(b"x")  # from another item
        plan = self.plan(item("Bonanza_pd", BONANZA, title="Bonanza public domain episodes"))
        self.assertTrue(plan.approved, plan.review)
        self.assertNotIn("poster.jpg", [m[1] for m in plan.moves])
        (series / "Season 01" / "Bonanza S01E19.mp4").write_bytes(b"x")
        plan = self.plan(item("Bonanza_pd", BONANZA, title="Bonanza public domain episodes"))
        self.assertIn("Season 01/Bonanza S01E19.mp4 is already in", plan.review[0])


if __name__ == "__main__":
    unittest.main()


class FilmTitleTest(unittest.TestCase):
    def test_titles(self):
        from iafetch.handlers.video import film_title
        for title, date, want in (
                ("Der schweigende Stern (1960)", "1960", "Der schweigende Stern"),
                ("First Spaceship on Venus, The (Schweigende Stern, Der)", "1960",
                 "The First Spaceship on Venus (Der Schweigende Stern)"),
                ("In The Dust Of The Stars (English subs, HD)", "1976", "In The Dust Of The Stars"),
                ("Metropolis [1080p] (1927)", "1927", "Metropolis"),
                ("Reefer Madness", "1936", "Reefer Madness"),
                ("Batman: The Movie", "1966", "Batman - The Movie")):
            self.assertEqual(film_title(item("x", [], title=title, date=date)), want)

    def test_an_ipod_copy_is_the_same_film(self):  # the_first_spaceship_on_venus
        files = [file("Venus.mp4", fmt="MPEG4", size=2000), file("Venus.asr.srt", fmt="SubRip", size=48),
                 file("Venus_512kb.mp4", "derivative", "512Kb MPEG4", size=300, original="Venus.mp4"),
                 file("Venus_ipod.mp4", fmt="MPEG4", size=900), file("Venus_ipod.asr.srt", fmt="SubRip", size=49),
                 file("Venus.flv", fmt="Flash Video", size=160)]
        it = item("venus", files)
        self.assertEqual(Video().kind(it), "movies")
        self.assertEqual(names(selection(Video(), it, {})), ["Venus.mp4", "Venus_ipod.asr.srt"])

    def test_several_unnumbered_videos_are_a_film_for_review(self):
        it = item("x", [file("Feature.mp4", fmt="MPEG4"), file("Trailer.mp4", fmt="MPEG4")], title="Feature")
        self.assertEqual(Video().kind(it), "movies")
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            plan = Video().plan(it, Path(tmp) / "_Inbox" / "x", selection(Video(), it, {}, config), config)
        self.assertIn("2 videos where one film was expected", plan.review)
