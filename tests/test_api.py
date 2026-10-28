import tempfile
import unittest
from pathlib import Path

from iacore.http import FetchError

from iafetch.api import Archive, Item, build_query, identifier_of
from iafetch.net import Client
from iafetch.rights import RESTRICTED_QUERY, license_text, restriction
from tests.support import FakeArchive, item_json


def archive(fake, cache=None, clock=None):
    c = Client(1.0, 1.0, get=fake.get, opener=fake.open, sleep=lambda s: None)
    return Archive(c, cache, **({"clock": clock} if clock else {}))


class ApiTest(unittest.TestCase):
    def test_identifiers_and_urls(self):  # F5
        self.assertEqual(identifier_of("AboutBan1935"), ("AboutBan1935", ""))
        self.assertEqual(identifier_of("https://archive.org/details/AboutBan1935"), ("AboutBan1935", ""))
        self.assertEqual(identifier_of("archive.org/details/AboutBan1935/"), ("AboutBan1935", ""))
        self.assertEqual(identifier_of("https://archive.org/download/get-smart/S01/E01%20Pilot.mp4"),
                         ("get-smart", "S01/E01 Pilot.mp4"))
        for bad in ("../etc", "a b", "https://example.org/x", "https://archive.org/search?q=x", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                identifier_of(bad)

    def test_every_search_leaves_out_restricted_items(self):  # R14
        self.assertTrue(build_query("bananas").endswith(RESTRICTED_QUERY))
        q = build_query("", collection="prelinger", mediatype="movies", year="1930-1939", license="pd")
        self.assertIn("collection:prelinger AND mediatype:movies AND year:[1930 TO 1939]", q)
        self.assertIn("publicdomain", q)
        with self.assertRaises(ValueError):
            build_query("")

    def test_metadata_is_cached(self):  # R9
        fake = FakeArchive()
        fake.add("x", [])
        with tempfile.TemporaryDirectory() as tmp:
            now = [0.0]
            a = archive(fake, Path(tmp) / "m.db", clock=lambda: now[0])
            a.item("x")
            a.item("x")
            self.assertEqual(len(fake.calls), 1)
            now[0] = 8 * 24 * 3600  # past the 7-day TTL
            a.item("x")
            a.item("x", refresh=True)
            self.assertEqual(len(fake.calls), 3)

    def test_a_missing_item(self):
        with self.assertRaisesRegex(FetchError, "no such item"):
            archive(FakeArchive()).item("gone")

    def test_item_fields(self):
        i = Item.from_json("x", item_json("x", [], date="1952-04-26", title=["A", "B"]))
        self.assertEqual((i.year, i.title, i.mediatype, i.collections), ("1952", "A; B", "movies", ["prelinger"]))


class RightsTest(unittest.TestCase):
    def test_restrictions(self):
        def r(**kw):
            data = item_json("x", [], **kw)
            return restriction(Item.from_json("x", data))
        self.assertEqual(r(), "")
        self.assertIn("access-restricted", r(**{"access-restricted-item": "true"}))
        self.assertIn("inlibrary", r(collection=["inlibrary", "x"]))
        dark = item_json("x", [])
        dark["is_dark"] = True
        self.assertIn("dark", restriction(Item.from_json("x", dark)))

    def test_license_is_shown_plainly(self):  # R16
        def lic(**kw):
            return license_text(Item.from_json("x", item_json("x", [], **kw)))
        self.assertTrue(lic().startswith("public domain"))
        self.assertTrue(lic(licenseurl="https://creativecommons.org/licenses/by-nc/3.0/").startswith("CC BY-NC 3.0"))
        self.assertEqual(lic(licenseurl=""), "no license declared")
        self.assertEqual(lic(licenseurl="", rights="Copyright 1952 CBS"), "no license declared (Copyright 1952 CBS)")


if __name__ == "__main__":
    unittest.main()
