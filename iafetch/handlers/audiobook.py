"""Audiobooks and spoken word (§6.6): LibriVox, and the rest of archive.org's audio_bookspoetry collections.

Nearly every LibriVox item (21,890 of 21,913) has an M4B already built, with chapters; long books come in parts
(Moby Dick: five). When there is one, the M4B is all that's taken: no chapter MP3s as well, nothing converted.
Other spoken word comes as chapter files: the uploaded MP3s (else FLAC, else archive.org's 64 kbps copies, else
Ogg), in track order, renamed so they sort. Filed for Audiobookshelf and Jellyfin, which both read this:

    Audiobooks/{author}/{title}/{title}.m4b (or "- Part 1.m4b"…, or "01 - {chapter}.mp3"…)
                                cover.jpg, desc.txt (the description), reader.txt (the narrator)

The narrator comes from the description ("Read by Moira Fogarty."). The item's records go in .iafetch/<id>/.
"""

import html
import re
from dataclasses import replace
from pathlib import PurePosixPath

from ..select import TILE, Profile, Selection, size_of
from .base import Handler, Plan, clean

AUDIOBOOK = "audiobook"  # archive.org's format name for an M4B
# Chapter files, most wanted first: (format names, originals only)
CHAPTERS = (
    (("vbr mp3", "mp3", "128kbps mp3", "192kbps mp3", "256kbps mp3", "320kbps mp3"), True),
    (("flac", "24bit flac"), True),
    (("64kbps mp3", "128kbps mp3", "vbr mp3", "mp3"), False),
    (("ogg vorbis",), False),
)
COVER_FORMATS = ("jpeg", "png")
READER = re.compile(r"(?i)\b(?:read|narrated|performed)\s+by:?\s+([^.\n]{3,100}?)\s*(?:\.\s|\.$|\n|$)|"
                    r"\breaders?:\s*([^.\n]{3,100})")


class Audiobook(Handler):
    name = "audiobook"

    def kind(self, item) -> str:
        return "audiobooks"

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)  # some items' only MP3s are archive.org's

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        m4b = [f for f in selection.files if _fmt(f) == AUDIOBOOK or f["name"].lower().endswith(".m4b")]
        files = sorted(m4b, key=lambda f: natural(f["name"])) if m4b else chapters(selection.files)
        art = cover(selection.files)
        return replace(selection, files=files + ([art] if art else []))

    def plan(self, item, staged, selection, config) -> Plan:
        author, title, narrator = credits(item)
        folder = config.folder("audiobooks") / _short(author)
        dest = config.inside(folder / _short(title))
        if dest.exists():  # another recording of the same book: keep both
            dest = config.inside(folder / f"{_short(title)} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        audio = [f for f in selection.files if not _is_image(f)]
        m4b = [f for f in audio if f["name"].lower().endswith(".m4b")]
        if m4b:
            for n, f in enumerate(m4b, 1):
                plan.moves.append([f["name"], f"{_short(title)}{f' - Part {n}' if len(m4b) > 1 else ''}.m4b"])
        elif len(audio) == 1:
            plan.moves.append([audio[0]["name"], _short(title) + _ext(audio[0])])
        else:
            width = max(2, len(str(len(audio))))
            for n, f in enumerate(audio, 1):
                plan.moves.append([f["name"], f"{n:0{width}d} - {_short(chapter_title(f))}{_ext(f)}"])
        images = [f for f in selection.files if _is_image(f)]
        for f in images:
            plan.moves.append([f["name"], "cover" + _ext(f)])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}" if images else "cover.jpg"])
        description = describe(item)
        if description:
            plan.writes["desc.txt"] = description + "\n"
        if narrator:
            plan.writes["reader.txt"] = narrator + "\n"
        if not audio:
            plan.review.append("no audio to take (try --as generic)")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        return file_plan(plan, item, selection, config, self.name)


