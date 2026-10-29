"""Images (§6.10): museum collections, maps, photographs and other pictures.

An image item is usually one picture (a museum's object, a map sheet) and sometimes hundreds (a community
upload). Each image's original is taken (TIFF, JPEG 2000, JPEG or PNG), with a map's companion files (.tfw, .fgd,
.aux.xml: what places it on the globe); `[images] format = "jpeg"` takes viewable JPEGs instead, much smaller.

    Images/{collection}/{title}.tif           one picture: named after the item
    Images/{collection}/{title}/…             several: in a folder, by their own names

The collection is the item's most specific one ("Cleveland Museum of Art"). Each picture gets an XMP sidecar
({name}.{ext}.xmp) with the item's title, creator, date, description, subjects, rights and link, which photo managers
(digiKam, darktable, Lightroom, Immich, PhotoPrism) read; nothing needs installing to write it.
"""

import html
import re
from dataclasses import replace
from pathlib import PurePosixPath
from xml.sax.saxutils import escape

from ..select import TILE, Profile, Selection, size_of
from .base import Handler, Plan, clean, collection_title

IMAGE_FORMATS = ("tiff", "jpeg 2000", "jpeg", "png", "gif", "bmp", "webp")
IMAGE_EXT = (".tif", ".tiff", ".jp2", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
COMPANIONS = (".tfw", ".tifw", ".jgw", ".pgw", ".fgd", ".aux.xml", ".prj", ".j2w")  # georeferencing beside a map
UMBRELLAS = {"image", "opensource_image", "additional_collections_image"}
COLLECTION_KEY = "_iafetch_collection"


class Images(Handler):
    name = "images"

    def kind(self, item) -> str:
        return "images"

    def prepare(self, item, archive) -> None:
        if COLLECTION_KEY not in item.raw:
            item.raw[COLLECTION_KEY] = collection_title(item, archive, UMBRELLAS)

    def profile(self, item, config=None) -> Profile:
        return Profile(originals=False)  # a viewable JPEG may be archive.org's

    def narrow(self, item, selection: Selection, config=None) -> Selection:
        settings = (config.settings.get("images") if config else None) or {}
        pictures = image_files(selection.files, settings.get("format", "original"))
        stems = {_stem(f) for f in pictures}
        companions = [f for f in selection.files if f["name"].lower().endswith(COMPANIONS) and _stem(f) in stems]
        return replace(selection, files=pictures + companions)

    def plan(self, item, staged, selection, config) -> Plan:
        collection = item.raw.get(COLLECTION_KEY) or "Other"
        title = _short(clean(item.title))
        pictures = [f for f in selection.files if _is_picture(f)]
        folder = config.folder("images") / _short(collection)
        single = len(pictures) == 1
        dest = config.inside(folder if single else folder / title)
        if not single and dest.exists():
            dest = config.inside(folder / f"{title} [{item.identifier}]")
        plan = Plan(item.identifier, self.name, str(staged), str(dest), sidecar=f".iafetch/{item.identifier}/iafetch.json")
        base = {}  # each picture's stem -> its new name, without the extension (companions follow it)
        for f in pictures:
            name = title if single else _short(clean(PurePosixPath(f["name"]).stem))
            if (dest / (name + _ext(f))).exists():  # another picture of the same name (Mona Lisa, again)
                name = f"{name} [{item.identifier}]"
            base[_stem(f)] = name
            plan.moves.append([f["name"], name + _ext(f)])
            plan.writes[name + _ext(f) + ".xmp"] = xmp(item, collection)  # "Mona Lisa.tif.xmp": darktable, Immich, digiKam
        for f in selection.files:
            if not _is_picture(f) and _stem(f) in base:
                suffix = next(s for s in COMPANIONS if f["name"].lower().endswith(s))
                plan.moves.append([f["name"], base[_stem(f)] + suffix])
        for f in selection.record:
            plan.moves.append([f["name"], f".iafetch/{item.identifier}/{PurePosixPath(f['name']).name}"])
        if selection.tile:
            plan.moves.append([TILE, f".iafetch/{item.identifier}/{TILE}"])
        if not pictures:
            plan.review.append("no pictures to take (try --as generic)")
        plan.approved = not plan.review
        return plan

    def process(self, plan: Plan, item, selection, config):
        from .generic import file_plan
        return file_plan(plan, item, selection, config, self.name)


def image_files(files: list[dict], prefer: str = "original") -> list[dict]:
    """One file per picture: its original (or, with prefer "jpeg", a JPEG of it: the original if it is one,
    else archive.org's copy), never the thumbnails."""
    pictures = [f for f in files if _is_picture(f)]
    by_picture: dict[str, list[dict]] = {}
    for f in pictures:
        by_picture.setdefault(_stem(f), []).append(f)
    out = []
    for group in by_picture.values():
        originals = [f for f in group if f.get("source") == "original"] or group
        if prefer == "jpeg":
            jpegs = [f for f in group if _ext(f) in (".jpg", ".jpeg")]
            out.append(max(jpegs, key=size_of) if jpegs else max(originals, key=size_of))
        else:
            out.append(max(originals, key=size_of))
    return sorted(out, key=lambda f: _natural(f["name"]))


def xmp(item, collection: str) -> str:
    """An XMP sidecar: the item's description of the picture, in the form photo managers read."""
    def bag(tag: str, values: list[str], kind: str = "Bag") -> str:
        values = [v for v in values if v.strip()]
        if not values:
            return ""
        items = "".join(f"<rdf:li>{escape(v.strip())}</rdf:li>" for v in values)
        return f"   <{tag}><rdf:{kind}>{items}</rdf:{kind}></{tag}>\n"

    def alt(tag: str, value: str) -> str:
        return f'   <{tag}><rdf:Alt><rdf:li xml:lang="x-default">{escape(value)}</rdf:li></rdf:Alt></{tag}>\n' if value.strip() else ""

    description = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)<br\s*/?>|</p>", "\n", item.field("description")))).strip()
    rights = html.unescape(re.sub(r"<[^>]+>", "", item.field("rights"))).strip() or item.field("licenseurl")
    subjects = [s.strip() for v in item.values("subject") for s in v.split(";")]
    date = item.field("date") or item.field("year")
    link = f"https://archive.org/details/{item.identifier}"
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
            '    xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">\n'
            + alt("dc:title", item.title)
            + bag("dc:creator", item.values("creator"), "Seq")
            + alt("dc:description", description)
            + bag("dc:subject", subjects)
            + alt("dc:rights", rights)
            + (f"   <dc:source>{escape(link)}</dc:source>\n")
            + (f"   <xmpRights:WebStatement>{escape(item.field('licenseurl') or link)}</xmpRights:WebStatement>\n")
            + (f"   <photoshop:DateCreated>{escape(date)}</photoshop:DateCreated>\n" if re.match(r"^\d{4}", date) else "")
            + (f"   <photoshop:Source>{escape(collection)}</photoshop:Source>\n" if collection else "")
            + '  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end="w"?>\n')


def _is_picture(f: dict) -> bool:
    return (_ext(f) in IMAGE_EXT and f.get("format", "").lower() in IMAGE_FORMATS
            and not re.search(r"(?i)_thumb\b|_thumb\.|spectrogram", f["name"]))


def _stem(f: dict) -> str:
    """What makes files the same picture: the name without extension, "_full" or archive.org's suffixes."""
    name = PurePosixPath(f.get("original") or f["name"] if f.get("source") == "derivative" else f["name"]).name.lower()
    for suffix in sorted(COMPANIONS, key=len, reverse=True):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name.rsplit(".", 1)[0]


def _ext(f: dict) -> str:
    return PurePosixPath(f["name"]).suffix.lower()


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _short(name: str, limit: int = 150) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0]
