import unittest

from iafetch.livemusic.titles import capitalize, expansion


class CapitalizeTest(unittest.TestCase):
    def test_lowercase_titles(self):
        for raw, want in [("brown eyed women", "Brown Eyed Women"), ("intro > dancin", "Intro > Dancin"),
                          ("standing on the corner", "Standing on the Corner"),
                          ("tastebud (come back baby)", "Tastebud (Come Back Baby)"),
                          ("it's all over now baby blue", "It's All Over Now Baby Blue"),
                          ("b.i.o.d.t.l.", "B.I.O.D.T.L."), ("the other one", "The Other One")]:
            self.assertEqual(capitalize(raw), want)

    def test_other_titles_are_left_alone(self):
        for title in ("Other ones", "BIODTL", "Beat It On Down the Line", "1/2 Step", ""):
            self.assertEqual(capitalize(title), title)


class ExpansionTest(unittest.TestCase):
    def test_abbreviations(self):
        for short, full in [("BIODTL", "Beat It On Down the Line"), ("b.i.o.d.t.l.", "Beat It On Down the Line"),
                            ("GDTRFB", "Goin' Down the Road Feeling Bad"), ("NFA", "Not Fade Away"),
                            ("LL Rain", "Looks Like Rain"), ("Big R&R Blues", "Big Railroad Blues"),
                            ("sugar mag", "Sugar Magnolia"),
                            ("China Cat > IKYR", "China Cat Sunflower > I Know You Rider")]:
            self.assertEqual(expansion(short, full), full)

    def test_archive_markers_are_stripped(self):
        self.assertEqual(expansion("NFA", "E: Not Fade Away ->"), "Not Fade Away")

    def test_not_abbreviations(self):
        for short, full in [("other ones", "The other one"), ("Women Are Smarter", "Man Smart, Woman Smarter"),
                            ("Bertha", "E: Bertha"), ("Jam", "Jam Out"), ("Drums", "Drums / Space"),
                            ("1/2 Step", "Mississippi Half Step"), ("NFA", None)]:
            self.assertIsNone(expansion(short, full), short)


if __name__ == "__main__":
    unittest.main()
