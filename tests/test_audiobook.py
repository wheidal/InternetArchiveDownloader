import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.audiobook import Audiobook, chapter_title, credits, person
from tests.support import file, item_json

LIBRIVOX = {"mediatype": "audio", "collection": ["librivoxaudio", "audio_bookspoetry"], "language": "eng"}


def mp3(name, track, title, fmt="VBR MP3", source="original", **kw):
    return file(name, source, fmt, size=500, track=track, title=title, **kw)


# The shapes of real items: The Art of War (an M4B and chapter MP3s), Moby Dick (an M4B in five parts)
ART_OF_WAR = [file("ArtOfWar-64kb_librivox.m4b", fmt="Audiobook", size=900), file("Art_War_1107.jpg", fmt="JPEG", size=100),
              file("Art_War_1107_thumb.jpg", "derivative", "JPEG Thumb"), file("Art_War_1107.pdf", fmt="Text PDF"),
              mp3("art_of_war_01-02_sun_tzu.mp3", "1", "1 Laying Plans - 2 Waging War"),
              mp3("art_of_war_01-02_sun_tzu_64kb.mp3", "", "", fmt="64Kbps MP3", source="derivative"),
              file("art_of_war_librivox_64kb_mp3.zip", "derivative", "ZIP"), file("art_of_war_librivox.json", fmt="JSON")]
MOBY = [file(f"MobyDickOrTheWhalePart{n}_librivox.m4b", fmt="Audiobook", size=900) for n in (2, 1, 5, 3, 4)] + \
       [file("Moby_Dick_1002.jpg", fmt="JPEG", size=100)]
CHAPTERS = [mp3("ch10.mp3", "10/12", "10 - The Noble Bachelor"), mp3("ch2.mp3", "2/12", "* The Album [06:29]"),
            mp3("ch1.mp3", "", "", fmt="64Kbps MP3", source="derivative"),
            mp3("ch1_hq.mp3", "1/12", "01 - A Scandal in Bohemia"),
            file("ch1.ogg", "derivative", "Ogg Vorbis")]


def item(identifier, files, **meta):
    return Item.from_json(identifier, item_json(identifier, files, **{**LIBRIVOX, **meta}))


class AudiobookTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, it):
        sel = selection(Audiobook(), it, {}, self.config)
        return sel, Audiobook().plan(it, self.root / "_Inbox" / it.identifier, sel, self.config)

    def test_librivox_and_m4b_items_are_audiobooks(self):
        self.assertEqual(choose(item("x", []), self.config)[0].name, "audiobook")
        m4b_elsewhere = Item.from_json("y", item_json("y", ART_OF_WAR[:1], mediatype="audio", collection=["opensource_audio"]))
        self.assertEqual(choose(m4b_elsewhere, self.config)[0].name, "audiobook")

    def test_the_m4b_alone(self):
        it = item("art_of_war_librivox", ART_OF_WAR, title="The Art of War", creator="Sun Tzu",
                  description="<div>Librivox recording of The Art of War, translated by Lionel Giles.<br/></div>"
                              "<div>Read by Moira Fogarty. <br/></div>")
        sel, plan = self.plan(it)
        self.assertEqual([f["name"] for f in sel.files], ["ArtOfWar-64kb_librivox.m4b", "Art_War_1107.jpg"])
        self.assertEqual(plan.dest, str((self.root / "Audiobooks" / "Sun Tzu" / "The Art of War").resolve()))
        self.assertIn(["ArtOfWar-64kb_librivox.m4b", "The Art of War.m4b"], plan.moves)
        self.assertIn(["Art_War_1107.jpg", "cover.jpg"], plan.moves)
        self.assertEqual(plan.writes["reader.txt"], "Moira Fogarty\n")
        self.assertIn("translated by Lionel Giles", plan.writes["desc.txt"])
        self.assertTrue(plan.approved)

    def test_m4b_parts_in_order(self):
        sel, plan = self.plan(item("moby_dick_librivox", MOBY, title="Moby Dick, or the Whale", creator="Herman Melville"))
        self.assertEqual([m[1] for m in plan.moves if m[1].endswith(".m4b")],
                         [f"Moby Dick, or the Whale - Part {n}.m4b" for n in range(1, 6)])
        self.assertEqual([m[0] for m in plan.moves if m[0].endswith(".m4b")],
                         [f"MobyDickOrTheWhalePart{n}_librivox.m4b" for n in range(1, 6)])

    def test_chapters_when_theres_no_m4b(self):
        sel, plan = self.plan(item("holmes", CHAPTERS, title="Adventures", creator="Arthur Conan Doyle"))
        self.assertEqual([m for m in plan.moves if m[1].endswith(".mp3")],
                         [["ch1_hq.mp3", "01 - A Scandal in Bohemia.mp3"], ["ch2.mp3", "02 - The Album.mp3"],
                          ["ch10.mp3", "03 - The Noble Bachelor.mp3"]])  # uploaded MP3s, track order, tidy titles

    def test_a_single_file_book_is_named_after_the_book(self):
        sel, plan = self.plan(item("g", [mp3("book.mp3", "", "Untitled")], title="My Lady's Money",
                                   creator="Collins, Wilkie, 1824-1889", collection=["gutenberg-audiobooks"]))
        self.assertEqual(plan.dest, str((self.root / "Audiobooks" / "Wilkie Collins" / "My Lady's Money").resolve()))
        self.assertIn(["book.mp3", "My Lady's Money.mp3"], plan.moves)

    def test_another_recording_of_the_same_book(self):
        (self.root / "Audiobooks" / "Sun Tzu" / "The Art of War").mkdir(parents=True)
        sel, plan = self.plan(item("art_of_war_2", ART_OF_WAR, title="The Art of War (version 2)", creator="Sun Tzu"))
        self.assertTrue(plan.dest.endswith("The Art of War (version 2)"))
        sel, plan = self.plan(item("art_of_war_3", ART_OF_WAR, title="The Art of War", creator="Sun Tzu"))
        self.assertTrue(plan.dest.endswith("The Art of War [art_of_war_3]"))


class CreditsTest(unittest.TestCase):
    def credits(self, **meta):
        return credits(Item.from_json("x", item_json("x", [], **{**LIBRIVOX, **meta})))

    def test_credits(self):
        self.assertEqual(self.credits(title="Alice's Adventures in Wonderland, by Lewis Carroll", creator="Lewis Carroll"),
                         ("Lewis Carroll", "Alice's Adventures in Wonderland", ""))
        # the title names the author, and the creator is the narrator
        self.assertEqual(self.credits(title="Anton Chekhov - Collected Stories", creator="Alan Davis Drake",
                                      description="Anton Chekhov (1860-1904) shares insightful moments…"),
                         ("Anton Chekhov", "Collected Stories", "Alan Davis Drake"))
        # but not when the creator is in the description, or the separator is a colon
        self.assertEqual(self.credits(title="Sherlock Holmes - The Final Problem", creator="Arthur Conan Doyle",
                                      description="Sherlock Holmes, by Arthur Conan Doyle…")[0], "Arthur Conan Doyle")
        self.assertEqual(self.credits(title="Walden: or, Life in the Woods", creator="Henry David Thoreau",
                                      description="Walden is Thoreau's account…")[0], "Henry David Thoreau")
        self.assertEqual(self.credits(title="Untitled", creator="")[0], "Unknown")

    def test_names_and_chapters(self):
        self.assertEqual(person("Collins, Wilkie, 1824-1889"), "Wilkie Collins")
        self.assertEqual(person("Twain, Mark"), "Mark Twain")
        self.assertEqual(person("Sir Arthur Conan Doyle"), "Sir Arthur Conan Doyle")
        self.assertEqual(chapter_title({"name": "x.mp3", "title": "* After the Theatre [08:46]"}), "After the Theatre")
        self.assertEqual(chapter_title({"name": "chapter_03_twain.mp3", "title": ""}), "chapter 03 twain")


if __name__ == "__main__":
    unittest.main()
