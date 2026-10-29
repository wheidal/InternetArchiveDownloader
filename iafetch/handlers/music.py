"""Studio music (§6.4): netlabel releases, uploaded albums and compilations, and 78 rpm sides.

An album item (a netlabel release, an upload, "Collected Works of Caruso") is filed as one album:

    Music/{artist}/{year} - {album}/01 - {title}.flac      (multi-disc: 1-01 - …), cover.jpg

A Great 78 Project item is one side of one record (a restored FLAC, a TIFF label scan, a zip of raw transfers):

    Music/78rpm/{artist}/{title} ({label} {catalog}).flac  with the label as its cover

Lossless when it covers the whole album (FLAC, including archive.org's FLAC made from an uploaded WAV), else the
uploaded MP3s; never both, never the album zip or the raw transfers. Track titles come from the tags (else the
file names) and lose track and disc codes ("tcp d1 1 the swedish rhapsody irdial"), the artist's and the label's
names, and "(restored)"; all-lowercase ones get capitals. With mutagen (the `audio` extra) every track is tagged (album,
artists, title, track, disc, year, genre, label, catalog number, license, the archive.org link) and the cover
embedded; Pillow turns a TIFF label into JPEG. The tagged files' MD5s go in the item's record.
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .. import tags
from ..download import safe_path
from ..select import TILE, Profile, Selection, size_of
from .base import Handler, Plan, clean, collection_title

LOSSLESS = ("flac", "24bit flac", "wave", "aiff")
LOSSY = ("vbr mp3", "mp3", "320kbps mp3", "256kbps mp3", "192kbps mp3", "128kbps mp3", "64kbps mp3", "ogg vorbis")
IMAGES = ("jpeg", "png", "tiff", "jpg")
UMBRELLAS = {"netlabels", "audio_music", "78rpm", "georgeblood", "audio", "opensource_audio", "archiveofcontemporarymusic"}
LABEL_KEY = "_iafetch_label"  # where prepare() keeps the netlabel's name on the item
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "vs"}


@dataclass
class Track:
    file: dict
    disc: int | None
    number: int | None  # from the tags, when they number the tracks
    title: str


class Music(Handler):
    name = "music"

    def kind(self, item) -> str:
        return "music"

    def prepare(self, item, archive) -> None:
        if LABEL_KEY not in item.raw and not is_78(item):
            item.raw[LABEL_KEY] = collection_title(item, archive, UMBRELLAS)

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)  # archive.org's FLAC of an uploaded WAV is a derivative

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        settings = (config.settings.get("music") if config else None) or {}
        lossless_first = settings.get("quality", "lossless") != "mp3"
        audio = best_audio(selection.files, lossless_first)
        art = cover_file(item, selection.files)
        return replace(selection, files=audio + ([art] if art else []))

    def plan(self, item, staged, selection, config) -> Plan:
        audio = [f for f in selection.files if not _is_image(f)]
        images = [f for f in selection.files if _is_image(f)]
        artist = album_artist(item, audio)
        if is_78(item):
            return self._side(item, staged, selection, config, artist, audio, images)
        album = album_title(item, artist)
        year = item.year
        folder = config.folder("music") / _short(artist)
        name = _short(f"{year} - {album}" if year else album)
        dest = config.inside(folder / name)
        if dest.exists():  # another release or upload of the same album: keep both
            dest = config.inside(folder / f"{name} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        tracks = numbered(read_tracks(audio, artist, label_words(item)))
        discs = len({t.disc for t in tracks}) > 1
        width = max(2, len(str(max((t.number for t in tracks), default=1))))  # 001…100 sort as text too
        for t in tracks:
            prefix = f"{t.disc or 1}-{t.number:0{width}d}" if discs else f"{t.number:0{width}d}"
            plan.moves.append([t.file["name"], f"{prefix} - {_short(t.title)}{_ext(t.file)}"])
        for f in images:
            plan.moves.append([f["name"], "cover" + _ext(f)])  # a TIFF becomes cover.jpg when filed (finish)
        self._records(plan, item, selection, cover=bool(images))
        if not audio:
            plan.review.append("no audio to take (try --as generic)")
        plan.approved = not plan.review
        return plan

    def _side(self, item, staged, selection, config, artist, audio, images) -> Plan:
        """A 78 rpm side: one track, filed with the others by the same artist."""
        dest = config.inside(config.folder("music") / "78rpm" / _short(artist))
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        title = side_title(item)
        label = " ".join(x for x in (item.field("publisher"), item.field("publisher-catalog-number")) if x)
        name = _short(f"{title} ({label})" if label else title)
        if audio and (dest / (name + _ext(audio[0]))).exists():
            name = f"{name} [{item.identifier}]"
        for f in audio[:1]:
            plan.moves.append([f["name"], name + _ext(f)])
        for f in images:
            plan.moves.append([f["name"], name + _ext(f)])  # the TIFF label becomes a JPEG when filed (finish)
        self._records(plan, item, selection, cover=True)
        if not audio:
            plan.review.append("no audio to take (try --as generic)")
        plan.approved = not plan.review
        return plan

    def _records(self, plan: Plan, item, selection, cover: bool) -> None:
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}" if cover else "cover.jpg"])

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        dest = Path(file_plan(plan, item, selection, config, self.name))
        finish(plan, item, selection, dest)
        return dest


# --- choosing files --------------------------------------------------------------------------------------

def is_78(item) -> bool:
    """A Great 78 Project side: one side of one record."""
    return "georgeblood" in item.collections and item.identifier.startswith("78_")


def best_audio(files: list[dict], lossless_first: bool = True) -> list[dict]:
    """The tracks in one format: lossless if there is any (unless asked for MP3), else the uploaded MP3s,
    else archive.org's lossy copies. Never two formats of the same track."""
    def pick(formats, originals_only):
        return [f for f in files if f.get("format", "").lower() in formats
                and (f.get("source") == "original" or not originals_only)]
    order = ((LOSSLESS, False), (LOSSY, True), (LOSSY, False))
    if not lossless_first:
        order = ((LOSSY, True), (LOSSY, False), (LOSSLESS, False))
    tracks = max(len(pick(LOSSY, True)), len({_stem(f) for f in pick(LOSSY, False)}))
    for formats, originals_only in order:
        found = pick(formats, originals_only)
        if formats is LOSSLESS and len({_stem(f) for f in found}) < tracks:
            continue  # lossless for only some tracks (one WAV of a nine-track album): not the album
        if found:
            # FLAC made from a WAV duplicates it: keep the FLAC (it can be tagged)
            flac_stems = {_stem(f) for f in found if f.get("format", "").lower() in ("flac", "24bit flac")}
            found = [f for f in found if not (f.get("format", "").lower() in ("wave", "aiff") and _stem(f) in flac_stems)]
            # one format only: the most common among them (an MP3 album with one stray Ogg stays MP3)
            by_format: dict[str, list[dict]] = {}
            for f in found:
                by_format.setdefault(f.get("format", "").lower(), []).append(f)
            return max(by_format.values(), key=len) if len(by_format) > 1 and formats is LOSSY else found
    return []


