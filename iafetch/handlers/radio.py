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

AUDIO = (  # format names, most wanted first: archive.org's MP3s are what OTR and podcasts are shared as
    ("vbr mp3", "mp3", "32kbps mp3", "64kbps mp3", "128kbps mp3", "192kbps mp3", "256kbps mp3", "320kbps mp3"),
    ("m4a", "aac", "mpeg4 audio", "apple lossless audio"),
    ("flac", "24bit flac"),
    ("wave", "aiff"),
    ("ogg vorbis",),
)
WEBSITE = re.compile(r"(?i)\s*[-–|:]?\s*\b(?:https?://)?(?:www\.)?[\w-]+\.(?:com|org|net|info|co\.uk)\b/?")
SERIES_NOISE = re.compile(r"(?i)\s*(?:[-–:]\s*)?(?:single episodes|complete series|the complete|old time radio|"
                          r"\(?otr\)?|radio shows?|radio programs?|radio|collection|the \d0'?s)\s*$")
DATE_FORMS = (  # year, month, day (the second: month, day, year); two-digit years are the 1900s
    re.compile(r"(?<!\d)((?:19|20)?\d\d)[-._ ](0[1-9]|1[0-2])[-._ ](0[1-9]|[12]\d|3[01])(?!\d)"),   # 52-04-26, 1952-04-26
    re.compile(r"(?<!\d)(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d\d)(?!\d)"),                        # 04/26/52 (album tags)
    re.compile(r"^((?:19)?\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b"),                            # 481207 (title tags)
    re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])[-._ ](0[1-9]|1[0-2])[-._ ]((?:19|20)\d\d)(?!\d)"),         # 14_11_2009
    re.compile(r"(?<!\d)(0[1-9]|1[0-2])[-._ ](1[3-9]|2\d|3[01])[-._ ]((?:19|20)\d\d)(?!\d)"),            # 11-24-2009
)
# the order each pattern gives (year, month, day) in
DATE_GROUPS = ((1, 2, 3), (3, 1, 2), (1, 2, 3), (3, 2, 1), (3, 1, 2))
EPISODE_FORMS = (re.compile(r"\((\d{1,4})\)"), re.compile(r"(?i)\bep(?:isode)?\.?\s*(\d{1,4})\b"),
                 re.compile(r"^[A-Za-z]{2,6}\s?(\d{2,4})(?![\d-])"),  # EPDB001, CC472: (not YTJD 1948-12-07)
                 re.compile(r"^\s*(\d{1,4})(?:\s*[.):-]\s*|\s+)(?=\D)"))  # "1. El Muro", "001 Al-Fatihah"


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
        files = episode_files(selection.files)
        series = series_name(item, files)
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

    GENRE = "Old Time Radio"

    def episode_folder(self, ep: "Episode", any_dated: bool) -> str:
        """The folder an episode goes in, inside the series' folder ("" for the series' folder itself)."""
        if ep.date or ep.number is not None:
            return ep.date[:4] if ep.date else "Undated"
        return "Extras"  # a biography, an interview, a promo: not an episode

    def plan(self, item, staged, selection, config) -> Plan:
        series = series_name(item, selection.files)
        dest = config.inside(config.folder(self.kind(item)) / _short(series))
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        taken = set()
        episodes = [(f, episode(f, series, item)) for f in selection.files]
        any_dated = any(ep.date for _, ep in episodes)
        for f, ep in episodes:
            folder = self.episode_folder(ep, any_dated)
            if ep.date or ep.number is not None:
                name = f"{series}" + (f" {ep.date}" if ep.date else "") + (f" ({ep.number:03d})" if ep.number is not None else "")
                name += f" {ep.title}" if ep.title else ""
            else:
                name = ep.title or clean(PurePosixPath(f["name"]).stem)
            rel = (f"{folder}/" if folder else "") + f"{_short(name)}{PurePosixPath(f['name']).suffix.lower()}"
            if rel.lower() in taken or (dest / rel).exists():  # two files read the same: keep both
                rel = (f"{folder}/" if folder else "") + f"{_short(name)} [{PurePosixPath(f['name']).stem}]{PurePosixPath(f['name']).suffix.lower()}"
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
        tag_episodes(plan, item, selection, Path(dest), self.GENRE)
        return dest


# --- reading episodes ---------------------------------------------------------------------------------------

