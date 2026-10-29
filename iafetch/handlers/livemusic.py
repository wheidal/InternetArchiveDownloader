"""Live music: concert tapes from archive.org's etree collections, any band but the Grateful Dead (§6.3).

The Dead's shows go to deadfetch and deadtag; this is the general version, built on iafetch's own copy of
their etree tools (iafetch/livemusic). Unlike most Dead shows, other bands' items usually have every track's
title in archive.org's file list ("One Reason >"), and the band, date, venue, city, source and lineage as
fields; the info .txt's setlist is the fallback, parsed and matched to the files as deadtag does.

    Live Music/{band}/{year}/{date} {venue}, {city} [{source}]/{date} d1t01 {title}.flac
                                                                 the info .txt, checksum files, pictures

Takes 16-bit FLAC (24-bit when that's all, or when [livemusic] prefer = "24bit"), else Shorten or WAV made into
FLAC losslessly, else MP3; never archive.org's MP3 and Ogg copies or the torrents. When filed, each FLAC is
checked against the taper's checksums (.md5 of the files, .ffp/.st5 of the audio), and (with mutagen) tagged: artist, album "{date} {venue},
{city}", title (segue marks kept), disc and track, date, the source and lineage as a comment.
"""

import re
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .. import tags
from ..download import safe_path
from ..livemusic import audio as audio_mod
from ..livemusic.infotxt import parse_info_text
from ..livemusic.match import build_audio_files, match_titles
from ..livemusic.names import date_from_name, decode_text, find_date, natural_key, parse_track_label
from ..select import TILE, Profile, Selection
from .base import Handler, Plan, clean

FAMILIES = (  # (format names, made into FLAC when filed)
    (("flac",), False),
    (("24bit flac",), False),
    (("shorten",), True),
    (("wave", "aiff"), True),
    (("vbr mp3", "mp3", "320kbps mp3", "256kbps mp3", "192kbps mp3", "128kbps mp3"), False),
)
SOURCES = (("SBD", r"\bsbd\b|soundboard|\bboard\b|\bpre-?fm\b"), ("MTX", r"matrix|\bmtx\b"), ("FM", r"\bfm\b|broadcast"),
           ("AUD", r"\baud\b|audience|\bfob\b|\bdfc\b|schoeps|neumann|akg|dpa|\bmk4|\bmics?\b"))


class LiveMusic(Handler):
    name = "livemusic"

    def kind(self, item) -> str:
        return "livemusic"

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=True)

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        settings = (config.settings.get("livemusic") if config else None) or {}
        families = list(FAMILIES)
        if settings.get("prefer") == "24bit":
            families[0], families[1] = families[1], families[0]
        audio = []
        for formats, _ in families:
            audio = [f for f in selection.files if f.get("format", "").lower() in formats]
            if audio:
                break
        extras = [f for f in selection.files if _is_info(f) or audio_mod.kind_of(f["name"]) or _is_image(f)]
        return replace(selection, files=sorted(audio, key=lambda f: natural_key(f["name"])) + extras)

    def plan(self, item, staged, selection, config) -> Plan:
        band = clean(item.field("creator")) or "Unknown Artist"
        date = show_date(item)
        album = album_name(item, date)
        folder = config.folder("livemusic") / _short(band) / (date[:4] if date else "Undated")
        source = source_type(item)
        name = _short(f"{album} [{source}]" if source else album)
        dest = config.inside(folder / name)
        if dest.exists():  # another tape of the same show
            dest = config.inside(folder / f"{name} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        audio = [f for f in selection.files if not (_is_info(f) or audio_mod.kind_of(f["name"]) or _is_image(f))]
        tracks, problems = track_titles(item, audio, Path(staged), band)
        for f, (disc, number, title) in zip(audio, tracks):
            rel = f"{date + ' ' if date else ''}d{disc}t{number:02d} {_short(_file_title(title))}{_ext(f)}"
            plan.moves.append([f["name"], rel])
            plan.tags[rel] = {"title": title, "discnumber": str(disc), "tracknumber": str(number)}
        for f in selection.files:
            if _is_info(f) or audio_mod.kind_of(f["name"]) or _is_image(f):
                plan.moves.append([f["name"], PurePosixPath(f["name"]).name])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}"])
        plan.review += problems
        if not audio:
            plan.review.append("no audio to take (try --as generic)")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        dest = Path(file_plan(plan, item, selection, config, self.name))
        finish(plan, item, selection, dest)
        return dest


