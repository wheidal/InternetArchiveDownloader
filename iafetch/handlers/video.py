"""Films and TV (§6.7), filed the way Jellyfin and Plex expect.

archive.org often has one film in several versions (the uploaded MPEG2, a HiRes MP4, its own h.264 and Ogg
encodes, old RealMedia), and a TV item holds a whole series. So the handler groups the files that are the
same video, takes one of each, and names them:

    Movies/{title} ({year})/{title} ({year}).mp4    with .srt/.vtt subtitles, movie.nfo and poster.jpg
    TV/{series}/Season {ss}/{series} S{ss}E{ee} - {episode title}.mp4    with tvshow.nfo and poster.jpg

The item's records (iafetch.json, _meta.xml, _files.xml) go in a hidden .iafetch/<identifier>/ folder
beside them, so a series filled from several items keeps each item's record. A film without a year, or
episodes that can't be numbered (or are numbered twice), are written as a plan for review.
"""

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from ..select import TILE, Profile, Selection, size_of
from .base import Handler, Plan

VIDEO_EXT = {".mp4", ".m4v", ".mpeg", ".mpg", ".avi", ".mkv", ".mov", ".wmv", ".webm", ".flv", ".rm", ".ogv",
             ".m2ts", ".ts", ".vob"}
UNPLAYABLE = {".ogv", ".rm"}  # Ogg encodes and RealMedia: taken only when there's nothing else
MP4 = {".mp4", ".m4v"}
H264 = ("h.264", "h.264 ia", "h.264 hd", "512kb mpeg4", "mpeg4")
SUB_EXT = {".srt", ".vtt"}
# Suffixes archive.org (and uploaders) give other versions of the same video
VARIANT = re.compile(r"(?i)(\.ia|_512kb|_256kb|_64kb|_edit|_hq|_lq|_access|\.asr|_ipod|_iphone|_mobile|_sd|_hd|_web|"
                     r"_small|_large)$")
LANG = re.compile(r"(?i)\.([a-z]{2,3}(?:-[a-z]{2})?)$")

EPISODE = re.compile(r"(?i)(?<!\d)s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)")
CROSS = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)")
NUMBERED = re.compile(r"(?i)(?:^|[ _.-])(?:part|pt|ep|episode|chapter)?[ _.-]*(\d{1,3})$")
# Uploaders' tags in a film's title: "(English subs, HD)", "[1080p]", "(Remastered)"
TITLE_TAGS = re.compile(r"(?i)\s*[(\[][^)\]]*\b(subs?|subtitled|subtitles|untertitel|hd|1080p|720p|480p|dvdrip|"
                        r"remaster(ed)?|restored)\b[^)\]]*[)\]]")
ARTICLE = re.compile(r"^(.+), (The|A|An|Der|Die|Das|Le|La|Les|El)$")
SERIES_NOISE = re.compile(r"(?i)\s*(\((mini-?series|tv series|tv)\)|\b(the )?complete series\b|\btv show\b|"
                          r"\btv series\b|\bpublic domain episodes\b|\bepisodes\b)\s*")