def series_name(item, files: list[dict] | None = None) -> str:
    """The series: the item's title without "- Single Episodes", "(OTR)" and the like; for a single-episode item
    ("Big Story 1952-04-09 Printed Word…"), what comes before the date. When the title is only the identifier
    ("rr12016"), the album its files are tagged with ("Case Closed!")."""
    title = item.title
    if files and (title == item.identifier or " " not in title.strip()):
        albums = [str(f.get("album") or "").strip() for f in files if str(f.get("album") or "").strip()]
        folders = [str(PurePosixPath(f["name"]).parent) for f in files if "/" in f["name"]]
        if albums:
            title = max(set(albums), key=albums.count)
        elif folders and len(set(folders)) == 1:  # "English Podcast Elementary/englishpod_0149pb.mp3"
            title = folders[0]
    title = WEBSITE.sub("", title)  # "Maher Al-Muaiqly- TvQuran.com"
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
    for pattern, (yg, mg, dg) in zip(DATE_FORMS, DATE_GROUPS):
        m = pattern.search(text)
        if m:
            y, mo, d = m.group(yg), m.group(mg), m.group(dg)
            return f"{('19' + y) if len(y) == 2 else y}-{mo}-{d}"
    return ""


def _number(text: str, date: str) -> int | None:
    for pattern in EPISODE_FORMS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    m = re.search(r"(?<![\d-])(?:\d\d|\d{4})[-._ ]\d\d[-._ ]\d\d[ _-]*(\d{3,4})(?!\d)", text)  # 58-11-02_343_Old
    if m:
        return int(m.group(1))
    if not date:  # a bare number in a name with no date: englishpod_0149pb, Mishary/01
        m = re.search(r"(?<![\d.-])(\d{2,4})(?![\d-])", text)
        return int(m.group(1)) if m else None
    return None


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
    text = re.sub(r"(?i)^\s*(?:\((?:\d{1,4}|x+)\)|ep(?:isode)?\.?\s*\d{1,4}|\d{1,4}[.):]?|x{3}|[a-z]{2,6}\d{2,4}:?)(?=\s|$)",
                  "", text.strip())
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

def tag_episodes(plan: Plan, item, selection: Selection, dest: Path, genre: str = "Old Time Radio") -> int:
    """Tag the filed episodes (album = series, title, date, track = episode), if mutagen is installed, and
    record the tagged files' MD5s. How many were tagged."""
    if not tags.available():
        return 0
    series = series_name(item, selection.files)
    by_name = {f["name"]: f for f in selection.files}
    processed = {}
    for src, rel in plan.moves:
        f = by_name.get(src)
        path = safe_path(dest, rel)  # the name it was filed under
        if f is None or not path.is_file():
            continue
        ep = episode(f, series, item)
        if tags.write(path, {"album": series, "artist": series, "title": ep.title or path.stem, "genre": genre,
                             "date": ep.date, "tracknumber": "" if ep.number is None else str(ep.number)}):
            processed[rel] = tags.md5(path)
    tags.record_processed(dest / plan.sidecar, processed)
    return len(processed)


def _short(name: str, limit: int = 150) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]



def episode_files(files: list[dict]) -> list[dict]:
    """The episodes as uploaded, in the most wanted format there is, one copy of each. But when the only uploads
    are WAV (a speech recorded straight to disk), archive.org's MP3 of each instead: its VBR copy, else its
    64 kbps one (never both)."""
    audio = [f for f in files if any(f.get("format", "").lower() in formats for formats in AUDIO)]
    originals = [f for f in audio if f.get("source") == "original"]
    lossless_only = originals and all(f.get("format", "").lower() in ("wave", "aiff") for f in originals)
    derived_mp3 = [f for f in audio if f.get("source") == "derivative" and f.get("format", "").lower() in AUDIO[0]]
    if originals and not (lossless_only and derived_mp3):
        pool = originals
    else:
        pool = derived_mp3 or audio
    for formats in AUDIO:
        found = [f for f in pool if f.get("format", "").lower() in formats]
        if found:
            break
    else:
        return []
    by_source: dict[str, dict] = {}  # one copy per uploaded file: the VBR copy before the 64 kbps one
    for f in sorted(found, key=lambda f: ("64kbps" in f.get("format", "").lower(), f["name"])):
        key = f.get("original") or f["name"] if f.get("source") == "derivative" else f["name"]
        by_source.setdefault(str(key), f)
    return list(by_source.values())


# --- podcasts and spoken word ---------------------------------------------------------------------------------

PODCASTS = {"podcasts", "podcasts_miscellaneous", "podcasts_compilations"}
SPOKEN = {"audio_religion", "audio_sermons", "audio_islamic", "opensource_religionaudio"}


class Podcast(Radio):
    """Podcasts, and talks, sermons and recitations: series of episodes, read as radio's are, filed in
    Podcasts/{show}/ or Spoken Word/{speaker or series}/. Dated episodes go in year folders; numbered or plain
    ones sit in the show's folder (there's no "Undated"); only an item that mixes dated episodes with other files
    puts those in Extras/."""
    name = "podcast"
    GENRE = "Podcast"

    def kind(self, item) -> str:
        return "spoken" if SPOKEN.intersection(item.collections) else "podcasts"

    def episode_folder(self, ep: Episode, any_dated: bool) -> str:
        if ep.date:
            return ep.date[:4]
        return "Extras" if any_dated and ep.number is None else ""
