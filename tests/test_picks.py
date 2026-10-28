import contextlib
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iafetch import cli, picks
from iafetch.api import Archive, Item
from iafetch.net import Client
from iafetch.picks import Copy, Film, Lists, assess, best_copies, ranked
from iafetch.queue import Queue
from tests.support import FakeArchive, file, item_json


def wikidata_rows(*rows):
    return json.dumps({"results": {"bindings": [{k: {"value": str(v)} for k, v in r.items()} for r in rows]}}).encode()


WIKIDATA = wikidata_rows(
    {"film": "http://www.wikidata.org/entity/Q1", "filmLabel": "His Girl Friday", "ia": "HisGirlFriday1940",
     "imdb": "tt0032599", "year": 1940, "minutes": 92, "pd": 1, "countries": "United States"},
    {"film": "http://www.wikidata.org/entity/Q1", "filmLabel": "His Girl Friday", "ia": "GirlFriday",
     "imdb": "tt0032599", "year": 1940, "minutes": 92},
    {"film": "http://www.wikidata.org/entity/Q1", "filmLabel": "His Girl Friday", "ia": "bad id with spaces",
     "imdb": "tt0032599", "year": 1940},
    {"film": "http://www.wikidata.org/entity/Q2", "filmLabel": "Charade", "ia": "Charade1963", "imdb": "tt0056923",
     "year": 1963, "minutes": 5212, "countries": "United States|United Kingdom"},  # 5212: seconds, ignored
    {"film": "http://www.wikidata.org/entity/Q3", "filmLabel": "Recent Film", "ia": "recent", "imdb": "tt9999999",
     "year": 2020, "minutes": 100},
)
RATINGS = gzip.compress(b"tconst\taverageRating\tnumVotes\ntt0032599\t7.8\t70000\ntt0056923\t7.9\t90000\n"
                        b"tt9999999\t9.9\t100000\ntt0000001\t5.0\t10\n")


class Opener:
    def __init__(self):
        self.calls = []

    def __call__(self, url, accept="*/*", timeout=120):
        self.calls.append(url)
        return io.BytesIO(WIKIDATA if "wikidata" in url else RATINGS)


def video(name, size, width, height, length, **kw):
    return file(name, fmt="h.264", size=size, width=str(width), height=str(height), length=str(length), **kw)


class ListsTest(unittest.TestCase):
    def test_wikidata_and_imdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = Opener()
            films = {f.title: f for f in Lists(Path(tmp) / "p.json", opener).films()}
            hgf = films["His Girl Friday"]
            self.assertEqual(hgf.uploads, ["HisGirlFriday1940", "GirlFriday"])  # the bad identifier is dropped
            self.assertEqual((hgf.year, hgf.minutes, hgf.rating, hgf.votes, hgf.public_domain), (1940, 92, 7.8, 70000, True))
            self.assertEqual(films["Charade"].countries, ["United States", "United Kingdom"])
            self.assertIsNone(films["Charade"].minutes)
            self.assertFalse(films["Charade"].public_domain)
            Lists(Path(tmp) / "p.json", opener).films()  # cached: nothing fetched again
            self.assertEqual(len(opener.calls), 2)
            Lists(Path(tmp) / "p.json", opener).films(refresh=True)
            self.assertEqual(len(opener.calls), 4)

    def test_only_wikidata_and_imdb_are_fetched(self):
        with self.assertRaises(ValueError):
            picks.fetch("https://example.org/x")

    def test_countries(self):
        films = [Film("Q1", "Tokyo Story", 1953, 136, "tt1", ["a"], False, ["Japan"], 8.1, 79000),
                 Film("Q2", "Metropolis", 1927, 153, "tt2", ["b"], True, ["Weimar Republic"], 8.2, 200000),
                 Film("Q3", "His Girl Friday", 1940, 92, "tt3", ["c"], True, ["United States"], 7.8, 70000)]
        self.assertEqual(picks.country_names("Germany", films), {"weimar republic"})
        self.assertEqual(picks.country_names("japan", films), {"japan"})
        self.assertEqual([f.title for f in ranked(films, countries={"weimar republic"})], ["Metropolis"])
        self.assertEqual([f.title for f in ranked(films, countries={"japan"})], [])  # not public domain
        self.assertEqual([f.title for f in ranked(films, countries={"japan"}, any_rights=True)], ["Tokyo Story"])
        with self.assertRaisesRegex(ValueError, "did you mean japan"):
            picks.country_names("Japn", films)

    def test_ranking(self):
        films = [Film("Q1", "A", 1940, 90, "tt1", ["a"], True, [], 7.8, 70000),
                 Film("Q2", "B", 1963, 90, "tt2", ["b"], False, [], 7.9, 90000),
                 Film("Q3", "C", 1930, 90, "tt3", ["c"], True, [], 8.5, 100),  # too few votes
                 Film("Q4", "D", 1925, 90, "tt4", ["d"], True, [], 7.8, 90000)]
        self.assertEqual([f.title for f in ranked(films)], ["D", "A"])
        self.assertEqual([f.title for f in ranked(films, any_rights=True)], ["B", "D", "A"])
        self.assertEqual([f.title for f in ranked(films, years=(1930, 1950))], ["A"])
        self.assertEqual([f.title for f in ranked(films, min_votes=50)], ["C", "D", "A"])


