"""Parse a show's info .txt: header, source and lineage, setlist, notes and shntool table.

Info files are written by hand and vary a lot, so every rule here is a heuristic. The parser
keeps each track's original line so a person can check what it made of it.

Adapted from deadtag (DeadShowTagger, the same author), which keeps its own Grateful-Dead-specific copy;
this one is iafetch's, for every etree band.
"""

from __future__ import annotations  # `date: date | None` below would otherwise read its own field

import re
from dataclasses import dataclass, field
from datetime import date

from .names import TrackLabel, find_date, name_key

# --- headings -----------------------------------------------------------------------------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_NUM = r"(\d{1,2}|i{1,3}|iv|v|one|two|three|four|five)"

_DISC_HEADING = re.compile(r"(?i)^(?:disc|disk|cd)\s*#?\s*" + _NUM + r"\b\s*[:.)\-–/]*\s*(.*)$")
_SET_HEADING = re.compile(r"(?i)^(?:set|s)\s*" + _NUM + r"\b\s*[:.)\-–/]*\s*(.*)$")
_ENCORE_HEADING = re.compile(r"(?i)^(?:encores?|e)\s*[:.)\-–]*\s*(.*)$")
_NOTES_HEADING = re.compile(r"(?i)^(?:notes?|comments?|extra notes?)\s*[:\-–]*\s*(.*)$")
_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9 ./&()'=-]{0,40}?)\s*:\s*(.*)$")

# --- track lines --------------------------------------------------------------------------

_LABELED = re.compile(r"(?i)^(?:[a-z][a-z0-9.]{0,15}?\d{2,4}-\d{2}-\d{2}\.?)?([ds])(\d{1,2})t(\d{1,3})(?![0-9])\s*[-–.:)\]]*\s*(.*)$")
_NUMBERED = re.compile(r"(?i)^(?:t(?:rack)?\s*)?(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*[.):\]\-–]?\s+(\S.*)$")

_DURATION = r"\d{1,2}:\d{2}(?:[:.]\d{1,3})?"
_TRAILING = re.compile(
    r"\s*(->|-?>|//|[*%#@^+~;]+|\[" + _DURATION + r"\]|\(" + _DURATION + r"\)|" + _DURATION + r")\s*$"
)
_LEADING = re.compile(r"^\s*(//|>|[*%#@^+~]+|(?i:e\s*:|encore\s*[:-])|ENCORE\s)\s*")
_MISSING_LINE = re.compile(r"(?i)^(?:[<(\[]\s*(?:missing|not recorded|not taped)\s*[>)\]]|(?:missing|not recorded|not taped)\s*:)"
                           r"\s*(\S.*)$")  # "<missing> Big River", "missing: El Paso"
_MARKER_RUN = re.compile(r"([*%#@^+~])\1*")  # "**" is one marker, "*%" is two
_FOOTNOTE = re.compile(r"^([*%#@^+~]+)\s*(\S.*)$")

# --- shntool table ------------------------------------------------------------------------

# Old:  length  expanded size  cdr  WAVE  probs  filename
# New:  length  expanded size B  cdr  WAVE  problems  fmt  ratio  filename (may contain spaces)
_SHNTOOL_ROW = re.compile(
    r"^\s*(\d+:\d{2}(?:\.\d{2,3})?)\s+\d+(?:\s+B)?\s+\S+\s+\S+\s+\S+(?:\s+[a-z0-9]{2,5}\s+\d+\.\d+)?"
    r"\s+(.+?)\.(?:wav|shn|flac)\s*$", re.I
)

# --- source details -----------------------------------------------------------------------

