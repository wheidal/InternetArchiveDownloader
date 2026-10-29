"""Old-time radio and other series audio (§6.5).

An OTRR set is one item per series with hundreds of episodes (Gunsmoke: 487, Suspense: 35 GB); other uploads
hold a series or a single episode. Names vary even inside one item ("Gunsmoke 52-04-26 (001) Billy the
Kid.mp3", "Gunsmoke_58-11-02_343_Old_Mans_Gold.MP3", "XMinusOne55-04-24001NoContact.mp3"), and so do the tags
(some clean, some cut off at 40 characters, some missing). So each episode's date, number and title are read
from the file name, the title and album tags and, for a single-episode item, its title, each where it's best:

    Radio/{series}/{year}/{series} 1952-04-26 (001) Billy the Kid.mp3
    Radio/{series}/Extras/Howard McNear Biography.mp3      (no date or number: not an episode)

Episodes are chosen with --episodes 1-52 or --years 1952-1953. With mutagen installed (the `audio` extra) each
file is tagged: album = series, title, date, track = episode number; the processed files' MD5s go in the record.
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .. import tags
from ..download import safe_path
from ..select import TILE, Profile, Selection
from .base import Handler, Plan, clean

AUDIO = (  # format names, most wanted first: archive.org's MP3s are what OTR is shared as
    ("vbr mp3", "mp3", "32kbps mp3", "64kbps mp3", "128kbps mp3", "192kbps mp3", "256kbps mp3", "320kbps mp3"),
    ("flac", "24bit flac"),
    ("ogg vorbis",),
)
SERIES_NOISE = re.compile(r"(?i)\s*(?:[-–:]\s*)?(?:single episodes|complete series|the complete|old time radio|"
                          r"\(?otr\)?|radio shows?|radio programs?|radio|collection|the \d0'?s)\s*$")
DATE_FORMS = (  # year, month, day (the second: month, day, year); two-digit years are the 1900s
    re.compile(r"(?<!\d)((?:19|20)?\d\d)[-._ ](0[1-9]|1[0-2])[-._ ](0[1-9]|[12]\d|3[01])(?!\d)"),   # 52-04-26, 1952-04-26
    re.compile(r"(?<!\d)(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d\d)(?!\d)"),                        # 04/26/52 (album tags)
    re.compile(r"^((?:19)?\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b"),                            # 481207 (title tags)
)
EPISODE_FORMS = (re.compile(r"\((\d{1,4})\)"), re.compile(r"(?i)\bep(?:isode)?\.?\s*(\d{1,4})\b"))


@dataclass
class Episode:
    date: str  # "1952-04-26", or ""
    number: int | None
    title: str


class Radio(Handler):
    name = "radio"

    def kind(self, item) -> str:
        return "radio"

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        seen, files = set(), []
        for formats in AUDIO:
            for f in selection.files:
                if f.get("format", "").lower() in formats and f["name"] not in seen:
                    stem = PurePosixPath(f["name"]).stem.lower()
                    if f.get("source") == "derivative" and any(PurePosixPath(g["name"]).stem.lower() == stem for g in files):
                        continue
                    seen.add(f["name"])
                    files.append(f)
            if files:
                break
        series = series_name(item)
        return replace(selection, files=sorted(files, key=lambda f: _order(episode(f, series, item))))

    def choose_episodes(self, item, selection: Selection, options: dict) -> Selection:
        """--episodes 1-52,60 and --years 1952-1953."""
        numbers, years = parse_ranges(options.get("episodes", "")), parse_ranges(options.get("years", ""))
        series = series_name(item)
        keep = []
        for f in selection.files:
            ep = episode(f, series, item)
            if numbers is not None and (ep.number is None or ep.number not in numbers):
                continue
            if years is not None and (not ep.date or int(ep.date[:4]) not in years):
                continue
            keep.append(f)
        return replace(selection, files=keep)

    def plan(self, item, staged, selection, config) -> Plan:
        series = series_name(item)
        dest = config.inside(config.folder("radio") / _short(series))
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        taken = set()
        for f in selection.files:
            ep = episode(f, series, item)
            if ep.date or ep.number is not None:
                name = f"{series}" + (f" {ep.date}" if ep.date else "") + (f" ({ep.number:03d})" if ep.number is not None else "")
                name += f" {ep.title}" if ep.title else ""
                folder = ep.date[:4] if ep.date else "Undated"
            else:  # a biography, an interview, a promo: not an episode
                name, folder = ep.title or clean(PurePosixPath(f["name"]).stem), "Extras"
            rel = f"{folder}/{_short(name)}{PurePosixPath(f['name']).suffix.lower()}"
            if rel.lower() in taken or (dest / rel).exists():  # two files read the same: keep both
                rel = f"{folder}/{_short(name)} [{PurePosixPath(f['name']).stem}]{PurePosixPath(f['name']).suffix.lower()}"
            taken.add(rel.lower())
            plan.moves.append([f["name"], rel])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, "cover.jpg" if not (dest / "cover.jpg").exists() else f".iafetch/{item.identifier}/{TILE}"])
        if not selection.files:
            plan.review.append("no episodes to take (check --episodes/--years, or try --as generic)")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        dest = file_plan(plan, item, selection, config, self.name)
        tag_episodes(plan, item, selection, Path(dest))
        return dest


# --- reading episodes ---------------------------------------------------------------------------------------

def series_name(item) -> str:
    """The series: the item's title without "- Single Episodes", "(OTR)" and the like; for a single-episode item
    ("Big Story 1952-04-09 Printed Word…"), what comes before the date."""
    title = item.title
    for pattern in DATE_FORMS:
        m = pattern.search(title)
        if m and m.start() > 2:
            title = title[:m.start()]
            break
    prev = None
    while prev != title:
        prev, title = title, SERIES_NOISE.sub("", title).strip(" -–:,")
    return clean(title)


def episode(f: dict, series: str, item=None) -> Episode:
    """An episode's date, number and title, from the file name and its tags."""
    stem = PurePosixPath(f["name"]).stem
    tag_title, album = str(f.get("title") or "").strip(), str(f.get("album") or "").strip()
    readable = _split_words(stem.replace("_", " "))
    sources = [readable, album, tag_title] + ([item.title] if item is not None and len(item.files) <= 12 else [])
    date = next((d for d in (_date(s) for s in sources) if d), "")
    number = next((n for n in (_number(s, date) for s in (readable, album, tag_title)) if n is not None), None)
    from_name = _rest(readable, series, date)
    from_tag = _rest(_split_words(tag_title), series, date) if tag_title else ""
    a, b = _letters(from_tag), _letters(from_name)
    if from_tag and (a == b or not b or not b.startswith(a)):
        title = from_tag  # the tag is whole: it has the punctuation file names lose ("Old Man's Gold")
    else:
        title = from_name  # the tag was cut off ("The Old Fashioned Murder Matt")
    if title.islower() or title.isupper():
        title = re.sub(r"\b(\w)(\w*)", lambda m: m.group(1).upper() + m.group(2).lower(), title)
    return Episode(date, number, clean(title) if title else "")