def chapters(files: list[dict]) -> list[dict]:
    """The chapter files in the most wanted format there is, in track order."""
    for formats, originals_only in CHAPTERS:
        found = [f for f in files if _fmt(f) in formats and (f.get("source") == "original" or not originals_only)]
        if found:
            return sorted(found, key=lambda f: (_track(f), natural(f["name"])))
    return []


def cover(files: list[dict]) -> dict | None:
    """The uploaded cover image (the largest), not a thumbnail or archive.org's own pictures."""
    images = [f for f in files if _is_image(f)]
    return max(images, key=size_of) if images else None


def _is_image(f: dict) -> bool:
    return (_fmt(f) in COVER_FORMATS and f.get("source") == "original"
            and not re.search(r"(?i)_thumb|spectrogram", f["name"]))


def _track(f: dict) -> int:
    m = re.match(r"\s*(\d+)", str(f.get("track") or ""))
    return int(m.group(1)) if m else 1 << 30


def natural(name: str) -> list:
    """Sort key putting chapter2 before chapter10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def credits(item) -> tuple[str, str, str]:
    """(author, title, narrator). The creator is usually the author, the narrator in the description ("Read by
    …"). But uploaders vary: "Anton Chekhov - Collected Stories" by creator Alan Davis Drake names the author in
    the title, and then the creator is the narrator; "Alice's Adventures, by Lewis Carroll" repeats the author."""
    creators = [c for c in item.values("creator") if c.strip()]
    creator = person(creators[0]) if creators else ""
    title, narrator = item.title.strip(), reader_of(item)
    words = lambda text: set(re.findall(r"[a-z]{3,}", text.lower()))  # noqa: E731
    m = re.search(r"(?i),?\s+by\s+(.+)$", title)
    if m and creator and words(creator) & words(m.group(1)):
        title = title[:m.start()]
    # "Anton Chekhov - Collected Stories" by creator Alan Davis Drake: the title names the author when that name
    # is in the description and the creator is in neither (Walden: or Life in the Woods, by Thoreau, isn't)
    m = re.match(r"^([^-–—:]{3,40}?)\s+[-–—]\s+(.+)$", title)
    description = describe(item).lower()
    if (m and 2 <= len(m.group(1).split()) <= 4 and m.group(1).lower() in description
            and not (creator and words(creator) & (words(title) | words(description)))):
        return clean(person(m.group(1))), clean(m.group(2)), narrator or clean(creator)
    return clean(creator) if creator else "Unknown", clean(title), narrator


def person(name: str) -> str:
    """A name as it's said: "Collins, Wilkie, 1824-1889" -> "Wilkie Collins"."""
    name = re.sub(r",?\s*\(?\b\d{3,4}\s*-\s*(\d{3,4})?\)?\s*$", "", name.strip()).strip(" ,")
    m = re.fullmatch(r"([^,]+),\s*([^,]+)", name)
    return f"{m.group(2).strip()} {m.group(1).strip()}" if m else name


def chapter_title(f: dict) -> str:
    """A chapter's title from its tag, else its file name: no "01 -" in front, no "[08:46]" or "*" around it."""
    raw = str(f.get("title") or "").strip()
    if not raw or raw.lower() in ("untitled", "track"):
        raw = PurePosixPath(f["name"]).stem.replace("_", " ")
    raw = re.sub(r"\s*[\[(]\d{1,2}:\d{2}(?::\d{2})?[\])]\s*$", "", raw).strip(" *#")
    return clean(re.sub(r"^\d+\s*[-.:]?\s*", "", raw)) or clean(raw)


def describe(item) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", item.field("description"))
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def reader_of(item) -> str:
    """The narrator, from the description: "Read by Moira Fogarty." -> "Moira Fogarty"."""
    m = READER.search(describe(item))
    return clean(m.group(1) or m.group(2)).rstrip(",;") if m else ""


def _fmt(f: dict) -> str:
    return f.get("format", "").lower()


def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _short(name: str, limit: int = 120) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]