_SOURCE_TYPES = [
    ("SBD", re.compile(r"(?i)\b(?:sbd|soundboard)\b")),
    ("AUD", re.compile(r"(?i)\b(?:aud|audience|fob|fotb|dfc|otm|ots|shure|schoeps|akg|sennheiser|neumann|beyerdynamic"
                       r"|nak(?:amichi)?\s*(?:cm-?)?(?:100|300|700)|cm-?300|cp-?4)\b")),
    ("Pre-FM", re.compile(r"(?i)\bpre-?fm\b")),
    ("FM", re.compile(r"(?i)(?<!pre-)(?<!pre)(?<![a-z])(?:fm|broadcast|aired)\b")),  # also "99.5fm"
    ("Matrix", re.compile(r"(?i)\b(?:matrix|mtx)\b")),
    ("DTS", re.compile(r"(?i)\bdts\b")),
]
_NEAR_BOARD = re.compile(r"(?i)(?:\b(?:near|by|behind|next to|in front of|beside|at|from)|@)\s*(?:the\s+)?(?:sbd|soundboard)\b")
# Capitalized words, optionally joined by "and"/"&": "Charlie Miller", "Dave and Jim Melton"
_PEOPLE = r"([A-Z][\w.'-]*(?:[ \t]+(?:(?:and|&)[ \t]+)?[A-Z][\w.'-]*){0,4})"
_TAPER = re.compile(r"(?i:\b(?:recorded|taped|recording)[ \t]+by)[ \t]+" + _PEOPLE)
# "Source: 2x Nak 300 CP4 > unknown mixer > Nak 550 by Barry Levitt"
_TAPER_AT_END = re.compile(r"(?i:\bby)[ \t]+" + _PEOPLE + r"[ \t]*$")
_SOURCE_KEY = re.compile(r"(?i)source|recording|taper|mics?\b|location")
# Sections where a single "A > B" line is a lineage chain rather than, say, a segue in a flaw note
_LINEAGE_KEY = re.compile(r"(?i)source|lineage|recording|transfer|master|equipment|generation|chain|location")
# "Ted Mattes' Master Audience Dat", "Jerry Moore's master cassettes"
_TAPER_MASTER = re.compile(r"([A-Z][\w.-]*(?:[ \t]+[A-Z][\w.-]*){0,3})'s?[ \t]+(?i:master)\b")
_TRANSFERER = re.compile(
    r"(?i:\b(?:transfer(?:s|red)?|master(?:ing|ed)|remaster(?:ed|ing)?|mixed|pitch[- ]corrected|seeded|edited"
    r"|extract(?:ion|ed))\b)[^\n.]*?(?i:\bby)[ \t]+" + _PEOPLE
)
_THANKS = re.compile(r"(?i:\bthanks[ \t]+to)[ \t]+" + _PEOPLE + r"[ \t]+(?i:for[ \t]+the[ \t]+(?:seed|transfer))")
_EMAIL = re.compile(r"\S+@\S+\.\w+")

_STATE_CODES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY ON QC BC AB MB NS".split()
)
# Full names and old-style abbreviations seen in info files, keyed without dots or spaces
_STATE_NAMES = {
    "alabama": "AL", "ala": "AL", "arizona": "AZ", "ariz": "AZ", "arkansas": "AR", "ark": "AR",
    "california": "CA", "calif": "CA", "cal": "CA", "colorado": "CO", "colo": "CO", "connecticut": "CT",
    "conn": "CT", "delaware": "DE", "del": "DE", "florida": "FL", "fla": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "ill": "IL", "indiana": "IN", "ind": "IN",
    "iowa": "IA", "kansas": "KS", "kan": "KS", "kans": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "mass": "MA", "michigan": "MI", "mich": "MI",
    "minnesota": "MN", "minn": "MN", "mississippi": "MS", "miss": "MS", "missouri": "MO", "montana": "MT",
    "mont": "MT", "nebraska": "NE", "neb": "NE", "nebr": "NE", "nevada": "NV", "nev": "NV",
    "newhampshire": "NH", "newjersey": "NJ", "newmexico": "NM", "newyork": "NY", "northcarolina": "NC",
    "northdakota": "ND", "ohio": "OH", "oklahoma": "OK", "okla": "OK", "oregon": "OR", "ore": "OR",
    "pennsylvania": "PA", "penn": "PA", "penna": "PA", "rhodeisland": "RI", "southcarolina": "SC",
    "southdakota": "SD", "tennessee": "TN", "tenn": "TN", "texas": "TX", "tex": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "wash": "WA", "westvirginia": "WV",
    "wva": "WV", "wisconsin": "WI", "wis": "WI", "wisc": "WI", "wyoming": "WY", "wyo": "WY",
    "ontario": "ON", "ont": "ON", "quebec": "QC", "britishcolumbia": "BC",
}
_COUNTRIES = {
    "england", "scotland", "wales", "uk", "germany", "west germany", "france", "denmark",
    "netherlands", "holland", "sweden", "luxembourg", "egypt", "canada", "jamaica", "spain",
}
# Abbreviations that stand for a city, keyed without dots or spaces
_CITY_NAMES = {"sf": "San Francisco", "nyc": "New York", "la": "Los Angeles", "dc": "Washington"}
_WEEKDAYS = re.compile(r"(?i)\b(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\b\.?")