def _date(text: str) -> str:
    for n, pattern in enumerate(DATE_FORMS):
        m = pattern.search(text)
        if m:
            y, mo, d = (m.group(3), m.group(1), m.group(2)) if n == 1 else m.groups()
            return f"{('19' + y) if len(y) == 2 else y}-{mo}-{d}"
    return ""


def _number(text: str, date: str) -> int | None:
    for pattern in EPISODE_FORMS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    m = re.search(r"(?<![\d-])(?:\d\d|\d{4})[-._ ]\d\d[-._ ]\d\d[ _-]*(\d{3,4})(?!\d)", text)  # 58-11-02_343_Old
    return int(m.group(1)) if m else None


def _rest(text: str, series: str, date: str) -> str:
    """The episode title in a name or tag: whatever follows the date and the episode number ("Gunsmoke 52-04-26
    (001) Billy the Kid", "YTJD 1948-12-07 000 Milford Brooks III"); with no date, the text without a leading
    series name."""
    text = re.sub(r"^\s*\d{6}\s+\d{1,4}\s+", "", text)  # "481207 000 Milford Brooks" (title tags)
    for pattern in DATE_FORMS:
        m = pattern.search(text)
        if m:
            text = text[m.end():]
            break
    else:
        if series and _letters(text).startswith(_letters(series)):
            n, want = 0, len(_letters(series))
            for i, ch in enumerate(text):
                n += ch.isalnum()
                if n == want:
                    text = text[i + 1:]
                    break
    text = re.sub(r"(?i)^\s*(?:\((?:\d{1,4}|x+)\)|ep(?:isode)?\.?\s*\d{1,4}|\d{3,4}|x{3})(?=\s|$)", "", text.strip())
    return re.sub(r"\s+", " ", text).strip(" -–_,.")


def _split_words(text: str) -> str:
    """ "XMinusOne55-04-24001NoContact" -> "X Minus One 55-04-24 001 No Contact". Only a name with no spaces is
    split ("Howard McNear Biography" stays as it is)."""
    if " " in text.strip():
        return text
    text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    m = re.search(r"(\d\d-\d\d-\d\d)(\d{3,4})\b", text)  # a date run into the episode number
    return text[:m.end(1)] + " " + text[m.end(1):] if m else text


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _order(ep: Episode) -> tuple:
    return (ep.date or "9999", ep.number if ep.number is not None else 1 << 30, ep.title)


def parse_ranges(text: str) -> set[int] | None:
    """ "1-52,60" -> {1..52, 60}; "" -> None (everything)."""
    if not str(text).strip():
        return None
    out: set[int] = set()
    for part in str(text).split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", part)
        if not m:
            raise ValueError(f"{text!r}: give numbers and ranges, e.g. 1-52,60")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(range(lo, hi + 1))
    return out


# --- tagging ----------------------------------------------------------------------------------------------

def tag_episodes(plan: Plan, item, selection: Selection, dest: Path) -> int:
    """Tag the filed episodes (album = series, title, date, track = episode), if mutagen is installed, and
    record the tagged files' MD5s. How many were tagged."""
    if not tags.available():
        return 0
    series = series_name(item)
    by_name = {f["name"]: f for f in selection.files}
    processed = {}
    for src, rel in plan.moves:
        f = by_name.get(src)
        path = safe_path(dest, rel)  # the name it was filed under
        if f is None or not path.is_file():
            continue
        ep = episode(f, series, item)
        if tags.write(path, {"album": series, "artist": series, "title": ep.title or path.stem, "genre": "Old Time Radio",
                             "date": ep.date, "tracknumber": "" if ep.number is None else str(ep.number)}):
            processed[rel] = tags.md5(path)
    tags.record_processed(dest / plan.sidecar, processed)
    return len(processed)


def _short(name: str, limit: int = 150) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]
