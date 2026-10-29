"""Filtering search results on this computer, for the terminal UI's Choose screen.

A search fetches its results once, with every field worth filtering on (FIELDS: the scrape API returns them
all, a thousand items a request). Filtering and counting then happen here, instantly, with no more requests.

archive.org's fields are messy, so each item's values are tidied into facets first:
  language    codes and names merged ("eng", "English", "en" are one), and a region tag in the title ("(IT)",
              "(DE)") taken over the field, which often says English for a translated edition
  decade      from year, date or a year in the title
  format      what the item has, in plain words ("searchable PDF", "subtitles"), from its file formats
  collection  without people's favourites lists (fav-...), which are on most popular items
  license     public domain, Creative Commons, another license, or none declared
  kind        from the mediatype

A filter on a facet is three-state per value: include (+), exclude (-), or neither. An item passes a facet if it
has one of the included values (when any are included) and none of the excluded ones. A words filter matches
title and subject: +word or a bare word must appear, -word mustn't; "two words" is a phrase.
"""

import re
import shlex
from collections import Counter
from dataclasses import dataclass, field

FIELDS = ["identifier", "title", "mediatype", "language", "subject", "format", "year", "date", "collection",
          "licenseurl", "item_size", "downloads", "avg_rating", "num_reviews", "creator"]
FACETS = ("kind", "language", "decade", "format", "collection", "license")

LANGUAGES = {
    "English": ("eng", "en", "english", "en-us", "en-gb", "englisch"),
    "German": ("ger", "deu", "de", "german", "deutsch"),
    "Italian": ("ita", "it", "italian", "italiano"),
    "French": ("fre", "fra", "fr", "french", "français", "francais"),
    "Spanish": ("spa", "es", "spanish", "español", "espanol"),
    "Dutch": ("dut", "nld", "nl", "dutch"),
    "Swedish": ("swe", "sv", "swedish"), "Danish": ("dan", "da", "danish"), "Norwegian": ("nor", "no", "norwegian"),
    "Finnish": ("fin", "fi", "finnish"), "Hungarian": ("hun", "hu", "hungarian"), "Polish": ("pol", "pl", "polish"),
    "Portuguese": ("por", "pt", "portuguese"), "Russian": ("rus", "ru", "russian"), "Czech": ("cze", "ces", "cs", "czech"),
    "Japanese": ("jpn", "ja", "japanese"), "Chinese": ("chi", "zho", "zh", "chinese"), "Greek": ("gre", "ell", "greek"),
    "Latin": ("lat", "la", "latin"), "Hebrew": ("heb", "he", "hebrew"), "Arabic": ("ara", "ar", "arabic"),
    "Turkish": ("tur", "tr", "turkish"),
}
_LANGUAGE = {code: name for name, codes in LANGUAGES.items() for code in codes}
# A region tag in a title, as TOSEC names have them: "Manual (1984)(Commodore)(DE)"
REGION_TAGS = {"DE": "German", "IT": "Italian", "ES": "Spanish", "FR": "French", "NL": "Dutch", "SE": "Swedish",
               "DK": "Danish", "NO": "Norwegian", "FI": "Finnish", "HU": "Hungarian", "PL": "Polish",
               "PT": "Portuguese", "RU": "Russian", "JP": "Japanese", "CZ": "Czech"}
REGION = re.compile(r"\((%s)\)" % "|".join(REGION_TAGS), re.I)

FORMATS = {  # plain name -> archive.org format names that give it
    "searchable PDF": ("text pdf", "additional text pdf"),
    "PDF": ("text pdf", "additional text pdf", "image container pdf", "pdf"),
    "EPUB": ("epub",),
    "comic book": ("comic book zip", "comic book rar", "cbz", "cbr"),
    "subtitles": ("subrip", "web video text tracks", "substation alpha", "advanced substation alpha"),
    "MP4 video": ("h.264", "h.264 ia", "h.264 hd", "mpeg4", "512kb mpeg4", "hires mpeg4"),
    "MPEG2 video": ("mpeg2",),
    "FLAC": ("flac", "24bit flac"),
    "MP3": ("vbr mp3", "128kbps mp3", "64kbps mp3", "mp3"),
    "Ogg audio": ("ogg vorbis",),
    "page scans": ("single page processed jp2 zip", "single page processed jp2 tar", "single page original jp2 tar"),
}
KINDS = {"movies": "video", "texts": "books", "software": "games", "audio": "audio", "etree": "audio",
         "image": "images", "data": "data", "collection": "collections"}
SORTS = {  # ties go to the more downloaded
    "downloads": lambda r: -(r.downloads or 0),
    "rating": lambda r: (-(r.rating or 0), -(r.reviews or 0), -(r.downloads or 0)),
    "year": lambda r: (r.year or 9999, -(r.downloads or 0)),
    "title": lambda r: r.title.lower(),
}


@dataclass
class Record:
    identifier: str
    title: str
    size: int
    downloads: int
    rating: float | None
    reviews: int
    year: int | None
    subjects: list[str]
    facets: dict[str, set[str]]
    language_guessed: bool = False  # taken from a region tag in the title, not the language field
    text: str = ""  # title and subjects, lower case, for the words filter


