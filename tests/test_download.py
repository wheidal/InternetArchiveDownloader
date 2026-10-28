import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from iacore.download import DownloadError

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.download import DiskFull, download_item, safe_path, staging
from iafetch.handlers.generic import Generic
from iafetch.net import Client
from iafetch.select import select
from tests.support import FakeArchive, body, file


def setup(tmp, files=None):
    fake = FakeArchive()
    fake.add("film", files or [file("film.mpeg"), file("sub dir/a:b?.srt", fmt="SubRip", size=50),
                               file("__ia_thumb.jpg", fmt="Item Tile", size=80)])
    client = Client(1.0, 1.0, get=fake.get, opener=fake.open, sleep=lambda s: None)
    item = Item.from_json("film", fake.items["film"])
    inbox = Path(tmp) / "_Inbox"
    inbox.mkdir()
    return fake, client, item, inbox


def roomy(path):
    return SimpleNamespace(free=10 ** 12)


class SafePathTest(unittest.TestCase):
    def test_names_cant_escape_the_item_folder(self):  # F15b
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for bad in ("../x", "a/../../x", "/etc/passwd", "", "./"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    safe_path(folder, bad)
            self.assertEqual(safe_path(folder, "S01/E01: Pilot?.mp4"), folder / "S01" / "E01_ Pilot_.mp4")
            self.assertEqual(safe_path(folder, "name. "), folder / "name")


class DownloadTest(unittest.TestCase):
    def test_an_item_lands_verified_in_the_inbox(self):  # F15, F16
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            staged = download_item("film", select(item), inbox, client, disk_usage=roomy)
            self.assertEqual(staged.folder, inbox / "film")
            self.assertFalse(staging(inbox, "film").exists())
            self.assertEqual((inbox / "film" / "film.mpeg").read_bytes(), body("film.mpeg"))
            self.assertTrue((inbox / "film" / "sub dir" / "a_b_.srt").is_file())
            self.assertTrue((inbox / "film" / "film_meta.xml").is_file())  # its listed MD5 is wrong: not checked
            self.assertTrue((inbox / "film" / "film_files.xml").is_file())
            self.assertTrue((inbox / "film" / "__ia_thumb.jpg").is_file())
            self.assertEqual(staged.files, 5)

    def test_an_interrupted_item_resumes(self):  # F13
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            part = staging(inbox, "film")
            part.mkdir()
            (part / "film.mpeg").write_bytes(body("film.mpeg"))  # finished last time
            (part / "__ia_thumb.jpg.part").write_bytes(body("__ia_thumb.jpg", 80)[:30])  # half done
            staged = download_item("film", select(item), inbox, client, disk_usage=roomy)
            self.assertEqual(staged.reused, 1)
            self.assertNotIn("https://archive.org/download/film/film.mpeg", fake.calls)
            self.assertEqual((inbox / "film" / "__ia_thumb.jpg").read_bytes(), body("__ia_thumb.jpg", 80))

    def test_a_full_disk_pauses_rather_than_fails(self):  # F15d, R20
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            with self.assertRaises(DiskFull):
                download_item("film", select(item), inbox, client, reserve=10 ** 6,
                              disk_usage=lambda p: SimpleNamespace(free=10 ** 5))
            self.assertEqual([c for c in fake.calls if "/download/" in c], [])

    def test_a_bad_file_fails_the_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            fake.fail["film.mpeg"] = [404]
            with self.assertRaisesRegex(DownloadError, "404"):
                download_item("film", select(item), inbox, client, disk_usage=roomy)
            self.assertFalse((inbox / "film").exists())


class GenericTest(unittest.TestCase):
    def test_files_into_the_library_with_a_sidecar(self):  # §6.1, R16
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            config = parse_config({"root": tmp})
            sel = select(item)
            staged = download_item("film", sel, inbox, client, disk_usage=roomy)
            h = Generic()
            plan = h.plan(item, staged.folder, sel, config)
            self.assertEqual(plan.review, [])
            dest = h.process(plan, item, sel, config)
            self.assertEqual(dest, (Path(tmp) / "Other" / "film").resolve())
            self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()),
                             ["film.mpeg", "film_files.xml", "film_meta.xml", "folder.jpg", "iafetch.json",
                              "sub dir/a_b_.srt"])
            record = json.loads((dest / "iafetch.json").read_text())
            self.assertEqual(record["identifier"], "film")
            self.assertTrue(record["license"].startswith("public domain"))
            self.assertEqual({f["name"] for f in record["files"]}, {"film.mpeg", "sub dir/a:b?.srt"})
            self.assertFalse(staged.folder.exists())

    def test_an_existing_folder_goes_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake, client, item, inbox = setup(tmp)
            config = parse_config({"root": tmp})
            (Path(tmp) / "Other" / "film").mkdir(parents=True)
            plan = Generic().plan(item, inbox / "film", select(item), config)
            self.assertFalse(plan.approved)
            self.assertIn("already exists", plan.review[0])

    def test_datasets_and_images_have_their_own_folders(self):
        for mediatype, kind in (("data", "data"), ("image", "images"), ("audio", "other")):
            self.assertEqual(Generic().kind(SimpleNamespace(mediatype=mediatype)), kind)


if __name__ == "__main__":
    unittest.main()
