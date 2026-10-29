import tempfile
import unittest
import xml.dom.minidom
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.images import Images
from tests.support import file, item_json

# The shapes of real items: a museum's TIFF with archive.org's JPEG, a USGS map with its georeferencing
CLEVELAND = [file("1940.743_full.tif", fmt="TIFF", size=1800), file("1940.743_full.jpg", "derivative", "JPEG", size=200,
                                                                   original="1940.743_full.tif"),
             file("1940.743_full_thumb.jpg", "derivative", "JPEG Thumb"), file("clevelandart-1940.743_metadata.json", fmt="JSON")]
USGS = [file("o36105e4.tif", fmt="TIFF", size=900), file("o36105e4.tfw", fmt="Unknown", size=1), file("o36105e4.fgd", fmt="Unknown", size=1),
        file("o36105e4.jpg", "derivative", "JPEG", size=1600, original="o36105e4.tif")]


def item(identifier, files, **meta):
    meta = {"mediatype": "image", "collection": ["clevelandart", "image"], "title": "Mona Lisa", **meta}
    it = Item.from_json(identifier, item_json(identifier, files, **meta))
    it.raw["_iafetch_collection"] = "Cleveland Museum of Art"  # what prepare() looks up
    return it


class ImagesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, it, config=None):
        config = config or self.config
        sel = selection(Images(), it, {}, config)
        return sel, Images().plan(it, self.root / "x", sel, config)

    def test_images_get_the_images_handler(self):
        self.assertEqual(choose(item("x", []), self.config)[0].name, "images")

    def test_the_original_with_a_sidecar(self):
        it = item("clevelandart-1940.743-mona-lisa", CLEVELAND, creator=["Samuel Arlent-Edwards (American, 1862–1938)"],
                  date="c. 1880–1938", subject="Prints", licenseurl="https://creativecommons.org/publicdomain/zero/1.0/",
                  description="<p>Mona Lisa, c. 1880–1938.</p>")
        sel, plan = self.plan(it)
        self.assertEqual([f["name"] for f in sel.files], ["1940.743_full.tif"])  # not the JPEG copy or the thumbnail
        self.assertEqual(plan.dest, str((self.root / "Images" / "Cleveland Museum of Art").resolve()))
        self.assertIn(["1940.743_full.tif", "Mona Lisa.tif"], plan.moves)
        sidecar = plan.writes["Mona Lisa.tif.xmp"]
        xml.dom.minidom.parseString(sidecar)  # well-formed
        for part in ("<rdf:li xml:lang=\"x-default\">Mona Lisa</rdf:li>", "<rdf:li>Samuel Arlent-Edwards (American, 1862–1938)</rdf:li>",
                     "<rdf:li>Prints</rdf:li>", "<dc:source>https://archive.org/details/clevelandart-1940.743-mona-lisa</dc:source>",
                     "<photoshop:Source>Cleveland Museum of Art</photoshop:Source>"):
            self.assertIn(part, sidecar)

    def test_jpegs_when_asked(self):
        config = parse_config({"root": str(self.root), "images": {"format": "jpeg"}})
        sel, plan = self.plan(item("c", CLEVELAND), config)
        self.assertEqual([f["name"] for f in sel.files], ["1940.743_full.jpg"])

    def test_a_map_keeps_its_georeferencing(self):
        sel, plan = self.plan(item("usgs_drg_nm_36105_e4", USGS, title="Map of New_Mexico 36.500N 105.375W"))
        self.assertEqual(sorted(m[1] for m in plan.moves if not m[1].startswith(".iafetch")),
                         ["Map of New_Mexico 36.500N 105.375W.fgd", "Map of New_Mexico 36.500N 105.375W.tfw",
                          "Map of New_Mexico 36.500N 105.375W.tif"])

    def test_many_pictures_go_in_a_folder(self):
        files = [file(f"page{n:03d}.png", fmt="PNG") for n in (2, 1, 10)]
        sel, plan = self.plan(item("azan", files, title="electronic_azan"))
        self.assertTrue(plan.dest.endswith("Cleveland Museum of Art/electronic_azan"))
        self.assertEqual([m[1] for m in plan.moves if m[1].endswith(".png")], ["page001.png", "page002.png", "page010.png"])
        self.assertIn("page001.png.xmp", plan.writes)

    def test_a_second_picture_of_the_same_name(self):
        (self.root / "Images" / "Cleveland Museum of Art").mkdir(parents=True)
        (self.root / "Images" / "Cleveland Museum of Art" / "Mona Lisa.tif").write_bytes(b"x")
        sel, plan = self.plan(item("other-mona-lisa", CLEVELAND))
        self.assertIn(["1940.743_full.tif", "Mona Lisa [other-mona-lisa].tif"], plan.moves)


if __name__ == "__main__":
    unittest.main()