# --- reading the show ---------------------------------------------------------------------------------------

def show_date(item) -> str:
    """The show's date: the date field ("2006-10-21", "2006-10-21T00:00:00Z"), else the identifier's (tlg2006-10-21)."""
    m = re.search(r"\b((?:19|20)\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", item.field("date"))
    if m:
        return m.group(0)
    found = date_from_name(item.identifier) or (find_date(item.field("date")) or [None])[0]
    return found.isoformat() if found else ""


def album_name(item, date: str) -> str:
    """ "2006-10-21 The Vic Theatre, Chicago, IL" """
    place = ", ".join(clean(re.sub(r",(?=\S)", ", ", item.field(k))) for k in ("venue", "coverage") if item.field(k).strip())
    return clean(" ".join(x for x in (date, place) if x)) if (date or place) else clean(item.title)


def source_type(item) -> str:
    """SBD, MTX, FM or AUD, from the identifier and the source field (soundboard first: "Mix of AUD and SBD" is
    a matrix, but a board feed beats a mic list in naming)."""
    text = f"{item.identifier} {item.field('source')}".lower()
    found = [kind for kind, pattern in SOURCES if re.search(pattern, text)]
    if "SBD" in found and "AUD" in found:
        return "MTX"
    return found[0] if found else ""


def track_titles(item, audio: list[dict], staged: Path, band: str) -> tuple[list[tuple[int, int, str]], list[str]]:
    """(disc, track, title) for each audio file, and why it needs review. Titles from archive.org's file list
    when every track has one; else the info .txt's setlist, matched to the files."""
    labels = [parse_track_label(PurePosixPath(f["name"]).stem) for f in audio]
    numbered = _numbering(labels)
    titles = [str(f.get("title") or "").strip() for f in audio]
    if audio and all(titles):
        return [(d, n, clean_title(t)) for (d, n), t in zip(numbered, titles)], []
    stems = [(d, n, PurePosixPath(f["name"]).stem) for (d, n), f in zip(numbered, audio)]
    if not staged.is_dir():  # not downloaded yet (iafetch show): the info .txt is read when it is
        return stems, []
    info = _info_text(staged)
    if info is None:
        return stems, ["no track titles in archive.org's listing and no info .txt to read them from"]
    parsed = parse_info_text(info, band)
    result = match_titles(build_audio_files([(f["name"], int(f.get("size") or 0)) for f in audio]), parsed.titles)
    if not result.clean:
        return [(d, n, t or PurePosixPath(f["name"]).stem) for (d, n), t, f in zip(numbered, titles, audio)], \
            [f"the info .txt's setlist doesn't match the files ({result.status}): name the tracks in the plan"]
    by_name = {t.file.name: t for t in result.tracks}
    out = []
    for (d, n), f in zip(numbered, audio):
        t = by_name.get(f["name"])
        title = t.title.title + (" >" if t.title.segue else "") if t else PurePosixPath(f["name"]).stem
        out.append((t.disc if t else d, t.disc_track if t else n, title))
    return out, []


def _numbering(labels) -> list[tuple[int, int]]:
    """Disc and track for each file: from its label (d1t01) when every file has one, else 1, 2, 3… on disc 1."""
    if labels and all(lab and lab.kind == "d" for lab in labels):
        return [(lab.group, lab.track) for lab in labels]
    if labels and all(lab for lab in labels):  # set labels (s2t04) or bare t01: number within each set
        return [(lab.group or 1, lab.track) for lab in labels]
    return [(1, n) for n in range(1, len(labels) + 1)]


def _info_text(staged: Path) -> str | None:
    """The show's info .txt, from the downloaded folder: the largest .txt that isn't a checksum list."""
    if not staged.is_dir():
        return None
    texts = [p for p in staged.rglob("*.txt") if not audio_mod.kind_of(p.name) and "ffp" not in p.name.lower()]
    if not texts:
        return None
    return decode_text(max(texts, key=lambda p: p.stat().st_size).read_bytes())


