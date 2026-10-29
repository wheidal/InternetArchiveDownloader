"""Ranking a band's tapes of each show, and the shows themselves (`iafetch shows`).

Adapted from deadfetch (DeadShowTagger, the same author), which keeps its own copy for the Grateful Dead. A
recording's score is a weighted sum of parts, each from 0 to 1:

    rating      the average rating, pulled toward the band's mean when few people reviewed it
    popularity  downloads, against the most-downloaded recording of the same date
    source      soundboard and matrix above FM, above audience
    format      16-bit FLAC first (or 24-bit, by preference), then Shorten
plus a small bonus or penalty from notes in the identifier ("sbeok", "sbefail"), and a larger penalty when it
names only part of a show ("partial", "set2"). (deadfetch also compares running times; that needs a request
per tape, so it isn't done here.)
"""

import math
import re
from dataclasses import dataclass, field

FIELDS = ["identifier", "date", "venue", "coverage", "source", "format", "avg_rating", "num_reviews", "downloads",
          "item_size", "creator"]


@dataclass
class Recording:
    identifier: str
    date: str  # YYYY-MM-DD
    venue: str = ""
    city: str = ""
    source: str = ""  # archive.org's source description
    formats: list[str] = field(default_factory=list)  # "Shorten", "Flac", "24bit Flac"
    rating: float | None = None
    reviews: int = 0
    downloads: int = 0
    size: int = 0  # bytes, the whole item
    downloadable: bool = True

    @classmethod
    def from_doc(cls, d: dict) -> "Recording":
        def one(v):
            return "; ".join(map(str, v)) if isinstance(v, list) else str(v or "")
        m = re.search(r"(?:19|20)\d\d-\d\d-\d\d", one(d.get("date")))
        formats = d.get("format") or []
        return cls(d["identifier"], m.group(0) if m else "", one(d.get("venue")), one(d.get("coverage")),
                   one(d.get("source")), formats if isinstance(formats, list) else [formats],
                   float(d["avg_rating"]) if d.get("avg_rating") else None, int(d.get("num_reviews") or 0),
                   int(d.get("downloads") or 0), int(d.get("item_size") or 0))


WEIGHTS = {"rating": 0.45, "popularity": 0.20, "source": 0.20, "format": 0.15}
SOURCE_SCORE = {"sbd": 1.0, "matrix": 0.95, "fm": 0.85, "aud": 0.7, "unknown": 0.6, "surround": 0.3}
PRIOR_REVIEWS = 5  # a rating needs about this many reviews before it counts at full strength
PARTIAL_PENALTY = 0.15  # enough that a whole audience tape beats part of a soundboard
_SOURCE_TAGS = [("matrix", {"mtx", "matrix"}), ("sbd", {"sbd"}), ("fm", {"fm", "prefm", "pre-fm"}),
                ("aud", {"aud", "fob", "ots"})]
# audience tapes are often named by their microphones: senn421, nak700, akgc422, beyerm88, neumannkmf4,
# at835, mk4, bk4011, sonyecm... (learned from the catalog's "unknown" tapes, October 2026)
_MIC_TOKEN = re.compile(r"^(?:senn|sennheiser|akg|beyer|neuman|nak|schoeps|shure|sonyecm|ecm|mke|dpa|pzm|"
                        r"realistic|km\d|cm\d|ck\d|at\d|me\d|bk\d|mk4|u87|sm\d|m\d{2,3}$)")
# ...and their source text says where the microphones were
_AUD_TEXT = re.compile(r"\b(?:aud|audience|fob|ots|microphones?|mics?|shotguns?|xy|pzms?|row \d+|\d+ ?(?:feet|ft)\b)"
                       r"|sennheiser|beyerdynamic|\bbeyer\b|\bakg\b|schoeps|neuman|audio-technica|nakamichi cm|"
                       r"\bcm-?[137]00\b")
_PARTIAL = re.compile(r"(?i)(^|[.\-_])(partial|incomplete|fragment|set-?[123])([.\-_]|$)")


@dataclass
class Score:
    total: float
    parts: dict[str, float] = field(default_factory=dict)
    source: str = "unknown"
    format: str = "?"
    notes: list[str] = field(default_factory=list)