@dataclass
class TitleEntry:
    title: str
    raw: str
    line_no: int
    label: TrackLabel | None = None  # label written in the .txt, e.g. d1t01
    number: int | None = None
    disc: int | None = None  # from a "Disc n" heading or a d-label
    set_name: str | None = None  # "Set 1", "Set 2", "Encore"
    set_number: int | None = None  # the set it belongs to; an encore keeps the set it follows
    segue: bool = False
    encore: bool = False  # marked "E:" on the line itself
    missing: bool = False  # listed but marked "(missing)": no audio for it
    markers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # footnote text for the markers
    length: str | None = None

    @property
    def group(self) -> int:
        """Which disc/set group the title belongs to, for pairing with file groups."""
        if self.label:
            return self.label.group
        if self.disc:
            return self.disc
        return self.set_number or 1


@dataclass
class ShntoolRow:
    stem: str
    length: str


@dataclass
class UnparsedLine:
    """A line inside a numbered setlist that isn't numbered, e.g. an indented "Jam >"."""

    line_no: int
    text: str


@dataclass
class InfoText:
    band: str | None = None
    date: date | None = None
    venue: str | None = None
    city: str | None = None
    sources: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    taper: str | None = None
    transferer: str | None = None
    titles: list[TitleEntry] = field(default_factory=list)
    missing: list[TitleEntry] = field(default_factory=list)  # listed, but no audio exists
    unparsed: list[UnparsedLine] = field(default_factory=list)
    ignored: list[UnparsedLine] = field(default_factory=list)  # numbered lines that don't fit the setlist
    footnotes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    info_lines: list[tuple[str | None, str]] = field(default_factory=list)  # (section heading, line)
    shntool: dict[str, ShntoolRow] = field(default_factory=dict)  # name_key(stem) -> row

    def looks_like_setlist(self) -> bool:
        return len(self.titles) >= 3


def parse_info_text(text: str, band: str = "") -> InfoText:
    """The info .txt parsed. `band` (e.g. "Grateful Dead", "moe.") helps find the header when a trading round's
    banner sits above it."""
    info = InfoText()
    lines = text.split("\n")
    consumed = set()

    for i, line in enumerate(lines):
        row = _shntool_row(line)
        if row:
            info.shntool[name_key(row.stem)] = row
            consumed.add(i)
        elif re.match(r"(?i)^\s*length\s+expanded size", line) or re.search(r"\(totals for \d+ files\)", line):
            consumed.add(i)

    consumed |= _parse_header(lines, info, band)
    _parse_body(lines, consumed, info)
    _extract_source_details(info)
    return info


def parse_shntool_table(text: str) -> dict[str, ShntoolRow]:
    """Rows of a standalone shntool listing (shntool.txt, sbe_fail.txt), keyed by name_key(stem)."""
    rows = (_shntool_row(line) for line in text.split("\n"))
    return {name_key(r.stem): r for r in rows if r}


def _shntool_row(line: str) -> ShntoolRow | None:
    m = _SHNTOOL_ROW.match(line)
    return ShntoolRow(re.sub(r"(?i)\.wav$", "", m.group(2)), m.group(1)) if m else None


# --- header -------------------------------------------------------------------------------

_PLACE_WORDS = 10  # the longest venues and cities run to 8 words
_HEADER_LABEL = re.compile(r"(?i)^(band|artist|venue|location|place|city|state)\b\s*(.*)$")


def _header_start(lines: list[str], band_name: str = "") -> int:
    """Where the header block starts: the first block, unless it names neither the band nor a date and one of
    the next two blocks does (a trading round's banner above the show: "Audience Devotional Tree")."""
    blocks, start = [], None
    for i, line in enumerate(lines + [""]):
        if line.strip() and start is None:
            start = i
        elif not line.strip() and start is not None:
            blocks.append((start, [x.strip() for x in lines[start:i]]))
            start = None
        if len(blocks) == 3:
            break
    pattern = r"\s+".join(re.escape(w) for w in band_name.split()) if band_name.strip() else None
    band = lambda block: bool(pattern) and any(re.search(rf"(?i){pattern}", x) for x in block)  # noqa: E731
    dated = lambda block: any(find_date(x) for x in block)  # noqa: E731
    if not blocks or band(blocks[0][1]) or dated(blocks[0][1]):
        return 0
    later = [(at, b) for at, b in blocks[1:] if not any(_classify_heading(x) or _parse_track_line(x) for x in b)]
    for test in (band, dated):  # the band's name first: a banner like "PCRP#007NRPS-GD1970-11-8" has a date too
        for at, block in later:
            if test(block):
                return at
    return 0