def _list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def languages(value, title: str = "") -> tuple[set[str], bool]:
    """The languages, by name, and whether they were guessed from a region tag in the title."""
    tag = REGION.search(title)
    if tag:
        return {REGION_TAGS[tag.group(1).upper()]}, True
    names = set()
    for v in _list(value):
        for part in re.split(r"[;,/]", v):
            part = part.strip().lower()
            if part:
                names.add(_LANGUAGE.get(part, part.title()))
    return names or {"(not given)"}, False


def year_of(doc: dict) -> int | None:
    for value in (_list(doc.get("year")) + _list(doc.get("date")) + [str(doc.get("title", ""))]):
        m = re.search(r"\b(1[89]\d\d|20\d\d)\b", value)
        if m:
            return int(m.group(1))
    return None


def license_class(url) -> str:
    u = " ".join(_list(url)).lower()
    if not u:
        return "no license declared"
    if "publicdomain" in u or "/mark/" in u or "/zero/" in u:
        return "public domain"
    if "creativecommons" in u:
        return "Creative Commons"
    return "another license"


def record(doc: dict) -> Record:
    """One search result, its facets worked out."""
    title = " ".join(_list(doc.get("title"))) or doc["identifier"]
    langs, guessed = languages(doc.get("language"), title)
    year = year_of(doc)
    have = {f.lower() for f in _list(doc.get("format"))}
    formats = {name for name, wanted in FORMATS.items() if have.intersection(wanted)}
    collections = {c for c in _list(doc.get("collection")) if not c.startswith("fav-")}
    subjects = [s.strip() for v in _list(doc.get("subject")) for s in v.split(";") if s.strip()]
    facets = {
        "kind": {KINDS.get(str(doc.get("mediatype", "")), "other")},
        "language": langs,
        "decade": {f"{year // 10 * 10}s"} if year else {"(no year)"},
        "format": formats or {"(none listed)"},
        "collection": collections or {"(none)"},
        "license": {license_class(doc.get("licenseurl"))},
    }
    return Record(doc["identifier"], title, _int(doc.get("item_size")), _int(doc.get("downloads")),
                  _float(doc.get("avg_rating")), _int(doc.get("num_reviews")), year, subjects, facets, guessed,
                  (title + " " + " ".join(subjects)).lower())


@dataclass
class Filters:
    """What's chosen: per facet, value -> +1 (include) or -1 (exclude); words; whether to hide what's had."""
    chosen: dict[str, dict[str, int]] = field(default_factory=dict)
    words: str = ""
    hide_had: bool = False

    def cycle(self, facet: str, value: str) -> int:
        """Neither -> include -> exclude -> neither. The new state."""
        states = self.chosen.setdefault(facet, {})
        new = {0: 1, 1: -1, -1: 0}[states.get(value, 0)]
        if new:
            states[value] = new
        else:
            states.pop(value, None)
        return new

    def clear(self) -> None:
        self.chosen.clear()
        self.words, self.hide_had = "", False

    def summary(self) -> str:
        """The filters in a line: "language +English · format -page scans · words: +basic"."""
        parts = []
        for facet in FACETS:
            states = self.chosen.get(facet) or {}
            if states:
                parts.append(f"{facet} " + " ".join(("+" if s > 0 else "-") + v for v, s in sorted(states.items())))
        if self.words.strip():
            parts.append(f"words: {self.words.strip()}")
        if self.hide_had:
            parts.append("hiding what you have or have queued")
        return " · ".join(parts) or "no filters"


def words_match(text: str, words: str) -> bool:
    try:
        terms = shlex.split(words)
    except ValueError:  # an unclosed quote: take the words as they are
        terms = words.split()
    for term in terms:
        if term.startswith("-") and len(term) > 1:
            if term[1:].lower() in text:
                return False
        elif term.lstrip("+").lower() not in text:
            return False
    return True


def passes(r: Record, f: Filters, had: set[str] = frozenset(), skip: str = "") -> bool:
    """Whether the record passes the filters (all but the facet `skip`, for counting that facet's values)."""
    for facet, states in f.chosen.items():
        if facet == skip or not states:
            continue
        values = r.facets.get(facet, set())
        if any(states.get(v) == -1 for v in values):
            return False
        wanted = {v for v, s in states.items() if s == 1}
        if wanted and not values & wanted:
            return False
    if f.hide_had and r.identifier in had:
        return False
    return words_match(r.text, f.words) if f.words.strip() else True


def apply(records: list[Record], f: Filters, had: set[str] = frozenset(), sort: str = "downloads") -> list[Record]:
    return sorted((r for r in records if passes(r, f, had)), key=SORTS.get(sort, SORTS["downloads"]))


def counts(records: list[Record], f: Filters, facet: str, had: set[str] = frozenset()) -> Counter:
    """How many items have each value of `facet`, among those passing every other filter: what ticking a
    value would leave (the usual way facet counts work)."""
    c: Counter = Counter()
    for r in records:
        if passes(r, f, had, skip=facet):
            c.update(r.facets.get(facet, ()))
    return c


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