class CopiesTest(unittest.TestCase):
    FILM = Film("Q1", "His Girl Friday", 1940, 92, "tt0032599", [], True)

    def assess(self, identifier, files, **meta):
        return assess(Item.from_json(identifier, item_json(identifier, files, **meta)), self.FILM)

    def test_picture_and_bitrate(self):
        c = self.assess("HisGirlFriday1940_201505", [video("His Girl Friday (1940).mp4", 1_775_000_000, 635, 480, 5460)],
                        title="His Girl Friday")
        self.assertEqual((c.width, c.height, c.doubt), (635, 480, ""))
        self.assertAlmostEqual(c.mbps, 2.6, places=1)
        self.assertEqual(c.picture, "635x480, 2.6 Mbit/s")

    def test_doubts(self):
        wrong_film = self.assess("GirlFriday", [video("The_Saint's_Girl_Friday_1953.mp4", 205_000_000, 540, 360, 5400)],
                                 title="Girl Friday")
        self.assertIn("1953", wrong_film.doubt)
        trailer = self.assess("hgf", [video("trailer.mp4", 5_000_000, 640, 480, 120)], title="His Girl Friday")
        self.assertIn("runs 2 min", trailer.doubt)
        other = self.assess("x", [video("x.mp4", 5_000_000, 640, 480, 5500)], title="Bringing Up Baby")
        self.assertIn("titled", other.doubt)
        spelled = assess(Item.from_json("nayakan-1987", item_json("nayakan-1987", [video("n.mp4", 1, 1, 1, 9000)],
                                                                    title="Nayakan ( 1987)", date="1987")),
                         Film("Q", "Nayagan", 1987, 155, "tt", []))
        self.assertEqual(spelled.doubt, "")

    def test_better_copies_first(self):
        copies = [Copy("blurry", 348, 236, 0.8), Copy("doubtful", 1920, 1080, 10, doubt="runs 2 min"),
                  Copy("dvd", 720, 480, 3.0), Copy("vhs", 640, 480, 3.5), Copy("hd", 1440, 1080, 4.0)]
        self.assertEqual([c.identifier for c in sorted(copies, key=Copy.key)], ["hd", "vhs", "dvd", "blurry", "doubtful"])

    def test_best_copies_skips_restricted_and_missing_uploads(self):
        fake = FakeArchive()
        fake.add("good", [video("good.mp4", 1_000_000_000, 640, 480, 5500)], title="His Girl Friday")
        fake.items["good"]["search"] = {"num_reviews": 3, "avg_rating": 4.5}
        fake.add("lent", [video("lent.mp4", 2_000_000_000, 1920, 1080, 5500)], title="His Girl Friday",
                 **{"access-restricted-item": "true"})
        archive = Archive(Client(1.0, 1.0, get=fake.get, opener=fake.open, sleep=lambda s: None))
        film = Film("Q1", "His Girl Friday", 1940, 92, "tt", ["lent", "gone", "good"], True, [], 7.8, 70000)
        (got_film, copies), = best_copies([film], archive, top=1)
        self.assertEqual([c.identifier for c in copies], ["good"])
        self.assertEqual((copies[0].reviews, copies[0].stars), (3, 4.5))


class PicksCliTest(unittest.TestCase):
    def test_list_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "root").mkdir()
            (base / "config.toml").write_text(f'root = "{base / "root"}"\n')
            fake = FakeArchive()
            fake.add("HisGirlFriday1940", [video("hgf.mp4", 1_775_000_000, 635, 480, 5460)], title="His Girl Friday")
            fake.add("GirlFriday", [video("The_Saint's_Girl_Friday_1953.mp4", 205_000_000, 540, 360, 4080)],
                     title="Girl Friday")
            with mock.patch.dict("os.environ", {"IAFETCH_CONFIG": str(base / "config.toml"), "XDG_DATA_HOME": str(base)}), \
                    mock.patch.object(cli, "Client", lambda *a, **kw: Client(*a, get=fake.get, opener=fake.open,
                                                                             sleep=lambda s: None, **kw)), \
                    mock.patch.object(picks, "fetch", Opener()):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(["picks", "--queue", "--copies"])
                self.assertEqual(code, 0, out.getvalue())
                text = out.getvalue()
                self.assertIn("1. His Girl Friday (1940)   IMDb 7.8 (70,000 votes)", text)
                self.assertIn("635x480, 2.6 Mbit/s", text)
                self.assertIn("? runs 68 min; the film is 92", text)
                self.assertNotIn("Charade", text)  # not known to be public domain
                self.assertEqual([e.identifier for e in Queue().pending()], ["HisGirlFriday1940"])


if __name__ == "__main__":
    unittest.main()