def cover_file(item, files: list[dict]) -> dict | None:
    """The cover: the largest uploaded picture (a 78's TIFF label scan, an album's JPEG), not a thumbnail."""
    images = [f for f in files if _is_image(f)]
    return max(images, key=size_of) if images else None


def _is_image(f: dict) -> bool:
    return (f.get("format", "").lower() in IMAGES and f.get("source") == "original"
            and not re.search(r"(?i)_thumb|spectrogram", f["name"]))


# --- names -------------------------------------------------------------------------------------------------

def album_artist(item, audio: list[dict]) -> str:
    creators = [c for c in item.values("creator") if c.strip()]
    if creators:
        return clean(creators[0])
    artists = {str(f.get("artist") or "").strip() for f in audio} - {""}
    return clean(artists.pop()) if len(artists) == 1 else "Various Artists" if artists else "Unknown Artist"


def album_title(item, artist: str) -> str:
    """The item's title without its catalog number ("[BadPanda018]") or the artist in front."""
    title = re.sub(r"\s*\[[^\]]*\]\s*$", "", item.title).strip()
    m = re.match(r"^(.+?)\s+[-–—:]\s+(.+)$", title)
    if m and _letters(m.group(1)) == _letters(artist):
        title = m.group(2)
    return clean(title)


def side_title(item) -> str:
    return clean(re.sub(r"(?i)\s*\((restored|remastered)\)\s*$", "", item.title))


def label_words(item) -> list[str]:
    """Words a netlabel puts at the end of its file names ("…_irdial.mp3"): its collection names."""
    return [c for c in item.collections if c not in UMBRELLAS and not c.startswith("fav-")]


def read_tracks(audio: list[dict], artist: str, labels: list[str]) -> list[Track]:
    out = []
    for f in audio:
        stem = PurePosixPath(f["name"]).stem
        tag = str(f.get("title") or "").strip()
        raw = tag or stem  # the tag keeps punctuation names lose; track_title cleans either
        disc = _disc(stem) or _disc(tag)
        names = [n for n in (str(f.get("artist") or "").strip(), artist) if n]  # the track's own artist tag first
        out.append(Track(f, disc, _tag_number(f), track_title(raw, names, labels)))
    return out


def track_title(raw: str, artists: list[str] | str, labels: list[str]) -> str:
    """ "01 Riding Alone For Thousands Of Miles - lullaby" -> "Lullaby";
    "tcp_d1_01_the_swedish_rhapsody_irdial" -> "The Swedish Rhapsody"."""
    text = raw.replace("_", " ").strip()
    if " " not in text:  # run together: "01RidingAloneForThousandsOfMiles-Lullaby"
        text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?i)\s*[-(]\s*(restored|remastered)\)?\s*$", "", text)
    text = re.sub(r"(?i)^[a-z]{1,5}\s+(?:d|cd|disc)\s*\d{1,2}\s+\d{1,3}\s+", "", text)  # "tcp d1 1 …"
    text = re.sub(r"(?i)^(?:(?:d|cd|disc)\s*\d{1,2}\s*[-. ]\s*)?\d{1,3}(?:\s*[-.)]\s*|\s+)", "", text)  # "01 - ", "1-02 "
    for artist in ([artists] if isinstance(artists, str) else artists):
        if artist and _letters(text).startswith(_letters(artist)) and _letters(text) != _letters(artist):
            n, want = 0, len(_letters(artist))
            for i, ch in enumerate(text):
                n += ch.isalnum()
                if n == want:
                    text = text[i + 1:]
                    break
            break
    text = text.strip(" -–_.")
    for label in labels:
        text = re.sub(rf"(?i)\s+{re.escape(label)}$", "", text)
    if text.islower():
        text = title_case(text)
    return clean(text) if text else clean(raw)


