"""Books, manuals and magazines (§6.8): books filed the way Calibre imports them, magazines by title and year.

A scanned book on archive.org comes as page images (a JP2 zip, hundreds of MB) plus what archive.org derives
from them: a searchable PDF, an OCR'd EPUB, plain text. The handler takes the reading copy, not the scans:

    pdf:  a searchable PDF: the uploaded "Text PDF", else archive.org's "Additional Text PDF" (an uploaded
          scan-only "Image Container PDF" is taken only when there's nothing searchable)
    epub: the EPUB, if listed (archive.org's are made by OCR, so they're off by default)

and files it as

    Books/{author}/{title} ({year})/{title} ({year}).pdf    with metadata.opf and cover.jpg

which Calibre's "Add books from folders" reads. The item's records go in a hidden .iafetch/<identifier>/.

A magazine (an item in archive.org's magazine_rack collections) is one issue, filed by the magazine it belongs to
(the most specific of its collections, looked up once and cached) and its year:

    Magazines/ZZap!64 Magazine/1985/ZZap!64 Magazine - Issue 001 (1985-05).pdf

The issue number comes from the item's issue field or its title, the month from its date or title. Only when
nothing tells one issue from another does it go to review.
"""

import re
from xml.sax.saxutils import escape
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from ..select import TILE, Profile, Selection, size_of
from .base import Handler, Plan, clean

PDF_FORMATS = ("text pdf", "additional text pdf", "pdf", "image container pdf")  # most wanted first
EPUB_FORMATS = ("epub",)
DEFAULT_FORMATS = ["pdf"]
# TOSEC-style tags at the end of a title: "Programmers Reference Guide (1983)(Commodore)"
TOSEC = re.compile(r"\s*\((\d{4}|\d{4}-\d\d(-\d\d)?|19xx)\)\s*(\(([^)]*)\))?\s*$")
MAX_NAME = 120
DERIVED = re.compile(r"_text$")  # archive.org's name for the searchable PDF it makes from an upload


class Texts(Handler):
    name = "texts"

    def kind(self, item) -> str:
        return "magazines" if is_magazine(item) else "books"

    def prepare(self, item, archive) -> None:
        if is_magazine(item) and SERIES_KEY not in item.raw:
            item.raw[SERIES_KEY] = magazine_name(item, archive)

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)  # the searchable PDF is often a derivative

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        wanted = _formats(config)
        chosen = []
        if "pdf" in wanted:
            for files in _documents(selection.files, PDF_FORMATS).values():
                chosen.append(min(files, key=lambda f: PDF_FORMATS.index(_fmt(f))))
        if "epub" in wanted:
            for files in _documents(selection.files, EPUB_FORMATS).values():
                chosen.append(max(files, key=size_of))
        return replace(selection, files=sorted(chosen, key=lambda f: f["name"]))

    def plan(self, item, staged, selection, config) -> Plan:
        if is_magazine(item):
            return magazine_plan(item, staged, selection, config)
        title, year, author = book_title(item), item.year, author_of(item)
        name = _short(f"{title} ({year})" if year else title)
        dest = config.inside(config.folder("books") / _short(author) / name)
        if dest.exists():  # another scan or edition of the same book: keep both
            dest = config.inside(dest.parent / f"{name} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        by_ext = defaultdict(list)
        for f in selection.files:
            by_ext[_ext(f)].append(f)
        for ext, files in by_ext.items():
            for f in files:  # one document: named after the book; several: each keeps its own name
                plan.moves.append([f["name"], name + ext if len(files) == 1 else _short(clean(PurePosixPath(f["name"]).stem)) + ext])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, "cover.jpg"])
        plan.writes["metadata.opf"] = opf(item, title, author, has_cover=bool(selection.tile))
        if not selection.files:
            plan.review.append("no PDF or EPUB to take (try --as generic)")
        if dest.exists():
            plan.review.append(f"{dest} already exists")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        return file_plan(plan, item, selection, config, self.name)