def _parse_header(lines: list[str], info: InfoText, band: str = "") -> set[int]:
    """The first block of lines names the band, date, venue and city in some order."""
    used = set()
    places = []
    first = _header_start(lines, band)
    pattern = r"\s+".join(re.escape(w) for w in band.split()) if band.strip() else None
    gap = False
    for i, line in enumerate(lines):
        if i < first:
            continue
        s = line.strip()
        if not s:
            if used and (gap or places or info.date):
                break
            gap = bool(used)  # the band alone, a blank line, then the date and place: one gap is allowed
            continue
        kv = _KEY_VALUE.match(s)
        named = bool(pattern) and kv and re.search(rf"(?i){pattern}", kv.group(1))  # "Grateful Dead: Live at…"
        if len(used) >= 6 or _classify_heading(s) or _parse_track_line(s) or (kv and not named):
            break
        used.add(i)
        for part in re.split(r"\s+[-–—|]\s+", s):
            part = part.strip(" ,")
            label = _HEADER_LABEL.match(part)  # "VENUE War Memorial", "CITY Buffalo", "STATE NY"
            if label:
                part = label.group(2).strip(" ,")
                if label.group(1).lower() == "state" and places and not _as_city(places[-1]):
                    places[-1] = f"{places[-1]}, {part}"
                    continue
            if not part:
                continue
            if pattern and re.search(rf"(?i){pattern}", part):
                info.band = info.band or band
                part = re.sub(rf"(?i)^(?:the\s+)?{pattern}\s*(?:live\s+(?:at|in)\s*)?", "", part).strip(" ,")
            part = re.sub(r"(?i)^date\b\s*:?\s*", "", part)
            found = find_date(part) if info.date is None else None
            if found:
                info.date, (start, end) = found
                part = (part[:start] + " " + part[end:]).strip()
                part = _WEEKDAYS.sub("", part).strip(" ,-")
            if part and len(part.split()) <= _PLACE_WORDS:  # longer is a sentence: "This is the complete Dark Star…"
                places.append(part)

    # The city line comes first, so an event name above the venue ('"Formerly the Warlocks" Concert',
    # then "Hampton Coliseum", then "Hampton VA") doesn't push the venue into the city's place.
    cities = [(n, c) for n, c in ((n, _as_city(p)) for n, p in enumerate(places)) if c]
    if cities:
        at, (venue_part, info.city) = cities[0]
        others = [p for n, p in enumerate(places) if n != at and n not in {m for m, _ in cities}]
        before = [p for n, p in enumerate(places[:at]) if n not in {m for m, _ in cities}]
        info.venue = venue_part or (before[-1] if before else others[0] if others else None)
    elif places:
        info.venue = places[0]
        info.city = places[1] if len(places) > 1 else None
    info.venue = _tidy_place(info.venue)
    info.city = _tidy_place(info.city)
    return used


def _tidy_place(text: str | None) -> str | None:
    """Capitalize all-lowercase places and expand city abbreviations: "s.f., CA" -> "San Francisco, CA"."""
    if not text:
        return text
    parts = [p.strip() for p in text.split(",")]
    first = _CITY_NAMES.get(re.sub(r"[.\s]", "", parts[0]).lower()) if len(parts) > 1 else None
    if first:
        parts[0] = first
    text = ", ".join(parts)
    if text == text.lower():
        text = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), text)
    return text


