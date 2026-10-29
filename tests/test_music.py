import json
import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.base import Plan
from iafetch.handlers.music import Music, best_audio, finish, numbered, read_tracks, track_title
from tests.support import file, item_json
from tests.test_radio import tiny_mp3


def track(name, title="", track="", fmt="VBR MP3", source="original", **kw):
    return file(name, source, fmt, size=400, title=title, track=track, **kw)


# The shapes of real items
BAD_PANDA = [track(f"0{n}RidingAloneForThousandsOfMiles-Song{n}.mp3", f"0{n} Riding Alone For Thousands Of Miles - song {n}",
                   f"{n}/8", artist="Riding Alone for Thousands of Miles") for n in range(1, 4)] + \
            [file("RidingAlone-BrickCityLoveSong.wav", fmt="WAVE", size=900),
             file("RidingAlone-BrickCityLoveSong.flac", "derivative", "Flac", size=600),
             track("01RidingAloneForThousandsOfMiles-Song1.ogg", fmt="Ogg Vorbis", source="derivative"),
             file("album.zip", fmt="ZIP"), file("00-BrickCityGhosts.jpg", fmt="JPEG", size=300),
             file("00-BrickCityGhosts_thumb.jpg", "derivative", "JPEG Thumb")]
CONET = [track(f"tcp_d{d}_0{n}_track_{n}_irdial.mp3", f"tcp d{d} {n} track {n} irdial", "1") for d in (1, 2) for n in (1, 2)]
SIDE = [file("House Of The Rising Sun - Josh White-restored.flac", fmt="24bit Flac", size=900, title="House Of The Rising Sun (restored)"),
        file("House Of The Rising Sun - Josh White-restored.mp3", "derivative", "VBR MP3"),
        file("78_house_gbia0001628b.tif", fmt="TIFF", size=500), file("78_house_gbia0001628b.zip", fmt="ZIP", size=9000)]


def item(identifier, files, **meta):
    meta = {"mediatype": "audio", "collection": ["bad-panda", "netlabels"], **meta}
    return Item.from_json(identifier, item_json(identifier, files, **meta))


class MusicTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = parse_config({"root": tmp.name})

    def plan(self, it):
        sel = selection(Music(), it, {}, self.config)
        return sel, Music().plan(it, self.root / "_Inbox" / it.identifier, sel, self.config)

    def test_music_collections_get_the_music_handler(self):
        for collections in (["netlabels"], ["georgeblood", "78rpm"], ["audio_music"]):
            self.assertEqual(choose(item("x", [], collection=collections), self.config)[0].name, "music")

    def test_an_album(self):
        it = item("badpanda018", BAD_PANDA, title="Riding Alone for Thousands of Miles – Brick City Love Song [BadPanda018]",
                  creator="Riding Alone For Thousands Of Miles", date="2009")
        sel, plan = self.plan(it)
        # the MP3s, not one WAV's FLAC of a single track, the Ogg copies or the zip
        self.assertEqual(sorted(f["name"] for f in sel.files),
                         ["00-BrickCityGhosts.jpg"] + [f"0{n}RidingAloneForThousandsOfMiles-Song{n}.mp3" for n in range(1, 4)])
        self.assertEqual(plan.dest, str((self.root / "Music" / "Riding Alone For Thousands Of Miles" / "2009 - Brick City Love Song").resolve()))
        self.assertEqual([m[1] for m in plan.moves if not m[1].startswith(".iafetch")],
                         ["01 - Song 1.mp3", "02 - Song 2.mp3", "03 - Song 3.mp3", "cover.jpg"])

    def test_lossless_when_it_covers_the_album(self):
        files = [track("a.mp3", "A"), track("b.mp3", "B"), file("a.flac", fmt="Flac"), file("b.flac", fmt="Flac")]
        self.assertEqual([f["name"] for f in best_audio(files)], ["a.flac", "b.flac"])
        self.assertEqual([f["name"] for f in best_audio(files, lossless_first=False)], ["a.mp3", "b.mp3"])

    def test_discs_and_codes(self):
        it = item("ird059", CONET, title="The Conet Project - Recordings of Shortwave Numbers Stations [ird059]",
                  creator="The Conet Project", collection=["irdial", "netlabels"], date="0000-00-00")
        sel, plan = self.plan(it)
        self.assertTrue(plan.dest.endswith("The Conet Project/Recordings of Shortwave Numbers Stations"))
        self.assertEqual([m[1] for m in plan.moves if m[1].endswith(".mp3")],
                         ["1-01 - Track 1.mp3", "1-02 - Track 2.mp3", "2-01 - Track 1.mp3", "2-02 - Track 2.mp3"])

    def test_titles(self):
        self.assertEqual(track_title("01 Riding Alone For Thousands Of Miles - lullaby", "Riding Alone For Thousands Of Miles", []), "Lullaby")
        self.assertEqual(track_title("tcp d1 1 the swedish rhapsody irdial", "The Conet Project", ["irdial"]), "The Swedish Rhapsody")
        self.assertEqual(track_title("Anya's prayer, Anya's dream", "X", []), "Anya's prayer, Anya's dream")
        self.assertEqual(track_title("House Of The Rising Sun (restored)", "Josh White", []), "House Of The Rising Sun")

    def test_numbering(self):
        files = [track(f"Caruso-{name}.mp3", name, number) for name, number in (("Zeta", "01"), ("Alpha", "00"), ("Beta", "00"))]
        self.assertEqual([(t.number, t.title) for t in numbered(read_tracks(files, "Caruso", []))],
                         [(1, "Alpha"), (2, "Beta"), (3, "Zeta")])  # stray numbers: by name
        files = [track(f"{n}.mp3", f"T{n}", f"{4 - n}/3") for n in (1, 2, 3)]
        self.assertEqual([t.title for t in numbered(read_tracks(files, "X", []))], ["T3", "T2", "T1"])  # by the tags

    def test_a_78_side(self):
        it = item("78_house-of-the-rising-sun_josh-white_gbia0001628b", SIDE, title="House Of The Rising Sun",
                  creator="Josh White and his Guitar", date="1942", publisher="Keynote Recordings",
                  **{"publisher-catalog-number": "K-542 B"}, collection=["78rpm", "georgeblood", "audio_music"])
        sel, plan = self.plan(it)
        self.assertEqual(sorted(f["name"] for f in sel.files), ["78_house_gbia0001628b.tif", "House Of The Rising Sun - Josh White-restored.flac"])
        self.assertEqual(plan.dest, str((self.root / "Music" / "78rpm" / "Josh White and his Guitar").resolve()))
        self.assertIn(["House Of The Rising Sun - Josh White-restored.flac", "House Of The Rising Sun (Keynote Recordings K-542 B).flac"], plan.moves)
        self.assertIn(["78_house_gbia0001628b.tif", "House Of The Rising Sun (Keynote Recordings K-542 B).tif"], plan.moves)

    def test_genres_leave_out_the_format(self):
        from iafetch.handlers.music import genres
        self.assertEqual(genres(item("x", [], subject=["78rpm", "Blues", "Folk"])), ["Blues", "Folk"])

    def test_tagging_an_album(self):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("mutagen isn't installed")
        from mutagen.id3 import ID3
        it = item("badpanda018", BAD_PANDA[:1], title="Brick City Love Song [BadPanda018]", creator="Riding Alone", date="2009")
        sel = selection(Music(), it, {}, self.config)
        dest = self.root / "album"
        (dest / ".iafetch" / "b").mkdir(parents=True)
        (dest / "01 - Song 1.mp3").write_bytes(tiny_mp3())
        jpeg = b"\xff\xd8\xff\xe0" + bytes(100)
        (dest / "cover.jpg").write_bytes(jpeg)
        (dest / ".iafetch" / "b" / "iafetch.json").write_text(json.dumps({"processed": {}}))
        plan = Plan("b", "music", "", str(dest), moves=[[sel.files[0]["name"], "01 - Song 1.mp3"], ["x.jpg", "cover.jpg"]],
                    sidecar=".iafetch/b/iafetch.json")
        it.raw["_iafetch_label"] = "Bad Panda Records"
        self.assertEqual(finish(plan, it, sel, dest), 1)
        from mutagen import File as AudioFile
        tags = AudioFile(dest / "01 - Song 1.mp3", easy=True)
        self.assertEqual((tags["album"], tags["albumartist"], tags["title"], tags["tracknumber"], tags["date"], tags["organization"]),
                         (["Brick City Love Song"], ["Riding Alone"], ["Song 1"], ["1"], ["2009"], ["Bad Panda Records"]))
        self.assertEqual(ID3(dest / "01 - Song 1.mp3").getall("APIC")[0].data, jpeg)  # the cover, embedded
        self.assertEqual(list(json.loads((dest / ".iafetch" / "b" / "iafetch.json").read_text())["processed"]), ["01 - Song 1.mp3"])


if __name__ == "__main__":
    unittest.main()
