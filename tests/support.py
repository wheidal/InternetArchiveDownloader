"""A fake archive.org for the tests: items with files whose bytes, sizes and MD5s agree. Nothing here
touches the network."""

import hashlib
import io
import json
import urllib.error
import urllib.parse
from email.message import Message


def body(name: str, size: int = 1000) -> bytes:
    return (name.encode() * (size // max(len(name), 1) + 1))[:size]


def file(name: str, source: str = "original", fmt: str = "MPEG2", size: int = 1000, **extra) -> dict:
    data = body(name, size)
    return {"name": name, "source": source, "format": fmt, "size": str(size),
            "md5": hashlib.md5(data).hexdigest(), **extra}


def item_json(identifier: str, files: list[dict], **metadata) -> dict:
    meta = {"identifier": identifier, "mediatype": "movies", "collection": ["prelinger"], "title": "A Film",
            "date": "1935", "licenseurl": "http://creativecommons.org/licenses/publicdomain/", **metadata}
    records = [{"name": f"{identifier}_meta.xml", "source": "metadata", "format": "Metadata", "size": "300",
                "md5": "0" * 32},
               {"name": f"{identifier}_files.xml", "source": "metadata", "format": "Metadata", "md5": "1" * 32}]
    return {"metadata": meta, "files": files + records, "item_size": sum(int(f.get("size") or 0) for f in files)}


class Response(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200, headers: dict | None = None):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v


def http_error(url: str, code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(url, code, f"HTTP {code}", msg, io.BytesIO(b""))


class FakeArchive:
    """get(url) for metadata/search and open(url, start) for downloads, like iacore's http_get/http_open."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls: list[str] = []
        self.user_agents: list[str] = []
        self.fail: dict[str, list[int]] = {}  # file name -> HTTP codes to answer first, in turn
        self.search_docs: list[dict] = []

    def add(self, identifier: str, files: list[dict], **metadata) -> dict:
        self.items[identifier] = item_json(identifier, files, **metadata)
        return self.items[identifier]

    def get(self, url: str, limit: int = 0, timeout: int = 30, user_agent: str = "") -> bytes:
        from iacore.http import FetchError
        self.calls.append(url)
        self.user_agents.append(user_agent)
        path = urllib.parse.urlsplit(url).path
        if path.startswith("/metadata/"):
            return json.dumps(self.items.get(urllib.parse.unquote(path.split("/", 2)[2]), {})).encode()
        if path == "/advancedsearch.php":
            return json.dumps({"response": {"numFound": len(self.search_docs), "docs": self.search_docs}}).encode()
        raise FetchError(f"{url}: HTTP 404") from http_error(url, 404)

    def open(self, url: str, start: int = 0, timeout: int = 60, user_agent: str = ""):
        self.calls.append(url)
        self.user_agents.append(user_agent)
        parts = urllib.parse.urlsplit(url)
        _, _, identifier, name = parts.path.split("/", 3)
        name = urllib.parse.unquote(name)
        codes = self.fail.get(name)
        if codes:
            raise http_error(url, codes.pop(0), {"Retry-After": "1"})
        data = self.data(urllib.parse.unquote(identifier), name)
        if start:
            return Response(data[start:], 206, {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
        return Response(data)

    def data(self, identifier: str, name: str) -> bytes:
        for f in self.items[identifier]["files"]:
            if f["name"] == name:
                return body(name, int(f.get("size") or 300))
        raise http_error(name, 404)
