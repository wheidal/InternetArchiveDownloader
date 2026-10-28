"""Software: games and programs for old computers and consoles (§6.9).

The program files are kept exactly as published: a disk or tape image is what an emulator runs, and an
arcade or TOSEC set only works as its makers packed it. Each title gets a folder (a game can be several
disks), with the cover screenshot as cover.jpg:

    Games/{platform}/{title} ({year})/River_Raid_1984_Activision.d64, cover.jpg

The item's emulator settings (emulator, emulator_ext, emulator_start) are kept in its iafetch.json record,
in .iafetch/<identifier>/ beside it.
"""

import re
from dataclasses import replace
from pathlib import PurePosixPath

from ..select import TILE, Profile, Selection
from .base import Handler, Plan, clean

PROGRAM_EXT = {
    ".d64", ".d71", ".d81", ".g64", ".g71", ".nib", ".t64", ".tap", ".crt", ".prg", ".p00", ".x64",  # Commodore
    ".adf", ".dms", ".ipf", ".hdf", ".lha",  # Amiga
    ".tzx", ".z80", ".sna", ".dsk", ".cdt", ".atr", ".xex", ".cas", ".do", ".po", ".2mg", ".woz",
    ".nes", ".sfc", ".smc", ".gb", ".gbc", ".gba", ".md", ".gen", ".sms", ".gg", ".a26", ".a78", ".lnx",
    ".pce", ".n64", ".z64", ".v64", ".iso", ".cue", ".bin", ".img", ".chd", ".m3u",
    ".zip", ".7z", ".rar",
}
COVER_NAMES = ("00_coverscreenshot.jpg", "00_coverscreenshot.png", "cover.jpg", "cover.png", "box.jpg")
# Collection names, and the platform folder they mean (most specific first)
PLATFORMS = [
    ("c128", "Commodore 128"), ("vic20", "Commodore VIC-20"), ("vic-20", "Commodore VIC-20"),
    ("plus4", "Commodore Plus-4"), ("c64", "Commodore 64"), ("commodore 64", "Commodore 64"),
    ("amiga", "Commodore Amiga"), ("zx_spectrum", "ZX Spectrum"), ("zx spectrum", "ZX Spectrum"),
    ("cpc", "Amstrad CPC"), ("apple2", "Apple II"), ("apple_ii", "Apple II"), ("atari_800", "Atari 8-bit"),
    ("atari_2600", "Atari 2600"), ("msdos", "MS-DOS"), ("dos", "MS-DOS"), ("nes", "NES"), ("snes", "SNES"),
    ("megadrive", "Sega Mega Drive"), ("genesis", "Sega Mega Drive"), ("arcade", "Arcade"),
]
EXT_PLATFORMS = {".d64": "Commodore 64", ".t64": "Commodore 64", ".crt": "Commodore 64", ".g64": "Commodore 64",
                 ".prg": "Commodore 64", ".tap": "Commodore 64", ".adf": "Commodore Amiga", ".tzx": "ZX Spectrum"}
# A TOSEC-style title: "Name (1985)(Publisher)"
TOSEC = re.compile(r"\s*\((\d{4}|19xx)[^)]*\)\s*(\([^)]*\))*\s*$")
# "(Side A)", "(Disk 1 of 2)": the folder holds the whole game
DISK_PART = re.compile(r"(?i)\s*\((side [a-d]|disk \d+( of \d+)?)[^)]*\)")


class Software(Handler):
    name = "software"

    def kind(self, item) -> str:
        return "games"

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=True)

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        programs = [f for f in selection.files if _ext(f["name"]) in PROGRAM_EXT]
        cover = next((f for name in COVER_NAMES for f in selection.files if f["name"].lower() == name), None)
        return replace(selection, files=programs + ([cover] if cover else []))

    def plan(self, item, staged, selection, config) -> Plan:
        name = game_title(item)
        folder = config.folder("games") / platform(item)
        dest = config.inside(folder / name)
        if dest.exists():  # another copy or version of the same title: keep both
            dest = config.inside(folder / f"{name} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        for f in selection.files:
            base = PurePosixPath(f["name"]).name
            plan.moves.append([f["name"], "cover" + _ext(base) if base.lower() in COVER_NAMES else f["name"]])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:  # archive.org's thumbnail: the cover only if the item has no cover screenshot
            has_cover = any(m[1].startswith("cover.") for m in plan.moves)
            plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}" if has_cover else "cover.jpg"])
        if not any(_ext(f["name"]) in PROGRAM_EXT for f in selection.files):
            plan.review.append("no program file to take (try --as generic)")
        if dest.exists():
            plan.review.append(f"{dest} already exists")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        return file_plan(plan, item, selection, config, self.name)


def _ext(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def platform(item) -> str:
    """The platform folder: from the item's collections, else its title, else its emulator's file type."""
    for text in (" ".join(item.collections).lower(), item.title.lower()):
        for key, name in PLATFORMS:
            if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])|_{re.escape(key)}\b", text):
                return name
    ext = "." + item.field("emulator_ext").strip().lstrip(".").lower()
    return EXT_PLATFORMS.get(ext, "Other")


def game_title(item) -> str:
    title = DISK_PART.sub("", TOSEC.sub("", item.title))
    year = item.year
    return clean(f"{title} ({year})" if year and year not in title else title)