def _as_city(text: str) -> tuple[str | None, str] | None:
    """Split "Hollywood Bowl, Hollywood, CA", "The Sportatorium,Pembroke Pines,Fla" or
    "San Francisco CA" into (venue or None, "City, ST")."""
    parts = [p.strip() for p in re.split(r"\s*,\s*", text) if p.strip()]
    if len(parts) >= 2:
        state = _state_code(parts[-1])
        if state:
            return ", ".join(parts[:-2]) or None, f"{parts[-2]}, {state}"
        if parts[-1].lower() in _COUNTRIES:
            if len(parts) >= 3 and parts[-2].upper().replace(".", "") in _PROVINCES:  # "Vancouver, BC, Canada"
                return ", ".join(parts[:-3]) or None, f"{parts[-3]}, {parts[-2].upper().replace('.', '')}"
            return ", ".join(parts[:-2]) or None, f"{parts[-2]}, {parts[-1]}"
    if len(parts) >= 2:  # "Capitol Thea., Port Chester NY"
        m = re.match(r"^(.+?)\s+([A-Z]{2})\.?$", parts[-1])
        if m and m.group(2) in _STATE_CODES:
            return ", ".join(parts[:-1]), f"{m.group(1)}, {m.group(2)}"
    m = re.match(r"^(.+?)\s+([A-Z]{2})\.?$", text)
    if m and m.group(2) in _STATE_CODES:
        return None, f"{m.group(1)}, {m.group(2)}"
    return None


_PROVINCES = {"BC", "AB", "SK", "MB", "ON", "QC", "PQ", "NB", "NS", "PE", "PEI", "NL", "NF"}


def _state_code(text: str) -> str | None:
    key = re.sub(r"[.\s]", "", text)
    if key.upper() in _STATE_CODES and len(key) == 2:
        return key.upper()
    return _STATE_NAMES.get(key.lower())


# --- body ---------------------------------------------------------------------------------

def _classify_heading(s: str) -> tuple[str, object, str] | None:
    m = _DISC_HEADING.match(s)
    if m and not _looks_like_title_after_number(m.group(2)):
        return ("disc", _number(m.group(1)), m.group(2))
    m = _SET_HEADING.match(s)
    if m and (not _looks_like_title_after_number(m.group(2)) or re.match(r"(?i)^(?:set|s)\s*\w+\s*:", s)):
        return ("set", f"Set {_number(m.group(1))}", m.group(2))
    wrapped = re.fullmatch(r"[-–—=*~_\s]+(\w.*?)[-–—=*~_\s]+", s)
    if wrapped:  # "-- encore --", "--Set Two begin--", "== Disc 2 =="
        return _classify_heading(wrapped.group(1))
    m = _ENCORE_HEADING.match(s)
    if m and (s.lower().startswith("encore") or re.match(r"(?i)^e\s*:", s)):
        return ("encore", "Encore", m.group(1))
    m = _NOTES_HEADING.match(s)
    if m and (s.rstrip().endswith(":") or ":" in s[:15] or s.lower().rstrip() in ("notes", "note", "comments")):
        return ("notes", None, m.group(1))
    return None


def _looks_like_title_after_number(rest: str) -> bool:
    """'Set 1' is a heading; 'S 1 Promised Land' is not. Allow short parentheticals and dashes."""
    rest = rest.strip(" /,|")
    if not rest:
        return False
    if re.match(r"(?i)^[(\[]|^(?:disc|disk|cd|set)\b|^cont(?:inued|'d|\.)?\b|^(?:begins?|ends?|starts?)$|^[-–]?\s*(?:[a-d]|begins?|ends?|starts?|cont\w*)\s*:?$|^\d{1,3}:\d{2}",
                rest):
        return False  # "Set 1 (cont)", "SET1 / DISC 1", "Disc One: 60:07"
    return True


def _number(token: str) -> int:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _ROMAN.get(token) or _WORDS[token]


def _set_in(text: str) -> str | None:
    """A set named inside a disc heading: "Disc 1 (1st set)", "Disc 3 (2nd set/cont)", "Disc 1 - Set 1"."""
    m = re.search(r"(?i)\b(\d)(?:st|nd|rd|th)\s+set\b|\bset\s*(\d)\b", text)
    if m:
        return f"Set {m.group(1) or m.group(2)}"
    if re.search(r"(?i)\bencores?\b", text):
        return "Encore"
    return None


