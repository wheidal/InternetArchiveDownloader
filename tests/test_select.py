import unittest

from iafetch.api import Item
from iafetch.select import Profile, select
from tests.support import file, item_json

# The shape of a real Prelinger item (AboutBan1935), trimmed.
FILES = [
    file("film.asr.js", fmt="Unknown"), file("film.asr.srt", fmt="SubRip", size=0),
    file("film.avi", fmt="Cinepack"), file("film.gif", "derivative", "Animated GIF"),
    file("film.mp4", "derivative", "h.264"), file("film.mpeg", fmt="MPEG2"),
    file("film.ogv", "derivative", "Ogg Video"), file("film.pack-car.trigger", fmt="Content Addressable aRchive Trigger"),
    file("film.thumbs/film_000001.jpg", "derivative", "Thumbnail"),
    file("film.transmit-car-boost.log", fmt="CAR Transmit Log (Boost)"), file("film_meta.sqlite", fmt="Metadata"),
    file("film_reviews.xml", fmt="Metadata"), file("film_archive.torrent", "metadata", "Archive BitTorrent"),
    file("film_edit.mp4", fmt="HiRes MPEG4"), file("__ia_thumb.jpg", fmt="Item Tile"),
    file("secret.mpeg", private="true"), file("book_lcp.pdf", fmt="LCP Encrypted PDF"),
]


def item(files=FILES):
    return Item.from_json("film", item_json("film", files))


class SelectTest(unittest.TestCase):
    def test_originals_without_byproducts(self):  # R11
        sel = select(item())
        self.assertEqual([f["name"] for f in sel.files], ["film.avi", "film.mpeg", "film_edit.mp4"])
        self.assertEqual([f["name"] for f in sel.record], ["film_meta.xml", "film_files.xml"])
        self.assertEqual(sel.tile["name"], "__ia_thumb.jpg")

    def test_private_and_encrypted_are_skipped_and_counted(self):  # R14, F15c
        sel = select(item(), Profile(originals=False))
        names = [f["name"] for f in sel.files]
        self.assertNotIn("secret.mpeg", names)
        self.assertNotIn("book_lcp.pdf", names)
        self.assertEqual((sel.private, sel.encrypted), (1, 1))

    def test_a_format_picks_the_derivative(self):  # F7
        sel = select(item(), Profile(formats=["h.264"]))
        self.assertEqual([f["name"] for f in sel.files], ["film.mp4"])

    def test_all_takes_derivatives_but_never_junk(self):
        names = [f["name"] for f in select(item(), Profile(originals=False)).files]
        self.assertIn("film.mp4", names)
        self.assertIn("film.ogv", names)
        for junk in ("film.gif", "film.thumbs/film_000001.jpg", "film_meta.sqlite", "film_reviews.xml",
                     "film_archive.torrent", "film.transmit-car-boost.log", "film.pack-car.trigger", "film.asr.js"):
            self.assertNotIn(junk, names)

    def test_globs_and_excludes(self):
        self.assertEqual([f["name"] for f in select(item(), Profile(globs=["*.mpeg"])).files], ["film.mpeg"])
        self.assertEqual([f["name"] for f in select(item(), Profile(exclude=["*.avi", "*_edit*"])).files], ["film.mpeg"])

    def test_empty_files_are_left_out(self):
        self.assertNotIn("film.asr.srt", [f["name"] for f in select(item(), Profile(globs=["*.srt"])).files])
        self.assertIn("film.asr.srt", [f["name"] for f in select(item(), Profile(globs=["*.srt"], empty=True)).files])

    def test_size_counts_files_records_and_tile(self):
        sel = select(item())
        self.assertEqual(sel.size, 3 * 1000 + 300 + 1000)


if __name__ == "__main__":
    unittest.main()
