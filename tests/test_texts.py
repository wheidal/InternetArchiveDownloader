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