def _formats(config) -> list[str]:
    settings = (config.settings.get("texts") if config else None) or {}
    return [x.lower() for x in settings.get("formats", DEFAULT_FORMATS)]


def _fmt(f: dict) -> str:
    return f.get("format", "").lower()


def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _documents(files: list[dict], formats: tuple[str, ...]) -> dict[str, list[dict]]:
    """Files of these formats, grouped by the document they are, by name: book.pdf and archive.org's
    book_text.pdf are one document. (A derivative's `original` often names the scans it was made from, not
    the PDF, so it can't be used.)"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        if _fmt(f) in formats:
            groups[DERIVED.sub("", str(PurePosixPath(f["name"]).with_suffix("")))].append(f)
    return groups


def _short(name: str) -> str:
    return name if len(name) <= MAX_NAME else name[:MAX_NAME].rsplit(" ", 1)[0]


# --- names and metadata -------------------------------------------------------------------------------

def book_title(item) -> str:
    """The title without TOSEC tags. A long subtitle ("Title : an easy introduction to ...") is dropped from
    the folder name; metadata.opf keeps the whole title."""
    title = TOSEC.sub("", item.title)
    main = re.split(r"\s+:\s+|\s+/\s+", title)[0]
    return clean(main)


def author_of(item) -> str:
    """The first creator, else the publisher, else a TOSEC tag's publisher, else "Unknown"."""
    for value in (item.values("creator")[:1], item.values("publisher")[:1]):
        if value and value[0].strip():
            return clean(value[0])
    m = TOSEC.search(item.title)
    if m and m.group(4):
        return clean(m.group(4))
    return "Unknown"


def opf(item, title: str, author: str, has_cover: bool) -> str:
    """Calibre's metadata.opf, laid out as Calibre writes it: what it reads when adding a book from a folder."""
    lines = []

    def add(tag: str, text: str, attrs: str = "") -> None:
        if text and text.strip():
            lines.append(f"    <dc:{tag}{attrs}>{escape(text.strip())}</dc:{tag}>")

    add("identifier", item.identifier, ' id="archive_id" opf:scheme="archive.org"')
    add("title", clean(TOSEC.sub("", item.title)))
    for creator in item.values("creator") or ([author] if author != "Unknown" else []):
        add("creator", creator, ' opf:role="aut"')
    add("date", item.field("date") or item.year)
    add("publisher", (item.values("publisher") or [""])[0])
    add("language", (item.values("language") or [""])[0])
    add("description", re.sub(r"<[^>]+>", "", item.field("description")))
    for subject in item.values("subject"):
        for one in subject.split(";"):
            add("subject", one)
    for isbn in item.values("isbn"):
        add("identifier", isbn, ' opf:scheme="ISBN"')
    for oclc in item.values("oclc-id"):
        add("identifier", oclc, ' opf:scheme="OCLC"')
    add("source", f"https://archive.org/details/{item.identifier}")
    guide = ('  <guide>\n    <reference type="cover" title="Cover" href="cover.jpg"/>\n  </guide>\n'
             if has_cover else "")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="archive_id" version="2.0">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
            + "\n".join(lines) + "\n  </metadata>\n" + guide + "</package>\n")


# --- a game's manual -------------------------------------------------------------------------------------

# Where archive.org keeps the manuals for each platform's software
MANUAL_COLLECTIONS = {"Commodore 64": "commodore_c64_manuals"}
ENGLISH = {"eng", "english", "en"}
REGION = re.compile(r"\((IT|DE|ES|FR|NL|SE|DK|FI|HU|PL|NO)\)", re.I)


