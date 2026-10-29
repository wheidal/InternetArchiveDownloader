import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.texts import Texts, author_of, book_title
from tests.support import file, item_json

# The real shapes of three C64 books on archive.org
TOSEC_MANUAL = [file("Guide.pdf", fmt="Text PDF", size=4410), file("Guide.epub", "derivative", "EPUB", size=250),
                file("Guide.djvu", "derivative", "DjVu"), file("Guide_djvu.txt", "derivative", "DjVuTXT"),
                file("Guide_jp2.zip", "derivative", "Single Page Processed JP2 ZIP", size=11150),
                file("Guide_abbyy.gz", "derivative", "Abbyy GZ"), file("__ia_thumb.jpg", fmt="Item Tile")]
SCAN_ONLY_UPLOAD = [file("Image.merged.pdf", fmt="Image Container PDF", size=17340),
                    file("Image.merged_text.pdf", "derivative", "Additional Text PDF", size=4680,
                         original="Image.merged_jp2.zip"),  # as archive.org lists it: made from the scans
                    file("Image.merged.epub", "derivative", "EPUB", size=4740, original="Image.merged.pdf")]


def item(files, **meta):
    return Item.from_json("book", item_json("book", files, mediatype="texts", **meta))


def names(sel):
    return [f["name"] for f in sel.files]