def title_case(text: str) -> str:
    words = text.split()
    return " ".join(w if (i and w in SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def numbered(tracks: list[Track]) -> list[Track]:
    """In order and numbered, disc by disc: by the tags' numbers when they number each track once, else by name,
    1, 2, 3… (Caruso's are mostly 00 with a stray 01, Conet's all 1)."""
    by_disc: dict[int, list[Track]] = {}
    for t in tracks:
        by_disc.setdefault(t.disc or 1, []).append(t)
    out = []
    for disc in sorted(by_disc):
        disc_tracks = by_disc[disc]
        numbers = [t.number for t in disc_tracks]
        if None in numbers or 0 in numbers or len(set(numbers)) != len(numbers):
            disc_tracks.sort(key=lambda t: _natural(t.file["name"]))
            for n, t in enumerate(disc_tracks, 1):
                t.number = n
        else:
            disc_tracks.sort(key=lambda t: t.number)
        out += disc_tracks
    return out


def _disc(text: str) -> int | None:
    m = re.search(r"(?i)(?:^|[\s_-])(?:d|cd|disc|disk)\s*(\d{1,2})(?=[\s_-]|$)", text)
    return int(m.group(1)) if m else None


def _tag_number(f: dict) -> int | None:
    m = re.match(r"\s*(\d{1,3})", str(f.get("track") or ""))
    return int(m.group(1)) if m else None


# --- tagging -------------------------------------------------------------------------------------------------

def finish(plan: Plan, item, selection: Selection, dest: Path) -> int:
    """Tag the filed tracks and embed the cover (if mutagen is installed); a TIFF cover becomes JPEG (if Pillow
    is). Records the tagged files' MD5s. How many were tagged."""
    cover = None
    for _, rel in plan.moves:
        path = safe_path(dest, rel)
        if rel.startswith(".iafetch") or path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff") \
                or not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix.lower() in (".tif", ".tiff"):  # players don't show TIFF: a JPEG beside it, if Pillow can
            jpeg = tags.as_jpeg(data)
            if jpeg:
                path.with_suffix(".jpg").write_bytes(jpeg)
                path.unlink()
            cover = jpeg
        else:
            cover = tags.as_jpeg(data) or data
    if not tags.available():
        return 0
    artist = album_artist(item, [f for f in selection.files if not _is_image(f)])
    by_name = {f["name"]: f for f in selection.files}
    audio = [f for f in selection.files if not _is_image(f)]
    tracks = {t.file["name"]: t for t in numbered(read_tracks(audio, artist, label_words(item)))}
    common = {
        "albumartist": artist, "date": item.year, "copyright": item.field("licenseurl"),
        "comment": f"https://archive.org/details/{item.identifier}",
        "organization": item.field("publisher") or item.raw.get(LABEL_KEY, ""),
        "catalognumber": item.field("publisher-catalog-number") or _catalog(item.title),
    }
    processed = {}
    for src, rel in plan.moves:
        f, path = by_name.get(src), safe_path(dest, rel)  # the name it was filed under
        if f is None or _is_image(f) or not path.is_file():
            continue
        t = tracks[src]
        if is_78(item):
            fields = {**common, "album": side_title(item), "artist": artist, "title": side_title(item),
                      "tracknumber": "1", "genre": "; ".join(genres(item))}
        else:
            fields = {**common, "album": album_title(item, artist), "artist": str(f.get("artist") or "").strip() or artist,
                      "title": t.title, "tracknumber": str(t.number), "discnumber": str(t.disc) if t.disc else "",
                      "genre": str(f.get("genre") or "").strip() if str(f.get("genre") or "").lower() not in ("", "other") else ""}
        if tags.write(path, fields, cover):
            processed[rel] = tags.md5(path)
    tags.record_processed(dest / plan.sidecar, processed)
    return len(processed)


def genres(item) -> list[str]:
    """A 78's subjects as genres, without the format ("78rpm") archive.org lists among them."""
    return [g for g in item.values("subject") if not re.fullmatch(r"(?i)78\s*(rpm)?|shellac|audio", g.strip())][:3]


def _catalog(title: str) -> str:
    m = re.search(r"\[([^\]]+)\]\s*$", title)
    return m.group(1) if m else ""


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _stem(f: dict) -> str:
    return PurePosixPath(f["name"]).stem.lower()


def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _short(name: str, limit: int = 150) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]