def _parse_body(lines: list[str], consumed: set[int], info: InfoText) -> None:
    section = None  # None (before any heading), "setlist", "notes" or "info"
    section_title = None  # heading of the current "info" section, e.g. "Transfer Info"
    disc = None
    set_name = None
    set_number = None
    prose: list[str] = []  # a setlist written as running text, collected until it ends
    prose_line = 0
    bare = []  # unnumbered lines inside setlist sections, used only if nothing is numbered
    paragraph: list[str] = []  # consecutive free-text lines, read together for lineage chains
    after_track = False  # the previous non-blank line was a track line
    context_key = None  # key of the "Source: ..." line a continuation belongs to
    numbered = 0  # titles that came from numbered or labeled lines
    suspect: list[UnparsedLine] = []  # unnumbered lines right after a track line

    def flush_paragraph() -> None:
        # A chain may wrap over several lines; keep up to the last line that has an arrow.
        # Bullets ("> small cut in Crazy Fingers") are lists, not chains.
        lines_ = [line for line in paragraph if not re.match(r"^[>*•-]+\s", line)]
        arrows = [n for n, line in enumerate(lines_) if ">" in line]
        context = context_key or section_title or ""
        if arrows and sum(line.count(">") for line in lines_) < 2 and not _LINEAGE_KEY.search(context):
            arrows = []  # "problems during Me & My Uncle > Big River" is a segue, not a chain
        if lines_ and re.match(r"(?i)^(?:known\s+)?(?:flaws?|problems?|notes?|comments?)\b", lines_[0]):
            arrows = []
        if arrows:
            chain = " ".join(lines_[:arrows[-1] + 1])
            if not _EMAIL.search(chain):
                info.lineage.append(re.sub(r"\s+", " ", chain))
        paragraph.clear()

    def keep(entry: TitleEntry) -> None:
        (info.missing if entry.missing else info.titles).append(entry)

    def flush_prose() -> None:
        for piece in _split_prose(" ".join(prose)):
            entry = _make_entry(piece, piece, prose_line, None, None)
            if entry:
                entry.disc, entry.set_number = disc, set_number
                entry.set_name = "Encore" if entry.encore else set_name
                keep(entry)
        prose.clear()

    def add_track(s: str, i: int, encore: bool = False) -> bool:
        nonlocal numbered
        parsed = _parse_track_line(s)
        if not parsed:
            return False
        label, numbers, rest = parsed
        entry = _make_entry(rest, s, i, label, numbers[0] if numbers else None)
        if entry is None:
            return False
        parts = [entry]
        for n in numbers[1:]:  # "18-19. You Don't Have To Ask": the same title over several files
            parts.append(_make_entry(rest, s, i, None, n))
        for k, part in enumerate(parts):
            part.disc = disc if disc is not None else (label.group if label and label.kind == "d" else None)
            part.set_name = set_name or (f"Set {label.group}" if label and label.kind == "s" else None)
            part.set_number = set_number or (label.group if label and label.kind == "s" else None)
            if encore or part.encore:
                part.set_name = "Encore"
            if len(parts) > 1:
                part.notes.append(f"part {k + 1} of {len(parts)}")
            keep(part)
        numbered += 1
        return True

    for i, line in enumerate(lines):
        s = line.strip()
        if prose and (i in consumed or not s or _classify_heading(s) or _parse_track_line(s)
                      or (_KEY_VALUE.match(s) and not _KEY_VALUE.match(s).group(2))):
            flush_prose()
        elif prose:
            prose.append(s)
            continue
        if i in consumed or not s:
            flush_paragraph()
            context_key = None
            after_track = False
            suspect.clear()  # a blank line ends the run; trailing text is not part of the setlist
            continue

        heading = _classify_heading(s)
        if heading:
            flush_paragraph()
            context_key = None
            after_track = False
            suspect.clear()
            section_title = None
            kind, value, rest = heading
            if kind == "disc":
                disc = value
                section = "setlist"
                set_name = _set_in(rest) or set_name
                if set_name and set_name.startswith("Set "):
                    set_number = int(set_name[4:])
            elif kind == "set":
                set_name = value
                set_number = int(value[4:])
                section = "setlist"
                sub = _classify_heading(rest.strip(" ()[]-/")) if rest else None
                if sub and sub[0] == "disc":
                    disc = sub[1]
                elif rest and _looks_like_title_after_number(rest):
                    prose[:], prose_line = [rest], i
            elif kind == "encore":
                set_name = "Encore"
                section = "setlist"
                if rest and not add_track(rest, i, encore=True):
                    prose[:], prose_line = [rest], i
            elif kind == "notes":
                section = "notes"
                if rest:
                    _add_note(rest, info)
            continue

        gone = _MISSING_LINE.match(s)  # "<missing> Big River": played, but not on the tape
        if gone and section in (None, "setlist") and (entry := _make_entry(gone.group(1), s, i, None, None)):
            entry.missing, entry.disc, entry.set_name = True, disc, set_name
            info.missing.append(entry)
            continue

        if section in (None, "setlist") and add_track(s, i):
            flush_paragraph()
            info.unparsed.extend(suspect)  # sandwiched between track lines: almost surely a track
            suspect.clear()
            after_track = True
            continue

        kv = _KEY_VALUE.match(s)
        if kv and not kv.group(2):
            flush_paragraph()
            section, section_title = "info", kv.group(1)
            info.info_lines.append((section_title, s))  # "Recording by Bob Wagner:" names a person
            context_key = None
            after_track = False
            continue

        if section == "notes":
            _add_note(s, info)
        elif section == "setlist" and _FOOTNOTE.match(s):
            _add_note(s, info)  # "*Aud recording only" right under the setlist
        elif section == "setlist" and kv is None and len(s) <= 60:
            if after_track:
                suspect.append(UnparsedLine(i + 1, s))
            bare.append((s, i, disc, set_name, set_number))
            info.info_lines.append((section_title, s))
        else:
            if kv:
                context_key = kv.group(1)
            info.info_lines.append((context_key or section_title, s))
            if kv:  # "FM Source: SBD>FM>MC>DAT" stands on its own
                flush_paragraph()
                if ">" in s and not _EMAIL.search(s):
                    info.lineage.append(re.sub(r"\s+", " ", s))
            else:
                paragraph.append(s)
    flush_prose()
    flush_paragraph()

    if not numbered and bare:
        # No numbered lines at all: the setlist is plain titles under headings.
        for s, i, d, sn, snum in bare:
            entry = _make_entry(s, s, i, None, None)
            if entry:
                entry.disc, entry.set_name, entry.set_number = d, sn, snum
                keep(entry)
        info.titles.sort(key=lambda t: t.line_no)

    # Track numbers run in sequence (restarting at 1 per disc); "40-50 feet back" after track 7 is text.
    last = None
    kept = []
    for t in info.titles:
        if t.label is None and t.number is not None and last is not None and t.number not in (1, last + 1, last + 2):
            info.ignored.append(UnparsedLine(t.line_no, t.raw))
            info.info_lines.append((None, t.raw))
            continue
        if t.number is not None:
            last = t.number  # a labeled track's number counts too: "d2t03" then "2 Source Matrix"
        kept.append(t)
    info.titles = kept

    # A setlist labeled d1t01... doesn't also use bare numbers: "2 Source Matrix" below it is text.
    labeled = [t for t in info.titles if t.label]
    if len(labeled) >= 3 and len(labeled) >= 0.8 * len(info.titles):
        info.ignored += [UnparsedLine(t.line_no, t.raw) for t in info.titles if not t.label and t.number is not None]
        info.titles = [t for t in info.titles if t.label or t.number is None]

    # Without an "Encore" heading, an "Encore Break" track still marks what follows as the encore.
    after_break = False
    for entry in info.titles:
        if after_break:
            entry.set_name = "Encore"
        if re.fullmatch(r"(?i)encore\s+break", entry.title):
            after_break = True
        entry.notes = [info.footnotes[m] for m in entry.markers if m in info.footnotes] + entry.notes