def base_title(title: str) -> str:
    """A title reduced for matching a game with its manual: no "Commodore C64 Manual:" prefix, no tags in
    brackets, no leading article, lower case, letters and digits only."""
    title = re.sub(r"(?i)^commodore (c?64|c64 manual|64 manual)\s*:\s*", "", title)
    title = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title)
    title = re.sub(r"(?i),\s*the$", "", title.strip())
    title = re.sub(r"(?i)^(the|a|an)\s+", "", title)
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def manual_docs(archive, game, platform: str) -> list[dict]:
    """English manuals for the game in archive.org's manuals collection for its platform, best first."""
    collection = MANUAL_COLLECTIONS.get(platform)
    want = base_title(game.title)
    if not collection or not want:
        return []
    from ..api import build_query
    query = build_query(" ".join(f'title:"{w}"' for w in want.split()[:6]), collection=collection)
    found = [d for d in archive.search_docs(query, ["identifier", "title", "language"], rows=50)
             if base_title(str(d.get("title", ""))) == want and not REGION.search(str(d.get("title", "")))
             and (not d.get("language") or {str(x).lower() for x in _as_list(d["language"])} & ENGLISH)]
    return sorted(found, key=lambda d: ("[" in str(d.get("title", "")), str(d.get("title", ""))))


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def manual_plan(item, staged, selection, config, game_folder) -> Plan:
    """A game's manual, filed in the game's folder as "Manual.pdf" (or by its own names, if several)."""
    from pathlib import Path
    dest = config.inside(Path(game_folder))
    plan = Plan(item.identifier, "texts", str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
    for f in selection.files:
        name = "Manual" + _ext(f) if len(selection.files) == 1 else _short(clean(PurePosixPath(f["name"]).stem)) + _ext(f)
        if (dest / name).exists():
            name = f"Manual [{item.identifier}]{_ext(f)}"
        plan.moves.append([f["name"], name])
    for f in selection.record:
        plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
    if selection.tile:
        plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}"])
    if not selection.files:
        plan.review.append("no PDF or EPUB to take (try --as generic)")
    plan.approved = not plan.review
    return plan



# --- magazines ---------------------------------------------------------------------------------------------

MAGAZINE_ROOT = "magazine_rack"  # archive.org's collection of periodicals; every magazine item lists it
SERIES_KEY = "_iafetch_magazine"  # where prepare() keeps the magazine's name on the item
ISSUE = re.compile(r"(?i)(?:\b(?:issue|no\.?|nr\.?|number|ausgabe|numero)|#)\s*(\d{1,4})\b")
MONTHS = {name: n for n, names in enumerate((
    ("january", "jan", "januar", "janvier", "gennaio"), ("february", "feb", "februar", "fevrier", "febbraio"),
    ("march", "mar", "märz", "maerz", "mars", "marzo"), ("april", "apr", "avril", "aprile"),
    ("may", "mai", "maggio"), ("june", "jun", "juni", "juin", "giugno"), ("july", "jul", "juli", "juillet", "luglio"),
    ("august", "aug", "aout", "agosto"), ("september", "sep", "sept", "settembre"),
    ("october", "oct", "oktober", "octobre", "ottobre"), ("november", "nov", "novembre"),
    ("december", "dec", "dezember", "decembre", "dicembre")), 1) for name in names}
YEAR = r"(1[89]\d\d|20\d\d)"
MONTH_FORMS = (  # (pattern, which group is the year, which the month)
    (re.compile(rf"\b{YEAR}[-/._](0[1-9]|1[0-2])(?!\d)"), 1, 2),  # 1985-05, 1984_04
    (re.compile(rf"(?<!\d)(0?[1-9]|1[0-2])[ ./_-]{YEAR}\b"), 2, 1),  # 10 1987, 05/1985
    (re.compile(rf"(?i)\b({'|'.join(sorted(MONTHS, key=len, reverse=True))})\.?,?\s+{YEAR}\b"), 2, 1),  # February 1951
)
VOLUME = re.compile(r"(?i)\bvol(?:ume)?\.?\s*(\d{1,4})\b")
TITLE_ISSUE = re.compile(r"(?i)\s*(?:[-:,]\s*)?(?:\b(?:issue|no\.?|nr\.?|number|vol\.?|volume|#)\s*\d+.*|"
                         r"\b(?:1[89]\d\d|20\d\d)\b.*|\(.*\)\s*)$")


