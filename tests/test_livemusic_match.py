import unittest

from functools import partial

from iafetch.livemusic.infotxt import parse_info_text as _parse

parse_info_text = partial(_parse, band="Grateful Dead")
from iafetch.livemusic.match import build_audio_files, match_titles

# Modeled on gd1972-04-26.12884.txt: Tennessee Jed was cut in two where disc 3 ran out, and the .txt lists
# it once, at the top of disc 4.
SPLIT = """Grateful Dead
04/26/72
Jahrhundert Halle

disc 3
01. Sugar Magnolia
02. El Paso

disc 4
01. Tennessee Jed
02. Greatest Story Ever Told
03. One More Saturday Night
"""
FILES = ["gd1972-04-26d3t01.shn", "gd1972-04-26d3t02.shn", "gd1972-04-26d3t03.shn",
         "gd1972-04-26d4t01.shn", "gd1972-04-26d4t02.shn", "gd1972-04-26d4t03.shn"]


def files(archive=None, lengths=("3:35.28", "4:47.62", "2:20.50", "3:52.68", "2:54.45", "3:32.81")):
    out = build_audio_files([(n, 1) for n in FILES])
    for f, title, length in zip(out, archive or [None] * len(out), lengths):
        f.archive_title, f.length = title, length
    return out


class DiscBreakTest(unittest.TestCase):
    def test_split_song_with_archive_org_agreeing(self):
        m = match_titles(files(["Sugar Magnolia", "El Paso", "Tennessee Jed", "Tennessee Jed", "Greatest Story",
                                "One More Saturday Night"]), parse_info_text(SPLIT).titles)
        self.assertTrue(m.clean)
        self.assertEqual([(t.disc, t.disc_track, t.title.title) for t in m.tracks][2:4],
                         [(1, 3, "Tennessee Jed (start)"), (2, 1, "Tennessee Jed (end)")])  # the two discs present
        self.assertIn("split at the disc break", m.resolved[0])
        self.assertEqual(m.unchecked, set())  # archive.org's disc 4 titles line up: still checked

    def test_split_song_in_a_txt_labeled_like_the_files(self):
        labeled = SPLIT.replace("disc 3\n01. ", "d3t01 - ").replace("02. El", "d3t02 - El") \
            .replace("disc 4\n01. ", "d4t01 - ").replace("02. Greatest", "d4t02 - Greatest") \
            .replace("03. One", "d4t03 - One")
        titles = parse_info_text(labeled).titles
        self.assertTrue(all(t.label for t in titles))
        m = match_titles(files(["Sugar Magnolia", "El Paso", "Tennessee Jed", "Tennessee Jed", "Greatest Story",
                                "One More Saturday Night"]), titles)
        self.assertTrue(m.clean, m.notes)
        self.assertEqual([t.title.title for t in m.tracks][2:4], ["Tennessee Jed (start)", "Tennessee Jed (end)"])

    def test_archive_org_one_place_behind_on_the_next_disc(self):
        m = match_titles(files(["Sugar Magnolia", "El Paso", "Tennessee Jed", "Greatest Story Ever Told",
                                "One More Saturday Night", None]), parse_info_text(SPLIT).titles)
        self.assertTrue(m.clean)
        self.assertEqual(len(m.unchecked), 3)  # disc 4 isn't held against archive.org's shifted titles
        self.assertIn("one place behind", m.resolved[1])

    def test_without_evidence_it_only_suggests(self):
        m = match_titles(files(), parse_info_text(SPLIT).titles)
        self.assertFalse(m.clean)
        self.assertEqual(m.resolved, [])
        self.assertIn('may be the start of \'Tennessee Jed\' split at the disc break: if so, title it '
                      '"Tennessee Jed (start)"', m.notes[0])
        self.assertIn("gd1972-04-26d3t03.shn (2:20)", m.notes[0])


if __name__ == "__main__":
    unittest.main()
