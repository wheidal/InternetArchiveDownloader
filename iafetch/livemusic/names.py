"""Dates, shnids and track labels found in archive.org names and info text.

Adapted from deadtag (DeadShowTagger, the same author), which keeps its own Grateful-Dead-specific copy;
this one is iafetch's, for every etree band.
"""

import re
from dataclasses import dataclass
from datetime import date

AUDIO_EXTENSIONS = (".flac", ".shn")

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_WORD = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)

# gd1972-06-17, gd72-06-17, 75-08-13 (always year first in archive names)
_NAME_DATE = re.compile(r"(?<![0-9])((?:19|20)?\d{2})-(\d{2})-(\d{2})(?![0-9])")

_TEXT_DATES = [
    # 1972-06-17
    ("ymd", re.compile(r"(?<![0-9])(?P<year>(?:19|20)\d{2})-(?P<mon>\d{1,2})-(?P<day>\d{1,2})(?![0-9])")),
    # June 17, 1972 / June 17th 1972 / Jun. 17, '72
    ("mdy", re.compile(
        _MONTH_WORD + r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+'?(?P<year>\d{4}|\d{2})(?![0-9])",
        re.I)),
    # 17 June 1972
    ("dmy", re.compile(
        r"(?<![0-9])(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_WORD + r",?\s+(?P<year>\d{4})(?![0-9])",
        re.I)),
    # 08/13/75, 8-13-1975, 08.13.75, 4/ 8/88 (US order)
    ("us", re.compile(
        r"(?<![0-9/.-])(?P<mon>\d{1,2}) ?[/.-] ?(?P<day>\d{1,2}) ?[/.-] ?(?P<year>\d{4}|\d{2})(?![0-9/.-])")),
]

# d1t01, s2t04, D1-T01, d1track01
# a source tag may run straight into the label: gd79-10-27audd1t01
_TRACK_LABEL = re.compile(r"(?i)(?:^|[^a-z]|(?<=aud)|(?<=sbd)|(?<=mtx)|(?<=fm))([ds])(\d{1,2})[-_ ]?t(?:rack)?[-_ ]?(\d{1,3})(?![0-9])")
_T_TRACK = re.compile(r"(?i)(?:^|[^a-z])t(?:rack)?[-_ ]?(\d{1,3})(?![0-9])")  # gd1987-04-11t01_crowd
_BARE_TRACK = re.compile(r"(?i)(?:^|[^a-z0-9])(\d{1,3})$")  # gd77-05-08_07
_LEADING_TRACK = re.compile(r"^(\d{1,3})(?!\d)")  # 01Tuning, 01 - Tuning


@dataclass(frozen=True)
class TrackLabel:
    """A disc/set + track label such as d1t01 or s2t04. kind is "d", "s" or "" (unlabeled)."""

    kind: str
    group: int
    track: int

    def __str__(self) -> str:
        if not self.kind:
            return f"t{self.track:02d}"
        return f"{self.kind}{self.group}t{self.track:02d}"


def make_date(year: int, month: int, day: int) -> date | None:
    if year < 100:  # two digits: tapes run from the 1960s on, so 06 is 2006 and 72 is 1972
        year += 2000 if year < 40 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_from_name(name: str) -> date | None:
    """The show date encoded in an archive file or folder name."""
    m = _NAME_DATE.search(name)
    if not m:
        return None
    return make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def find_date(text: str) -> tuple[date, tuple[int, int]] | None:
    """The first date written in free text, with the span it occupies."""
    found = []
    for kind, pattern in _TEXT_DATES:
        for m in pattern.finditer(text):
            year = int(m.group("year"))
            if kind in ("mdy", "dmy"):
                month = MONTHS[m.group("month").lower()[:3]]
                d = make_date(year, month, int(m.group("day")))
            else:
                d = make_date(year, int(m.group("mon")), int(m.group("day")))
            if d and 1960 <= d.year <= 1999:
                found.append((m.start(), d, m.span()))
                break
    if not found:
        return None
    found.sort(key=lambda f: f[0])
    return found[0][1], found[0][2]


def shnid_from_name(name: str) -> str | None:
    """The etree shnid: a purely numeric dot-separated token, e.g. 116272 in gd72-06-17.116272.txt."""
    for token in name.split("."):
        if token.isdigit() and 3 <= len(token) <= 7:
            return token
    return None


def parse_track_label(stem: str) -> TrackLabel | None:
    return split_track_name(stem)[0]


def split_track_name(stem: str) -> tuple[TrackLabel | None, str | None]:
    """A track file's disc/set + track label, and any title written after it.

    gd72-06-17s1t04 -> (s1t04, None); gd1987-04-11t02_Hell In A Bucket -> (t02, "Hell In A Bucket");
    02KickTheAmpJam -> (t02, "Kick The Amp Jam").
    """
    stem = re.sub(r"(?i)\.wav$", "", stem)
    undated = _NAME_DATE.sub(" ", stem)
    m = None
    for m in _TRACK_LABEL.finditer(undated):
        pass
    if m:
        label = TrackLabel(m.group(1).lower(), int(m.group(2)), int(m.group(3)))
    else:
        m = _T_TRACK.search(undated) or _BARE_TRACK.search(undated) or _LEADING_TRACK.match(undated)
        if not m:
            return None, None
        label = TrackLabel("", 1, int(m.group(1)))
    return label, _title_from_name(undated[m.end():])


def _title_from_name(rest: str) -> str | None:
    rest = rest.replace("_", " ").strip(" -.")
    if not re.search(r"[A-Za-z]{2}", rest):
        return None
    if " " not in rest:
        rest = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", rest)  # KickTheAmpJam -> Kick The Amp Jam
    return re.sub(r"\s+", " ", rest)


def name_key(stem: str) -> str:
    """Compare file stems loosely: "01 Tuning" (shntool) matches "01Tuning" (the file)."""
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def is_audio(name: str) -> bool:
    return name.lower().endswith(AUDIO_EXTENSIONS)


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def decode_text(data: bytes) -> str:
    """Decode an info file from UTF-8, falling back to Windows-1252, then Latin-1."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return re.sub(r"\r+\n", "\n", text).replace("\r", "\n")  # "\r\r\n" is one line break, not two