def is_magazine(item) -> bool:
    return MAGAZINE_ROOT in item.collections


def magazine_name(item, archive) -> str:
    """The magazine an issue belongs to: the title of the most specific of its collections (one that isn't a
    parent of another), e.g. "ZZap!64 Magazine" rather than "Commodore Computer Periodicals". Falls back on
    the issue's title without its number or date."""
    candidates = [c for c in item.collections if c != MAGAZINE_ROOT and not c.startswith("fav-")]
    found: dict[str, tuple[str, list[str]]] = {}
    for c in candidates:
        try:
            coll = archive.item(c)
        except Exception:  # the network, a missing collection: do without it
            continue
        found[c] = (coll.title, coll.collections)
    leaves = [c for c in found if not any(c in parents for o, (_, parents) in found.items() if o != c)]
    if leaves:
        return clean(re.sub(r"(?i)\s+(archive|collection)$", "", found[leaves[0]][0].strip()))
    return title_series(item.title)


def title_series(title: str) -> str:
    """ "ZZap!64 Magazine Issue 001" -> "ZZap!64 Magazine"; "64'er 1984-04" -> "64'er"."""
    return clean(TITLE_ISSUE.sub("", title)) or clean(title)


def issue_of(item) -> int | None:
    """The issue number: the item's issue field, else "Issue 12", "No. 12" or "#12" in its title."""
    m = re.search(r"\d{1,4}", item.field("issue"))
    if m:
        return int(m.group(0))
    m = ISSUE.search(item.title)
    return int(m.group(1)) if m else None


def volume_of(item) -> int | None:
    """The volume: the item's volume field, else "Vol. 3" or "Volume 03" in its title."""
    m = re.search(r"\d{1,4}", item.field("volume"))
    if m:
        return int(m.group(0))
    m = VOLUME.search(item.title)
    return int(m.group(1)) if m else None


def month_of(item) -> str:
    """ "1985-05" from the date, the title or the identifier ("1985-05", "10 1987", "February 1951"); "" if
    only a year (or nothing) is known."""
    for text in (item.field("date"), ISSUE.sub(" ", item.title), item.identifier):  # "Issue 10 1985" isn't October
        for pattern, y, m in MONTH_FORMS:
            found = pattern.search(text)
            if found:
                month = found.group(m)
                month = MONTHS[month.lower()] if month.lower() in MONTHS else int(month)
                return f"{found.group(y)}-{month:02d}"
    return ""


def magazine_plan(item, staged, selection, config) -> Plan:
    from pathlib import Path
    series = item.raw.get(SERIES_KEY) or title_series(item.title)
    issue, volume, month = issue_of(item), volume_of(item), month_of(item)
    year = item.year or month[:4] or (re.search(rf"\b{YEAR}\b", item.title) or [None, ""])[1]
    if issue is not None and volume is not None:  # numbers restart each volume
        name = f"{series} - Vol. {volume:02d} No. {issue:02d}" + (f" ({month})" if month else "")
    elif issue is not None:
        name = f"{series} - Issue {issue:03d}" + (f" ({month})" if month else "")
    elif month:
        name = f"{series} - {month}"
    else:
        name = clean(item.title)
    folder = config.folder("magazines") / _short(series)
    dest = config.inside(folder / year if year else folder)
    plan = Plan(item.identifier, "texts", str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
    name = _short(name)
    for f in selection.files:
        own = name if len(selection.files) == 1 else _short(clean(PurePosixPath(f["name"]).stem))
        if (dest / (own + _ext(f))).exists():  # another scan of the same issue: keep both
            own = f"{own} [{item.identifier}]"
        plan.moves.append([f["name"], own + _ext(f)])
    for f in selection.record:
        plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
    if selection.tile:
        plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}"])
    if not selection.files:
        plan.review.append("no PDF or EPUB to take (try --as generic)")
    if issue is None and not month and not year:
        plan.review.append("nothing tells which issue this is (no issue number or date): name it in the plan")
    plan.approved = not plan.review
    return plan