class Video(Handler):
    name = "video"

    def kind(self, item) -> str:
        """tv when the item holds several videos, most of them numbered as episodes; else one film."""
        groups = _groups(item.files)
        numbered = sum(1 for files in groups.values() if episode(files[0]["name"]) is not None)
        return "tv" if len(groups) > 1 and numbered * 2 > len(groups) else "movies"

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)  # the best playable copy is often archive.org's own h.264

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        settings = (config.settings.get("video") if config else None) or {}
        quality, subs = settings.get("quality", "h264"), settings.get("subtitles", True)
        groups = _groups(selection.files)
        chosen = [pick(files, quality) for files in groups.values()]
        if subs:
            kept: dict[tuple[str, str], dict] = {}  # one per video and language: e.g. not the _ipod copy's too
            for f in selection.files:
                if _is_sub(f) and (_stem(f) in groups or len(groups) == 1):
                    key = (_stem(f), _sub_suffix(f))
                    if key not in kept or size_of(f) > size_of(kept[key]):
                        kept[key] = f
            chosen += kept.values()
        return replace(selection, files=sorted(chosen, key=lambda f: f["name"]))

    def plan(self, item, staged, selection, config) -> Plan:
        if self.kind(item) == "tv":
            return self._tv(item, staged, selection, config)
        return self._film(item, staged, selection, config)

    def _film(self, item, staged, selection, config) -> Plan:
        title, year = film_title(item), item.year
        name = f"{title} ({year})" if year else title
        dest = config.inside(config.folder("movies") / name)
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=_record(item, "iafetch.json"))
        videos = [f for f in selection.files if not _is_sub(f)]
        for f in videos:
            plan.moves.append([f["name"], name + _ext(f)])
        for f in selection.files:
            if _is_sub(f):
                plan.moves.append([f["name"], name + _sub_suffix(f)])
        _records(plan, item, selection, "poster.jpg")
        if (config.settings.get("video") or {}).get("nfo", True):
            plan.writes["movie.nfo"] = movie_nfo(item, videos[0] if videos else None)
        if not year:
            plan.review.append("no year for the film: add it to dest and the file names")
        if len(videos) != 1:
            plan.review.append(f"{len(videos)} videos where one film was expected")
        if dest.exists():
            plan.review.append(f"{dest} already exists")
        plan.approved = not plan.review
        return plan

    def _tv(self, item, staged, selection, config) -> Plan:
        series = series_name(item)
        dest = config.inside(config.folder("tv") / series)
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=_record(item, "iafetch.json"))
        videos = [f for f in selection.files if not _is_sub(f)]
        names, seen = {}, defaultdict(list)
        for f in videos:
            ep = episode(f["name"], series)
            if ep is None:
                plan.review.append(f"no episode number in {f['name']}")
                ep = (0, len(names) + 1, clean(PurePosixPath(f["name"]).stem))
            season, number, title = ep
            seen[(season, number)].append(f["name"])
            base = f"Season {season:02d}/{series} S{season:02d}E{number:02d}" + (f" - {title}" if title else "")
            names[_stem(f)] = base
            plan.moves.append([f["name"], base + _ext(f)])
        for (season, number), files in sorted(seen.items()):
            if len(files) > 1:
                plan.review.append(f"S{season:02d}E{number:02d} is more than one file: {', '.join(files)}")
        for f in selection.files:
            if _is_sub(f) and _stem(f) in names:
                plan.moves.append([f["name"], names[_stem(f)] + _sub_suffix(f)])
        _records(plan, item, selection, "poster.jpg")
        if (config.settings.get("video") or {}).get("nfo", True):
            plan.writes["tvshow.nfo"] = tvshow_nfo(item, series)
        for _, rel in plan.moves:
            if (dest / rel).exists() and not rel.startswith(".iafetch/") and rel != "poster.jpg":
                plan.review.append(f"{rel} is already in {dest}")
        if dest.joinpath("poster.jpg").exists():  # the series' first item already gave it one
            plan.moves = [m for m in plan.moves if m[1] != "poster.jpg"]
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        return file_plan(plan, item, selection, config, self.name)


def _records(plan: Plan, item, selection, tile_as: str) -> None:
    for f in selection.record:
        plan.moves.append([f["name"], _record(item, PurePosixPath(f["name"]).name)])
    if selection.tile:
        plan.moves.append([TILE, tile_as])


def _record(item, name: str) -> str:
    return f".iafetch/{item.identifier}/{name}"


# --- grouping and picking ---------------------------------------------------------------------------

def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _is_video(f: dict) -> bool:
    return _ext(f) in VIDEO_EXT and "gif" not in f.get("format", "").lower()


def _is_sub(f: dict) -> bool:
    return _ext(f) in SUB_EXT and size_of(f) > 0


def _stem(f: dict) -> str:
    """What makes files the same video: the name without extension, version suffix or language. A
    derivative is grouped with the original it was made from."""
    name = f.get("original") if f.get("source") == "derivative" and f.get("original") else f["name"]
    stem = _unvaried(str(PurePosixPath(name).with_suffix("")))
    return _unvaried(LANG.sub("", stem)) if _ext(f) in SUB_EXT else stem


def _unvaried(stem: str) -> str:
    while VARIANT.search(stem):
        stem = VARIANT.sub("", stem)
    return stem


