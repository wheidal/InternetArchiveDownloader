import json
import tempfile
import unittest
from pathlib import Path

from iafetch.api import Item
from iafetch.config import parse_config
from iafetch.handlers import choose, selection
from iafetch.handlers.software import Software, game_title, platform
from iafetch.rights import sidecar
from tests.support import file, item_json

# The shape of a game in archive.org's C64 software library
LAST_NINJA = [file("00_coverscreenshot.jpg", fmt="JPEG"), file("00_coverscreenshot_thumb.jpg", "derivative", "JPEG Thumb"),
              file("00cover_itemimage.jpg", fmt="Item Image"), file("Last_Ninja_Side_A.d64", fmt="Unknown"),
              file("Last_Ninja_Side_B.d64", fmt="Unknown"), file("history/files/Last_Ninja_Side_B.d64.~1~", fmt="Unknown"),
              file("Last_Ninja_screenshot.gif", fmt="Emulator Screenshot"), file("screenshot_00.jpg", fmt="JPEG"),
              file("__ia_thumb.jpg", fmt="Item Tile")]
META = {"mediatype": "software", "collection": ["softwarelibrary_c64_games", "softwarelibrary_c64", "softwarelibrary"],
        "title": "The Last Ninja", "creator": "System 3", "date": "1987\r", "emulator": "vice-resid",
        "emulator_ext": "d64", "emulator_start": "Last_Ninja_Side_A.d64"}


def item(files=LAST_NINJA, identifier="Last_Ninja_The_1987_System_3", **meta):
    return Item.from_json(identifier, item_json(identifier, files, **{**META, **meta}))


class SoftwareTest(unittest.TestCase):
    def test_software_gets_the_software_handler(self):
        self.assertEqual(choose(item(), parse_config({}))[0].name, "software")

    def test_the_disks_and_the_cover_never_history(self):
        names = [f["name"] for f in selection(Software(), item(), {}).files]
        self.assertEqual(names, ["Last_Ninja_Side_A.d64", "Last_Ninja_Side_B.d64", "00_coverscreenshot.jpg"])

    def test_platforms(self):
        self.assertEqual(platform(item()), "Commodore 64")
        self.assertEqual(platform(item(collection=["softwarecapsules_commodore"], title="TOSEC: Commodore C64 (2012)")),
                         "Commodore 64")
        self.assertEqual(platform(item(collection=["softwarelibrary_vic20"])), "Commodore VIC-20")
        self.assertEqual(platform(item(collection=["x"], title="Odd")), "Commodore 64")  # from emulator_ext d64
        self.assertEqual(platform(item(collection=["x"], title="Odd", emulator_ext="")), "Other")

    def test_titles(self):
        self.assertEqual(game_title(item()), "The Last Ninja (1987)")
        self.assertEqual(game_title(item(title="Ghosts 'n Goblins (1986)(Elite)", date="1986")), "Ghosts 'n Goblins (1986)")
        self.assertEqual(game_title(item(title="Oregon Trail", date="")), "Oregon Trail")
        self.assertEqual(game_title(item(title="The Last Ninja (Side A)")), "The Last Ninja (1987)")
        self.assertEqual(game_title(item(title="Zak McKracken (Disk 1 of 2 Side A)", date="1988")), "Zak McKracken (1988)")

    def test_plan_and_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            it = item()
            sel = selection(Software(), it, {}, config)
            plan = Software().plan(it, Path(tmp) / "_Inbox" / it.identifier, sel, config)
            self.assertTrue(plan.approved, plan.review)
            self.assertEqual(plan.dest, str((Path(tmp) / "Games" / "Commodore 64" / "The Last Ninja (1987)").resolve()))
            self.assertIn(["00_coverscreenshot.jpg", "cover.jpg"], plan.moves)
            self.assertIn(["__ia_thumb.jpg", ".iafetch/Last_Ninja_The_1987_System_3/__ia_thumb.jpg"], plan.moves)
            self.assertIn(["Last_Ninja_Side_B.d64", "Last_Ninja_Side_B.d64"], plan.moves)
            record = sidecar(it, sel.files, handler="software")
            self.assertEqual((record["emulator"], record["emulator_ext"]), ("vice-resid", "d64"))
            Path(plan.dest).mkdir(parents=True)  # a second copy of the same game is kept beside it
            again = Software().plan(it, Path(tmp) / "x", sel, config)
            self.assertTrue(again.dest.endswith("The Last Ninja (1987) [Last_Ninja_The_1987_System_3]"))
            self.assertTrue(again.approved)

    def test_nothing_to_run_goes_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = parse_config({"root": tmp})
            it = item([file("readme.txt", fmt="Text")])
            plan = Software().plan(it, Path(tmp) / "x", selection(Software(), it, {}, config), config)
            self.assertIn("no program file", plan.review[0])


if __name__ == "__main__":
    unittest.main()
