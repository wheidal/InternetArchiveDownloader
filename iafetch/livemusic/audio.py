"""A show's audio: its checksum files (.md5, .ffp, .st5), and Shorten or WAV made into FLAC, losslessly.

Adapted from deadtag (DeadShowTagger, the same author). Converting needs ffmpeg (to decode) and flac (to
encode); the new FLAC's own audio MD5 must equal the decoded source's, else it's deleted and the source kept.
"""

import hashlib
import json
import re
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

COMPRESSION = 8
_PCM_CODECS = {"s16": "pcm_s16le", "s16p": "pcm_s16le", "s32": "pcm_s24le", "s32p": "pcm_s24le"}
_MD5_LINE = re.compile(r"^\\?([0-9a-fA-F]{32})\s+(?:\[shntool\]\s+)?\*?(.+?)\s*$")
_BSD_LINE = re.compile(r"^MD5\s*\((.+)\)\s*=\s*([0-9a-fA-F]{32})\s*$")
_FFP_LINE = re.compile(r"^(.+?)\s*:\s*([0-9a-fA-F]{32})\s*$")


class AudioError(Exception):
    pass


@dataclass
class Expected:
    """What a show's checksum files say about one audio file: hash -> the checksum files listing it."""
    md5: dict[str, list[str]] = field(default_factory=dict)
    ffp: dict[str, list[str]] = field(default_factory=dict)
    st5: dict[str, list[str]] = field(default_factory=dict)

    def add(self, kind: str, digest: str, source: str) -> None:
        getattr(self, kind).setdefault(digest.lower(), []).append(source)


def kind_of(name: str) -> str | None:
    """ "md5", "ffp" or "st5" for a checksum file's name (x.md5, x.ffp.txt, x-16bitffp.txt, x.md5.original), else None."""
    m = re.search(r"(?i)(md5|ffp|st5)(?:\.(?:new|original|old|txt))?$", name)  # also "-16bitffp.txt"
    return m.group(1).lower() if m else None


def parse_checksums(text: str, kind: str) -> list[tuple[str, str]]:
    """(file name, hash) pairs; the name reduced to its last path part."""
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if kind == "ffp":
            m = _FFP_LINE.match(line)
            if m:
                pairs.append((m.group(1), m.group(2)))
            continue
        m = _MD5_LINE.match(line)
        if m:
            pairs.append((m.group(2), m.group(1)))
        elif m := _BSD_LINE.match(line):
            pairs.append((m.group(1), m.group(2)))
    return [(re.split(r"[\\/]", name)[-1], digest.lower()) for name, digest in pairs]


def load_expected(paths: list[Path]) -> dict[str, Expected]:
    """The checksum files' contents: lowercased file name -> Expected."""
    from .names import decode_text
    expected: dict[str, Expected] = {}
    for path in paths:
        kind = kind_of(path.name)
        if not kind or not path.is_file():
            continue
        for name, digest in parse_checksums(decode_text(path.read_bytes()), kind):
            expected.setdefault(name.lower(), Expected()).add(kind, digest, path.name)
    return expected


def _loose(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"(?i)\.(?:flac|shn|wav)$", "", name).lower())


def find_expected(expected: dict[str, Expected], name: str) -> Expected | None:
    """The entry for a file, by name, or by the same name without case and punctuation (archive.org renames
    files on upload: "01 El Paso.flac" became "01ElPaso.flac") when only one listed name reads that way."""
    if name.lower() in expected:
        return expected[name.lower()]
    loose = [k for k in expected if _loose(k) == _loose(name)]
    return expected[loose[0]] if len(loose) == 1 else None


def flac_audio_md5(path: Path) -> str:
    """The MD5 of a FLAC's decoded audio, from its STREAMINFO (what an .ffp lists)."""
    from mutagen.flac import FLAC
    return f"{FLAC(path).info.md5_signature:032x}"


def to_flac(src: Path, out: Path, st5: set[str] | None = None, level: int = COMPRESSION) -> str:
    """Make a Shorten or WAV file into FLAC, losslessly; returns the audio MD5. The FLAC's own audio MD5 must
    equal the decoded source's (and one of the .st5's, if given). Raises AudioError, leaving `src` in place;
    on success `src` is deleted."""
    wav = src if src.suffix.lower() == ".wav" else src.with_suffix(".decoded.wav")
    try:
        if wav is not src:
            probe = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_fmt,bits_per_raw_sample", "-of", "json", str(src)])
            try:
                stream = json.loads(probe.stdout)["streams"][0]
            except (ValueError, KeyError, IndexError):
                raise AudioError(f"{src.name}: ffprobe can't read it: {probe.stderr.strip()}") from None
            codec = _PCM_CODECS.get(stream.get("sample_fmt"))
            if codec is None or (codec == "pcm_s24le" and str(stream.get("bits_per_raw_sample")) not in ("24", "0")):
                raise AudioError(f"{src.name}: unsupported sample format {stream.get('sample_fmt')}")
            decode = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:a:0", "-c:a", codec,
                           "-map_metadata", "-1", "-bitexact", "-f", "wav", "-y", str(wav)])
            if decode.returncode != 0:
                raise AudioError(f"{src.name}: ffmpeg can't decode it: {decode.stderr.strip()}")
        pcm_md5 = wav_md5(wav)
        if st5 and pcm_md5 not in st5:
            raise AudioError(f"{src.name}: its audio MD5 {pcm_md5} doesn't match the .st5")
        encode = _run(["flac", f"-{level}", "--verify", "--silent", "-f", "-o", str(out), str(wav)])
        if encode.returncode != 0:
            out.unlink(missing_ok=True)
            raise AudioError(f"{src.name}: flac failed: {encode.stderr.strip()}")
        audio_md5 = flac_audio_md5(out)
        if audio_md5 != pcm_md5:
            out.unlink()
            raise AudioError(f"{src.name}: the new FLAC's audio MD5 differs from the source's; the FLAC was deleted")
        src.unlink()
        return audio_md5
    finally:
        if wav is not src:
            wav.unlink(missing_ok=True)


def wav_md5(path: Path) -> str:
    """MD5 of a WAV's samples: the bytes FLAC's STREAMINFO MD5 covers. Chunks are read directly: ffmpeg writes a
    WAVE_FORMAT_EXTENSIBLE header the wave module refuses."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        if f.read(4) != b"RIFF" or f.read(8)[4:] != b"WAVE":
            raise AudioError(f"{path.name}: not a WAV file")
        bits = None
        while header := f.read(8):
            if len(header) < 8:
                break
            kind, size = header[:4], struct.unpack("<I", header[4:])[0]
            if kind == b"fmt ":
                bits = struct.unpack("<H", f.read(size)[14:16])[0]
                if bits == 8:
                    raise AudioError(f"{path.name}: 8-bit audio isn't supported")
            elif kind == b"data":
                if bits is None:
                    raise AudioError(f"{path.name}: its data comes before its format")
                left = size
                while left and (chunk := f.read(min(left, 1 << 20))):
                    h.update(chunk)
                    left -= len(chunk)
                return h.hexdigest()
            else:
                f.seek(size, 1)
            if size % 2:
                f.seek(1, 1)
    raise AudioError(f"{path.name}: the WAV has no audio data")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AudioError(f"{cmd[0]} isn't installed (it's needed to make FLAC of Shorten and WAV files)") from exc