def clean_title(title: str) -> str:
    """A title as the taper wrote it, tidied: "Bogo>Up Here" -> "Bogo > Up Here"; "UFO's Exactly>" keeps its segue."""
    title = re.sub(r"\s*(-?>)\s*", r" \1 ", title).strip()
    return re.sub(r"\s+", " ", title)


def _file_title(title: str) -> str:
    """A title fit for a file name: segue marks and footnote marks dropped."""
    title = re.sub(r"\s*-?>\s*$", "", title.strip())  # the segue mark first, then any footnote marks before it
    title = re.sub(r"\s*[*%#@^+~]+$", "", title)
    return clean(title.replace("->", "-").replace(">", "-")) or "Untitled"


def _is_info(f: dict) -> bool:
    name = f["name"].lower()
    return name.endswith(".txt") and not audio_mod.kind_of(name) and "ffp" not in name


def _is_image(f: dict) -> bool:
    return f.get("format", "").lower() in ("jpeg", "png", "gif") and f.get("source") == "original"


def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _short(name: str, limit: int = 150) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]


# --- after filing: FLAC, checksums, tags ---------------------------------------------------------------------

def finish(plan: Plan, item, selection: Selection, dest: Path) -> dict:
    """Make Shorten and WAV into FLAC, check every FLAC against the taper's .ffp/.st5, and tag the tracks.
    Records what happened (problems, and the tagged files' MD5s) in the item's record."""
    by_name = {f["name"]: f for f in selection.files}
    expected = audio_mod.load_expected([dest / rel for _, rel in plan.moves if audio_mod.kind_of(rel)])
    band, date = clean(item.field("creator")) or "Unknown Artist", show_date(item)
    comment = "\n".join(x for x in (item.field("source") and f"Source: {item.field('source')}",
                                     item.field("lineage") and f"Lineage: {item.field('lineage')}",
                                     f"https://archive.org/details/{item.identifier}") if x)
    problems, processed, checked = [], {}, 0
    audio_moves = [(src, rel) for src, rel in plan.moves
                   if src in by_name and not (_is_info(by_name[src]) or audio_mod.kind_of(rel) or _is_image(by_name[src]))]
    for src, rel in audio_moves:
        path = safe_path(dest, rel)  # the name it was filed under (NAS-safe: no quotes or question marks)
        if not path.is_file():
            continue
        want = audio_mod.find_expected(expected, PurePosixPath(src).name)
        if want and want.md5 and path.suffix.lower() in (".shn", ".wav", ".aiff", ".flac"):  # the taper's .md5
            if tags.md5(path) not in want.md5:
                problems.append(f"{path.name}: it doesn't match the taper's .md5")
            else:
                checked += 1
        if path.suffix.lower() in (".shn", ".wav", ".aiff"):
            try:
                audio_mod.to_flac(path, path.with_suffix(".flac"), set(want.st5) if want and want.st5 else None)
                path = path.with_suffix(".flac")
            except audio_mod.AudioError as exc:
                problems.append(str(exc))
                continue
        if path.suffix.lower() == ".flac" and want and want.ffp and tags.available():
            if audio_mod.flac_audio_md5(path) not in want.ffp:
                problems.append(f"{path.name}: its audio doesn't match the taper's .ffp")
            else:
                checked += 1
        track = plan.tags.get(rel, {})
        fields = {"artist": band, "albumartist": band, "album": album_name(item, date), "date": date,
                  "comment": comment, "genre": "Live", "title": path.stem, **track}
        if tags.write(path, fields):
            processed[path.relative_to(dest).as_posix()] = tags.md5(path)
    tags.record_processed(dest / plan.sidecar, processed)
    if problems or checked:
        import json
        side = dest / plan.sidecar
        if side.is_file():
            record = json.loads(side.read_text(encoding="utf-8"))
            record["problems"], record["checksums_checked"] = problems, checked
            side.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"problems": problems, "checked": checked, "tagged": len(processed)}
