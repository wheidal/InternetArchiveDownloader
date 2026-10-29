"""Writing tags and covers into audio files, for the handlers that tag (radio, music). Needs mutagen (the
`audio` extra); converting a cover that isn't JPEG or PNG (the 78s' TIFF label scans) needs Pillow too. Without
them nothing is tagged, and files are filed as they came."""

import hashlib
import io
from pathlib import Path


def available() -> bool:
    try:
        import mutagen  # noqa: F401
    except ImportError:
        return False
    return True


def write(path: Path, fields: dict[str, str], cover: bytes | None = None) -> bool:
    """Set these tags (lower-case names: album, albumartist, artist, title, tracknumber, discnumber, date, genre,
    organization, catalognumber, copyright, comment…; ones a format can't hold are skipped) and embed the cover
    (JPEG or PNG bytes). False if the file can't be tagged."""
    try:
        from mutagen import File as AudioFile
    except ImportError:
        return False
    try:
        audio = AudioFile(path, easy=True)
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        for key, value in fields.items():
            if value in (None, ""):
                continue
            try:
                audio[key] = str(value)
            except (KeyError, ValueError):  # EasyID3 holds only the keys it knows
                pass
        audio.save()
        if cover:
            _embed(path, cover)
    except Exception:  # a file mutagen can't write: leave it as it came
        return False
    return True


def _embed(path: Path, cover: bytes) -> None:
    mime = "image/png" if cover[:4] == b"\x89PNG" else "image/jpeg"
    suffix = path.suffix.lower()
    if suffix == ".flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(path)
        picture = Picture()
        picture.type, picture.mime, picture.data = 3, mime, cover  # 3: front cover
        audio.clear_pictures()
        audio.add_picture(picture)
        audio.save()
    elif suffix == ".mp3":
        from mutagen.id3 import APIC, ID3
        tags = ID3(path)
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        tags.save(path)


def as_jpeg(data: bytes, max_side: int = 1200) -> bytes | None:
    """An image as JPEG bytes (a TIFF label scan, a huge PNG), at most max_side pixels; None without Pillow."""
    if data[:3] == b"\xff\xd8\xff" and len(data) < 2_000_000:
        return data
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return None


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def record_processed(sidecar: Path, processed: dict[str, str]) -> None:
    """Keep the tagged files' MD5s in the item's record (iafetch.json), for `verify`."""
    import json
    if not processed or not sidecar.is_file():
        return
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record["processed"] = processed
    sidecar.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