def source_kind(r: Recording) -> str:
    """sbd, matrix, fm, aud, surround (5.1) or unknown. The taper's tags in the identifier come first; archive.org's
    source text only decides when the identifier has none (it often names other gear: "near the sbd")."""
    tokens = set(re.split(r"[.\-_]", r.identifier.lower()))
    if "dts" in tokens or ".5-1." in r.identifier or "surround" in r.source.lower():
        return "surround"  # a 5.1 mix: DTS plays as noise on ordinary players, LPCM as six channels
    for kind, tags in _SOURCE_TAGS:
        if tokens & tags:
            return kind
    if any(_MIC_TOKEN.match(t) for t in tokens):
        return "aud"
    text = r.source.lower()
    if "matrix" in text:
        return "matrix"
    if "soundboard" in text or re.search(r"\bsbd\b", text):
        return "sbd"
    if re.search(r"\b(pre-?)?fm\b", text):
        return "fm"
    if _AUD_TEXT.search(text):
        return "aud"
    return "unknown"


def format_kind(r: Recording, prefer: str = "16bit") -> str:
    """flac16, flac24 or shn: the best of the item's formats, by the 16/24-bit preference."""
    formats = {f.lower() for f in r.formats}
    has16, has24 = "flac" in formats, "24bit flac" in formats
    if has16 and has24:
        return "flac24" if prefer == "24bit" else "flac16"
    if has24:
        return "flac24"
    if has16 or "flac" in r.identifier.lower():
        return "flac16"
    if "shorten" in formats or "shn" in r.identifier.lower():
        return "shn"
    return "?"


def collection_mean(recordings: list[Recording]) -> float:
    rated = [r.rating for r in recordings if r.rating]
    return sum(rated) / len(rated) if rated else 4.0


def score(r: Recording, siblings: list[Recording], mean: float, prefer: str = "16bit") -> Score:
    reviews = r.reviews if r.rating else 0
    rating = ((r.rating or 0) * reviews + mean * PRIOR_REVIEWS) / (reviews + PRIOR_REVIEWS)
    top = max((s.downloads for s in siblings), default=0)
    popularity = math.log10(r.downloads + 1) / math.log10(top + 1) if top else 0.0
    kind = source_kind(r)
    fmt = format_kind(r, prefer)
    preferred, other = ("flac24", "flac16") if prefer == "24bit" else ("flac16", "flac24")
    fmt_score = {preferred: 1.0, other: 0.8, "shn": 0.7, "?": 0.4}[fmt]
    parts = {"rating": rating / 5, "popularity": popularity, "source": SOURCE_SCORE[kind], "format": fmt_score}
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    notes = []
    name = r.identifier.lower()
    if "sbeok" in name:
        total += 0.02
        notes.append("sbeok")
    if "sbefail" in name:
        total -= 0.04
        notes.append("sbefail")
    if _PARTIAL.search(r.identifier):
        total -= PARTIAL_PENALTY
        notes.append("partial")
    return Score(round(total, 4), {k: round(v, 3) for k, v in parts.items()}, kind, fmt, notes)


@dataclass
class Ranked:
    recording: Recording
    score: Score


def rank_date(recordings: list[Recording], mean: float, prefer: str = "16bit") -> list[Ranked]:
    """A date's recordings, best first. Stream-only ones are ranked too (to show them), but last."""
    ranked = [Ranked(r, score(r, recordings, mean, prefer)) for r in recordings]
    ranked.sort(key=lambda x: (not x.recording.downloadable, -x.score.total, x.recording.identifier))
    return ranked


def picks(recordings: list[Recording], mean: float, n: int = 1, prefer: str = "16bit") -> list[Ranked]:
    return [x for x in rank_date(recordings, mean, prefer) if x.recording.downloadable][:n]


def landmark(recordings: list[Recording]) -> float:
    """How much people care about a show: downloads and reviews across all its recordings."""
    downloads = sum(r.downloads for r in recordings)
    reviews = sum(r.reviews for r in recordings)
    return round(math.log10(downloads + 1) + math.log10(reviews + 1), 3)