class TextsTest(unittest.TestCase):
    def test_texts_get_the_texts_handler(self):
        self.assertEqual(choose(item([]), parse_config({}))[0].name, "texts")

    def test_the_searchable_pdf_not_the_scans(self):
        self.assertEqual(names(selection(Texts(), item(TOSEC_MANUAL), {})), ["Guide.pdf"])

    def test_a_scan_only_upload_gets_archive_orgs_text_pdf(self):
        self.assertEqual(names(selection(Texts(), item(SCAN_ONLY_UPLOAD), {})), ["Image.merged_text.pdf"])

    def test_epub_when_asked(self):
        config = parse_config({"texts": {"formats": ["pdf", "epub"]}})
        self.assertEqual(names(selection(Texts(), item(TOSEC_MANUAL), {}, config)), ["Guide.epub", "Guide.pdf"])

    def test_titles_and_authors(self):
        tosec = item([], title="Commodore C64 Manual: Commodore 64 Programmers Reference Guide (1983)(Commodore)",
                     date="1983")
        self.assertEqual(book_title(tosec), "Commodore C64 Manual - Commodore 64 Programmers Reference Guide")
        self.assertEqual(author_of(tosec), "Commodore")
        long = item([], title="C64 for the beginning beginner : an easy and helpful introduction", creator=["Jones, Ann"])
        self.assertEqual(book_title(long), "C64 for the beginning beginner")
        self.assertEqual(author_of(long), "Jones, Ann")
        self.assertEqual(author_of(item([], title="Anon")), "Unknown")
        self.assertEqual(author_of(item([], title="X", publisher="Compute! Books")), "Compute! Books")

    def test_plan_for_calibre(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            it = item(TOSEC_MANUAL, title="Programmers Reference Guide (1983)(Commodore)", date="1983",
                      language="eng", subject=["c64; programming"], isbn=["0672220563"])
            sel = selection(Texts(), it, {}, config)
            plan = Texts().plan(it, Path(tmp) / "_Inbox" / "book", sel, config)
            self.assertTrue(plan.approved, plan.review)
            self.assertEqual(plan.dest, str((Path(tmp) / "Books" / "Commodore" / "Programmers Reference Guide (1983)").resolve()))
            self.assertIn(["Guide.pdf", "Programmers Reference Guide (1983).pdf"], plan.moves)
            self.assertIn(["__ia_thumb.jpg", "cover.jpg"], plan.moves)
            opf = plan.writes["metadata.opf"]
            for part in ("<dc:title>Programmers Reference Guide</dc:title>", 'opf:role="aut">Commodore</dc:creator>',
                         "<dc:subject>programming</dc:subject>", 'opf:scheme="ISBN">0672220563</dc:identifier>',
                         '<reference type="cover" title="Cover" href="cover.jpg"/>', "<dc:language>eng</dc:language>"):
                self.assertIn(part, opf)
            import xml.dom.minidom
            xml.dom.minidom.parseString(opf)  # well-formed

    def test_a_second_scan_of_the_same_book_is_kept_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            it = item(TOSEC_MANUAL, title="Guide", date="1983", creator=["Commodore"])
            (Path(tmp) / "Books" / "Commodore" / "Guide (1983)").mkdir(parents=True)
            plan = Texts().plan(it, Path(tmp) / "x", selection(Texts(), it, {}, config), config)
            self.assertTrue(plan.approved, plan.review)
            self.assertTrue(plan.dest.endswith("Guide (1983) [book]"))

    def test_nothing_readable_goes_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            it = item([file("scans_jp2.zip", fmt="Single Page Processed JP2 ZIP")])
            plan = Texts().plan(it, Path(tmp) / "x", selection(Texts(), it, {}, config), config)
            self.assertIn("no PDF or EPUB", plan.review[0])


if __name__ == "__main__":
    unittest.main()


class ManualTitleTest(unittest.TestCase):
    def test_base_titles_match_game_and_manual(self):
        from iafetch.handlers.texts import base_title
        self.assertEqual(base_title("Commodore C64 Manual: Impossible Mission (1984)(Epyx)[a]"), "impossible mission")
        self.assertEqual(base_title("Commodore 64: Impossible Mission (Clean)"), "impossible mission")
        self.assertEqual(base_title("Commodore C64 Manual: Last Ninja, The (1987)(System 3)"), "last ninja")
        self.assertEqual(base_title("The Last Ninja (Side A)"), "last ninja")
        self.assertEqual(base_title("Ghosts 'n Goblins"), "ghosts n goblins")


class MagazineTest(unittest.TestCase):
    """The shapes of real magazine items: ZZap!64, 64'er, Compute!."""

    def setUp(self):
        from iafetch.api import Archive
        from iafetch.net import Client
        from tests.support import FakeArchive
        self.fake = fake = FakeArchive()
        for ident, title, parents in (
                ("zzap64-magazine", "ZZap!64 Magazine", ["commodoremagazines", "computermagazines", "magazine_rack"]),
                ("commodoremagazines", "Commodore Computer Periodicals", ["computermagazines", "magazine_rack"]),
                ("computermagazines", "Computer Magazines", ["magazine_rack"]),
                ("64er_magazine", "64'er Magazine", ["commodoremagazines", "computermagazines", "magazine_rack"])):
            fake.add(ident, [], mediatype="collection", title=title, collection=parents)
        self.archive = Archive(Client(1.0, 1.0, get=fake.get, opener=fake.open, sleep=lambda s: None))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, identifier, **meta):
        files = [file(f"{identifier}.pdf", fmt="Text PDF"), file(f"{identifier}_jp2.zip", "derivative", "Single Page Processed JP2 ZIP"),
                 file(f"{identifier}_lcp.epub", "derivative", "LCP Encrypted EPUB")]
        it = Item.from_json(identifier, item_json(identifier, files, mediatype="texts", **meta))
        h = Texts()
        self.assertEqual(h.kind(it), "magazines")
        h.prepare(it, self.archive)
        sel = selection(h, it, {}, self.config)
        self.assertEqual([f["name"] for f in sel.files], [f"{identifier}.pdf"])  # never the scans or the lending copy
        return h.plan(it, self.root / "_Inbox" / identifier, sel, self.config)

    def test_an_issue_with_a_number_and_a_date(self):
        plan = self.plan("zzap64-magazine-001", title="ZZap!64 Magazine Issue 001", date="1985-05-01T00:00:00Z", year="1985",
                         collection=["zzap64-magazine", "computermagazines", "magazine_rack", "commodoremagazines", "fav-x"])
        self.assertTrue(plan.approved, plan.review)
        self.assertEqual(plan.dest, str((self.root / "Magazines" / "ZZap!64 Magazine" / "1985").resolve()))
        self.assertIn(["zzap64-magazine-001.pdf", "ZZap!64 Magazine - Issue 001 (1985-05).pdf"], plan.moves)
        self.assertNotIn("metadata.opf", plan.writes)

    def test_a_month_without_an_issue_number(self):
        plan = self.plan("64er_1984_04", title="64'er 1984-04", date="1984-04",
                         collection=["64er_magazine", "computermagazines", "magazine_rack", "commodoremagazines"])
        self.assertEqual(plan.dest, str((self.root / "Magazines" / "64'er Magazine" / "1984").resolve()))
        self.assertIn(["64er_1984_04.pdf", "64'er Magazine - 1984-04.pdf"], plan.moves)

    def test_the_issue_field(self):
        plan = self.plan("64er_sonderheft_77", title="64'er 77", issue="77", date="",
                         collection=["64er_magazine", "magazine_rack"])
        self.assertIn(["64er_sonderheft_77.pdf", "64'er Magazine - Issue 077.pdf"], plan.moves)
        self.assertEqual(plan.dest, str((self.root / "Magazines" / "64'er Magazine").resolve()))  # no year: no year folder

    def test_without_its_collection_the_title_gives_the_name(self):
        plan = self.plan("compute-040", title="Compute! Magazine Issue 040", date="1983-09",
                         collection=["compute-magazine", "magazine_rack"])  # compute-magazine isn't known here
        self.assertIn(["compute-040.pdf", "Compute! Magazine - Issue 040 (1983-09).pdf"], plan.moves)

    def test_a_second_scan_of_an_issue_is_kept_beside_it(self):
        folder = self.root / "Magazines" / "ZZap!64 Magazine" / "1988"
        folder.mkdir(parents=True)
        (folder / "ZZap!64 Magazine - Issue 044 (1988-12).pdf").write_bytes(b"x")
        plan = self.plan("zzapp_64_issue_044_600dpi", title="Zzap! 64 Issue 044 (HQ)", date="1988-12-01",
                         collection=["zzap64-magazine", "magazine_rack"])
        self.assertIn(["zzapp_64_issue_044_600dpi.pdf", "ZZap!64 Magazine - Issue 044 (1988-12) [zzapp_64_issue_044_600dpi].pdf"],
                      plan.moves)

    def test_nothing_to_tell_issues_apart_goes_to_review(self):
        plan = self.plan("64er-magazin-de", title="64er DE Magazin", date="", collection=["64er_magazine", "magazine_rack"])
        self.assertFalse(plan.approved)
        self.assertIn("nothing tells which issue", plan.review[0])

    def test_month_then_year_in_the_title(self):
        plan = self.plan("64ER101987", title="64 ER 10 1987", date="", collection=["64er_magazine", "magazine_rack"])
        self.assertTrue(plan.approved, plan.review)
        self.assertEqual(plan.dest, str((self.root / "Magazines" / "64'er Magazine" / "1987").resolve()))
        self.assertIn(["64ER101987.pdf", "64'er Magazine - 1987-10.pdf"], plan.moves)

    def test_an_issue_number_isnt_a_month(self):
        plan = self.plan("c-10", title="Compute! Issue 10 1981", date="", collection=["compute-magazine", "magazine_rack"])
        self.assertIn(["c-10.pdf", "Compute! - Issue 010.pdf"], plan.moves)
        self.assertTrue(plan.dest.endswith("1981"))

    def test_volumes_and_spelled_months(self):
        plan = self.plan("AmazingStoriesVolume01Number04", title="Amazing Stories Volume 01 Number 04", date="1926",
                         collection=["amazingstories", "magazine_rack"])
        self.assertIn(["AmazingStoriesVolume01Number04.pdf", "Amazing Stories - Vol. 01 No. 04.pdf"], plan.moves)
        plan = self.plan("galaxymagazine-1951-02", title="Galaxy Magazine (February 1951)", date="",
                         collection=["galaxymagazine", "magazine_rack"])
        self.assertIn(["galaxymagazine-1951-02.pdf", "Galaxy Magazine - 1951-02.pdf"], plan.moves)
        self.assertTrue(plan.dest.endswith("1951"))

    def test_archive_and_collection_leave_the_name(self):
        self.fake.add("compute-magazine", [], mediatype="collection", title="Compute! Magazine Archive",
                      collection=["computermagazines", "magazine_rack"])
        plan = self.plan("compute-040", title="Compute! Magazine Issue 040", date="1983-09",
                         collection=["compute-magazine", "computermagazines", "magazine_rack"])
        self.assertIn(["compute-040.pdf", "Compute! Magazine - Issue 040 (1983-09).pdf"], plan.moves)

    def test_books_stay_books(self):
        it = Item.from_json("b", item_json("b", [], mediatype="texts", collection=["commodore_c64_books"]))
        self.assertEqual(Texts().kind(it), "books")