def _split_prose(text: str) -> list[str]:
    """ "Minglewood Blues-> Sugaree-> Little Red Rooster, Tennessee Jed" -> titles, segues kept as " >". """
    parts = re.split(r"\s*(->|>|,|;)\s*", text)
    out = []
    for k in range(0, len(parts), 2):
        piece = parts[k].strip()
        sep = parts[k + 1] if k + 1 < len(parts) else ""
        if piece:
            out.append(piece + (" >" if ">" in sep else ""))
    return out


def _add_note(s: str, info: InfoText) -> None:
    info.notes.append(s)
    m = _FOOTNOTE.match(s)
    if m and not s.startswith("--"):
        info.footnotes.setdefault(m.group(1), m.group(2).strip(" -"))


def _parse_track_line(s: str) -> tuple[TrackLabel | None, list[int], str] | None:
    m = _LABELED.match(s)
    if m:
        label = TrackLabel(m.group(1).lower(), int(m.group(2)), int(m.group(3)))
        return (label, [label.track], m.group(4)) if m.group(4).strip() else None
    m = _NUMBERED.match(s)
    if m:
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        numbers = list(range(first, last + 1)) if 0 <= last - first < 10 else [first]
        return None, numbers, m.group(3)
    return None


def _make_entry(rest: str, raw: str, line_no: int, label, number) -> TitleEntry | None:
    notes = []
    m = re.search(r"\s\*+\s*([A-Za-z].*)$", rest)  # "you don't have to ask *fade in/out of tape flip"
    if m:
        notes.append(m.group(1).strip())
        rest = rest[:m.start()]
    lead = re.match(r"^\s*[\[(](" + _DURATION + r")[\])]\s+(?=\S)", rest)  # "01 [06:31] Bertha*"
    if lead:
        rest = rest[lead.end():]
    missing = bool(re.search(r"(?i)\((?:missing|not recorded|not taped|not broadcast|not included)\)", rest))
    rest = re.sub(r"(?i)\s*\((?:missing|not recorded|not taped|not broadcast|not included)\)", "", rest)
    title, segue, markers, encore, length = normalize_title(rest)
    if not title or not re.search(r"[A-Za-z]", title):
        return None
    return TitleEntry(title=title, raw=raw, line_no=line_no + 1, label=label, number=number,
                      segue=segue, encore=encore, missing=missing, markers=markers, notes=notes,
                      length=length or (lead.group(1) if lead else None))