def _groups(files: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        if _is_video(f):
            groups[_stem(f)].append(f)
    return dict(groups)


def pick(files: list[dict], quality: str = "h264") -> dict:
    """One of several versions of the same video.

    h264: an MP4 that plays everywhere: the uploaded MP4 if there is one, else archive.org's h.264.
    original: the uploaded file (the largest, if several), unless it's RealMedia or Ogg.
    smallest: the smallest playable one.
    """
    playable = [f for f in files if _ext(f) not in UNPLAYABLE] or files
    mp4 = [f for f in playable if _ext(f) in MP4]
    if quality == "smallest":
        return min(mp4 or playable, key=size_of)
    originals = [f for f in playable if f.get("source") == "original"]
    if quality == "original" and originals:
        return max(originals, key=size_of)
    mp4_originals = [f for f in mp4 if f.get("source") == "original"]
    if mp4_originals:
        return max(mp4_originals, key=size_of)
    for fmt in H264:
        found = [f for f in mp4 if f.get("format", "").lower() == fmt]
        if found:
            return max(found, key=size_of)
    return max(originals or playable, key=size_of)


def _sub_suffix(f: dict) -> str:
    lang = LANG.search(_unvaried(str(PurePosixPath(f["name"]).with_suffix(""))))
    return (f".{lang.group(1).lower()}" if lang else "") + _ext(f)


# --- names ----------------------------------------------------------------------------------------

def clean(text: str) -> str:
    """A title fit for a file name: no slashes or colons, tidy spaces."""
    text = html.unescape(text).replace("/", "-").replace(":", " -")
    return re.sub(r"\s+", " ", text).strip(" .-_") or "Untitled"


def film_title(item) -> str:
    """The title for Movies/: without uploaders' tags or a repeated year, the article in front."""
    title = TITLE_TAGS.sub("", item.title)
    if item.year:
        title = re.sub(rf"\s*[(\[]{item.year}[)\]]\s*$", "", title)
    m = re.match(r"^(.*?)(\s*\(([^)]*)\))?$", title.strip())
    main, alt = m.group(1), m.group(3)
    main = ARTICLE.sub(r"\2 \1", main.strip())
    if alt:
        main += f" ({ARTICLE.sub(r'\2 \1', alt.strip())})"
    return clean(main)


def series_name(item) -> str:
    return clean(SERIES_NOISE.sub(" ", item.title))


def episode(name: str, series: str = "") -> tuple[int, int, str] | None:
    """(season, episode, title) from a file name, or None."""
    stem = _unvaried(PurePosixPath(name).stem)
    m = EPISODE.search(stem) or CROSS.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2)), _title(stem[m.end():], series)
    m = NUMBERED.search(stem)
    if m:
        return 1, int(m.group(1)), ""
    return None


def _title(rest: str, series: str) -> str:
    paren = re.search(r"\(([^)]+)\)", rest)
    text = paren.group(1) if paren else rest
    text = text.replace("_", " ").strip(" .-")
    if series and text.lower().startswith(series.lower()):
        text = text[len(series):].strip(" .-")
    if " " not in text:
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)  # EntertheLoneRanger -> Enterthe Lone Ranger
    return clean(text) if text else ""


# --- Jellyfin/Kodi .nfo -----------------------------------------------------------------------------

def plot(item) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", item.field("description"))
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def runtime(f: dict | None) -> int | None:
    """Minutes, from archive.org's `length` (seconds, or h:mm:ss)."""
    value = str((f or {}).get("length") or "")
    try:
        if ":" in value:
            seconds = 0.0
            for part in value.split(":"):
                seconds = seconds * 60 + float(part)
        else:
            seconds = float(value)
    except ValueError:
        return None
    return round(seconds / 60) or None


def _nfo(root: str, fields: list[tuple[str, str | None]], item) -> str:
    el = ET.Element(root)
    for tag, value in fields:
        if value:
            ET.SubElement(el, tag).text = value
    uid = ET.SubElement(el, "uniqueid", type="archive.org", default="true")
    uid.text = item.identifier
    ET.indent(el)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(el, encoding="unicode") + "\n"


def movie_nfo(item, video: dict | None) -> str:
    minutes = runtime(video)
    return _nfo("movie", [("title", clean(item.title)), ("year", item.year), ("plot", plot(item)),
                          ("director", item.field("director")), ("studio", item.field("creator")),
                          ("runtime", str(minutes) if minutes else None),
                          ("source", f"https://archive.org/details/{item.identifier}")], item)


def tvshow_nfo(item, series: str) -> str:
    return _nfo("tvshow", [("title", series), ("year", item.year), ("plot", plot(item)),
                           ("studio", item.field("creator")),
                           ("source", f"https://archive.org/details/{item.identifier}")], item)
