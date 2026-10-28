"""Books and manuals (§6.8), filed the way Calibre imports them.

A scanned book on archive.org comes as page images (a JP2 zip, hundreds of MB) plus what archive.org derives
from them: a searchable PDF, an OCR'd EPUB, plain text. The handler takes the reading copy, not the scans:

    pdf:  a searchable PDF: the uploaded "Text PDF", else archive.org's "Additional Text PDF" (an uploaded
          scan-only "Image Container PDF" is taken only when there's nothing searchable)
    epub: the EPUB, if listed (archive.org's are made by OCR, so they're off by default)

and files it as

    Books/{author}/{title} ({year})/{title} ({year}).pdf    with metadata.opf and cover.jpg

which Calibre's "Add books from folders" reads. The item's records go in a hidden .iafetch/<identifier>/.
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
        return "books"

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