def normalize_title(text: str) -> tuple[str, bool, list[str], bool, str | None]:
    """Strip markers from a setlist title. Returns (title, segue, markers, encore, length)."""
    s = text.strip()
    segue = False
    encore = False
    length = None
    markers: list[str] = []

    while True:
        m = _LEADING.match(s)
        if not m or m.end() == len(s):
            break
        token = m.group(1).lower().replace(" ", "")
        if token.rstrip(":-") in ("e", "encore"):
            encore = True
        else:
            markers.extend(r.group(0) for r in _MARKER_RUN.finditer(token))
        s = s[m.end():]

    trailing = []
    while True:
        m = _TRAILING.search(s)
        if not m or m.start() == 0:
            break
        token = m.group(1)
        if ">" in token:
            segue = True
        elif re.fullmatch(r"[\[(]?" + _DURATION + r"[\])]?", token):
            length = length or token.strip("[]()")
        else:
            trailing.extend(reversed([r.group(0) for r in _MARKER_RUN.finditer(token)]))
        s = s[:m.start()]

    markers.extend(reversed(trailing))
    s = re.sub(r"\s*//\s*", " ", s)  # a cut mid-title: "you don't // have to ask"
    title = re.sub(r"\s+", " ", s).strip(" -")
    return title, segue, markers, encore, length


# --- source details -----------------------------------------------------------------------

def _extract_source_details(info: InfoText) -> None:
    # A patch section names the source of a few seconds of audio, not the recording itself.
    source_text = "\n".join(line for title, line in info.info_lines
                            if not (title and "patch" in title.lower()))
    # "mics near the soundboard" says where the microphones were, not that it's a soundboard tape
    source_text = _NEAR_BOARD.sub(" ", source_text)
    hits = []
    for name, pattern in _SOURCE_TYPES:
        m = pattern.search(source_text)
        if m:
            hits.append((m.start(), name))
    info.sources = [name for _, name in sorted(hits)]
    if "DTS" in info.sources and re.search(r"\b5\.1\b", source_text):
        info.sources[info.sources.index("DTS")] = "DTS 5.1"

    searchable = "\n".join([line for _, line in info.info_lines] + info.notes)
    m = _TAPER.search(searchable) or _TAPER_MASTER.search(searchable)
    if not m:
        for key, line in info.info_lines:
            if key and _SOURCE_KEY.search(key) and (m := _TAPER_AT_END.search(line)):
                break
    if m:
        info.taper = m.group(1).strip()
    people = []
    for m in [*_TRANSFERER.finditer(searchable), *_THANKS.finditer(searchable)]:
        name = m.group(1).strip(" .")
        if name not in people:
            people.append(name)
    info.transferer = ", ".join(people) or None
