"""archive.org's APIs: item metadata (cached, R9), search (restricted items always left out, R14), and
turning what the user typed (an identifier or an archive.org URL) into an identifier."""

import json
import re
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from iacore.http import ARCHIVE, FetchError, metadata_url
from iacore.search import parse_json

from .rights import RESTRICTED_QUERY

METADATA_TTL = 7 * 24 * 3600
SEARCH_FIELDS = ["identifier", "title", "mediatype", "item_size", "licenseurl", "year", "downloads"]
SORTS = {"downloads": "downloads desc", "date": "date desc", "title": "titleSorter asc", "rating": "avg_rating desc"}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


@dataclass
class Item:
    identifier: str
    metadata: dict
    files: list[dict]
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_json(cls, identifier: str, data: dict) -> "Item":
        if not data or "metadata" not in data:
            raise FetchError(f"{identifier}: archive.org has no such item (or it has been taken down)")
        return cls(identifier, data["metadata"], data.get("files", []), data)

    def field(self, name: str) -> str:
        """A metadata field as one string (archive.org gives some as lists)."""
        value = self.metadata.get(name)
        if isinstance(value, list):
            return "; ".join(str(v) for v in value)
        return "" if value is None else str(value)

    def values(self, name: str) -> list[str]:
        value = self.metadata.get(name)
        if value is None:
            return []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    @property
    def title(self) -> str:
        return self.field("title") or self.identifier

    @property
    def mediatype(self) -> str:
        return self.field("mediatype")

    @property
    def collections(self) -> list[str]:
        return self.values("collection")

    @property
    def year(self) -> str:
        m = re.search(r"\b(1[89]\d\d|20\d\d)\b", self.field("year") or self.field("date"))
        return m.group(1) if m else ""

    @property
    def dark(self) -> bool:
        return bool(self.raw.get("is_dark"))


def identifier_of(text: str) -> tuple[str, str]:
    """(identifier, file within it or "") from an identifier or an archive.org details/download URL (F5)."""
    text = text.strip()
    if "://" in text or text.startswith("archive.org/"):
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
        segments = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
        if len(segments) >= 2 and segments[0] in ("details", "download", "metadata"):
            identifier, rest = segments[1], "/".join(segments[2:])
            if segments[0] == "download":
                return _checked(identifier), rest
            return _checked(identifier), ""
        raise ValueError(f"{text}: not an archive.org item URL (expected /details/<id> or /download/<id>/...)")
    return _checked(text), ""


def _checked(identifier: str) -> str:
    if not ID_RE.fullmatch(identifier):
        raise ValueError(f"{identifier!r} isn't an archive.org identifier")
    return identifier


class Archive:
    """Metadata and search through a net.Client, with metadata kept in a local SQLite cache."""

    def __init__(self, client, cache: Path | None = None, *, ttl: float = METADATA_TTL, clock=time.time):
        self.client, self.ttl, self.clock = client, ttl, clock
        self.db = None
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(cache)
            self.db.execute("CREATE TABLE IF NOT EXISTS metadata (identifier TEXT PRIMARY KEY, fetched REAL, json TEXT)")

    def item(self, identifier: str, refresh: bool = False) -> Item:
        data = None if refresh else self._cached(identifier)
        if data is None:
            data = json.loads(self.client.get(metadata_url(identifier)))
            if data and self.db:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)",
                                    (identifier, self.clock(), json.dumps(data)))
        return Item.from_json(identifier, data)

    def _cached(self, identifier: str) -> dict | None:
        if not self.db:
            return None
        row = self.db.execute("SELECT fetched, json FROM metadata WHERE identifier = ?", (identifier,)).fetchone()
        if row and self.clock() - row[0] < self.ttl:
            return json.loads(row[1])
        return None

    def search(self, text: str = "", *, collection: str = "", mediatype: str = "", creator: str = "",
               year: str = "", subject: str = "", license: str = "", sort: str = "downloads",
               limit: int = 50) -> list[dict]:
        """Up to `limit` matching items, never restricted ones (F1, R14)."""
        if not 1 <= limit <= 1000:
            raise ValueError("--limit must be 1 to 1000")
        query = build_query(text, collection=collection, mediatype=mediatype, creator=creator, year=year,
                            subject=subject, license=license)
        return _first_page(query, limit, self.client.get, SORTS.get(sort, sort))[:limit]

    def close(self) -> None:
        if self.db:
            self.db.close()

    __del__ = close


def _first_page(query: str, rows: int, get, sort: str) -> list[dict]:
    """One page of advanced search: a listing to look at, not an enumeration (that's the scrape API's job)."""
    params = [("q", query), ("rows", str(rows)), ("page", "1"), ("output", "json"), ("sort[]", sort)]
    params += [("fl[]", f) for f in SEARCH_FIELDS]
    data = parse_json(get(f"{ARCHIVE}/advancedsearch.php?" + urllib.parse.urlencode(params)))
    return (data.get("response") or {}).get("docs") or []


LICENSES = {"pd": "(licenseurl:*publicdomain* OR licenseurl:*/mark/*)", "cc": "licenseurl:*creativecommons*",
            "by": "licenseurl:*/by/*", "any": "licenseurl:*"}


def build_query(text: str = "", **fields: str) -> str:
    """A search query from free text and field filters, with the restriction filter always added (R14)."""
    parts = [f"({text})"] if text.strip() else []
    for name, value in fields.items():
        if not value:
            continue
        if name == "license":
            parts.append(LICENSES.get(value, f"licenseurl:{_quote(value)}"))
        elif name == "year" and re.fullmatch(r"\d{4}-\d{4}", value):
            parts.append(f"year:[{value[:4]} TO {value[5:]}]")
        else:
            parts.append(f"{name}:{_quote(value)}")
    if not parts:
        raise ValueError("search for something: words, or a filter such as --collection")
    return " AND ".join(parts) + " " + RESTRICTED_QUERY


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r"\s", value) else value
