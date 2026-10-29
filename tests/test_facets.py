import unittest

from iafetch.facets import Filters, apply, counts, languages, record, words_match

# Search results shaped like archive.org's, with its real inconsistencies
DOCS = [
    {"identifier": "prg-ref", "title": "Commodore 64 Programmer's Reference Guide", "mediatype": "texts",
     "language": "eng", "subject": ["Commodore 64 (Computer) -- Programming", "machine language"],
     "format": ["Text PDF", "Single Page Processed JP2 ZIP", "EPUB"], "year": "1982",
     "collection": ["commodore_c64_books", "fav-donchaos"], "downloads": 9000, "avg_rating": 4.8, "num_reviews": 5},
    {"identifier": "basic-it", "title": "Manuale del BASIC", "mediatype": "texts", "language": "ita",
     "subject": "basic; programmazione", "format": ["Single Page Processed JP2 ZIP"], "date": "1984-03",
     "collection": ["commodore_c64_books"], "downloads": 300},
    {"identifier": "manual-de", "title": "Commodore C64 Manual: Disk Drive 1541-II (198x)(Commodore)(DE)",
     "mediatype": "texts", "language": "eng", "format": ["Text PDF"], "collection": ["commodore_c64_manuals"],
     "downloads": 50},
    {"identifier": "games-book", "title": "Compute!'s First Book of Commodore 64 Games", "mediatype": "texts",
     "language": "English", "subject": "games", "format": ["Text PDF"], "year": "1983",
     "collection": ["commodore_c64_books"], "downloads": 4000, "licenseurl": "http://creativecommons.org/licenses/by/3.0/"},
    {"identifier": "film", "title": "His Girl Friday (1940)", "mediatype": "movies", "format": ["h.264", "SubRip"],
     "collection": ["feature_films", "fav-someone"], "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
     "downloads": 70000},
]
RECORDS = [record(d) for d in DOCS]
BY = {r.identifier: r for r in RECORDS}


class FacetValuesTest(unittest.TestCase):
    def test_languages_merged_and_region_tags_win(self):
        self.assertEqual(languages("eng"), ({"English"}, False))
        self.assertEqual(languages(["English", "en"]), ({"English"}, False))
        self.assertEqual(languages("ita; eng; ger; fre"), ({"Italian", "English", "German", "French"}, False))
        self.assertEqual(languages(None), ({"(not given)"}, False))
        self.assertEqual(BY["manual-de"].facets["language"], {"German"})  # tagged English, but (DE) in the title
        self.assertTrue(BY["manual-de"].language_guessed)

    def test_decades_formats_collections_licenses(self):
        self.assertEqual(BY["basic-it"].facets["decade"], {"1980s"})  # from the date
        self.assertEqual(BY["film"].facets["decade"], {"1940s"})  # from the title
        self.assertEqual(BY["manual-de"].facets["decade"], {"(no year)"})  # 198x isn't a year
        self.assertEqual(BY["prg-ref"].facets["format"], {"searchable PDF", "PDF", "EPUB", "page scans"})
        self.assertEqual(BY["basic-it"].facets["format"], {"page scans"})
        self.assertEqual(BY["film"].facets["format"], {"MP4 video", "subtitles"})
        self.assertEqual(BY["prg-ref"].facets["collection"], {"commodore_c64_books"})  # no favourites lists
        self.assertEqual(BY["film"].facets["license"], {"public domain"})
        self.assertEqual(BY["games-book"].facets["license"], {"Creative Commons"})
        self.assertEqual(BY["prg-ref"].facets["license"], {"no license declared"})
        self.assertEqual((BY["film"].facets["kind"], BY["prg-ref"].facets["kind"]), ({"video"}, {"books"}))


class FilteringTest(unittest.TestCase):
    def ids(self, f, **kw):
        return [r.identifier for r in apply(RECORDS, f, **kw)]

    def test_include_and_exclude(self):
        f = Filters()
        self.assertEqual(f.cycle("language", "English"), 1)
        self.assertEqual(self.ids(f), ["prg-ref", "games-book"])
        f.cycle("format", "page scans")
        f.cycle("format", "page scans")  # now excluded
        self.assertEqual(f.chosen["format"], {"page scans": -1})
        self.assertEqual(self.ids(f), ["games-book"])
        self.assertEqual(f.cycle("format", "page scans"), 0)  # back to neither
        self.assertEqual(f.summary(), "language +English")

    def test_two_included_values_are_either(self):
        f = Filters()
        f.cycle("language", "English")
        f.cycle("language", "Italian")
        self.assertEqual(self.ids(f), ["prg-ref", "games-book", "basic-it"])

    def test_words(self):
        self.assertTrue(words_match("programming the commodore 64", "+programming -games"))
        self.assertFalse(words_match("first book of commodore 64 games", "-games"))
        self.assertTrue(words_match("machine language for beginners", '"machine language"'))
        self.assertFalse(words_match("machine code", '"machine language"'))
        self.assertTrue(words_match('the "best" basic', '"best'))  # an unclosed quote: words as they are
        f = Filters(words="commodore -games")
        self.assertEqual(self.ids(f), ["prg-ref", "manual-de"])

    def test_hiding_what_you_have(self):
        f = Filters(hide_had=True)
        self.assertNotIn("film", self.ids(f, had={"film"}))

    def test_sorting(self):
        self.assertEqual(self.ids(Filters(), sort="year"), ["film", "prg-ref", "games-book", "basic-it", "manual-de"])
        self.assertEqual(self.ids(Filters(), sort="rating")[0], "prg-ref")

    def test_counts_leave_out_their_own_facet(self):
        f = Filters()
        f.cycle("language", "English")
        c = counts(RECORDS, f, "language")
        self.assertEqual((c["English"], c["Italian"], c["German"]), (2, 1, 1))  # what ticking each would add
        c = counts(RECORDS, f, "format")
        self.assertEqual((c["searchable PDF"], c["page scans"]), (2, 1))  # among the English ones


if __name__ == "__main__":
    unittest.main()
