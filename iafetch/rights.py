"""Access and rights (R14-R18): what iafetch never attempts, what it records about each item's license, and
the terms notice shown once."""

import json
import time
from pathlib import Path

from . import __version__

# Added to every search: lending library, stream-only and print-disabled items are never offered (R14).
RESTRICTED_QUERY = ("-access-restricted-item:true -collection:stream_only -collection:inlibrary "
                    "-collection:printdisabled -collection:lendinglibrary")
RESTRICTED_COLLECTIONS = {"stream_only", "inlibrary", "printdisabled", "lendinglibrary", "internetarchivebooks_lending"}
ENCRYPTED = ("_lcp.pdf", "_lcp.epub", ".acsm")  # DRM-wrapped lending copies: never selected

SIDECAR = "iafetch.json"

TERMS = """\
Before the first download: the Internet Archive's terms of use

  The Archive's collections are offered for scholarship and research. By using them you agree
  that your use is noninfringing or fair use, and that what you do with what you download is
  your responsibility. Many items carry no license at all; iafetch shows each item's license
  (or "no license declared") so you can judge.

  iafetch keeps archive.org's rules for automated clients: it says who it is, goes slowly
  (one download at a time, a bandwidth cap), backs off when asked, and never touches lending
  library, stream-only or private material.

  Full terms: https://archive.org/about/terms
"""


def restriction(item) -> str:
    """Why the item mustn't be downloaded, or "" (R14)."""
    if item.dark:
        return "it's dark (withdrawn from public access)"
    if item.field("access-restricted-item").lower() == "true":
        return "it's access-restricted (lending library or stream-only)"
    hit = RESTRICTED_COLLECTIONS.intersection(item.collections)
    if hit:
        return f"it's in the restricted collection {sorted(hit)[0]}"
    return ""


def forbidden_file(f: dict) -> str:
    """Why this file is never taken, or "": it's private, or a DRM-wrapped lending copy."""
    if str(f.get("private", "")).lower() == "true":
        return "private"
    name = f.get("name", "").lower()
    if name.endswith(ENCRYPTED) or "lcp" in f.get("format", "").lower().split():
        return "encrypted"
    return ""


def license_text(item) -> str:
    """The license, shortly, or plainly "no license declared" (R16)."""
    url = item.field("licenseurl")
    if url:
        u = url.lower()
        if "publicdomain" in u or "/mark/" in u or "/zero/" in u:
            return f"public domain ({url})"
        if "creativecommons.org/licenses/" in u:
            kind = u.split("/licenses/", 1)[1].strip("/").split("/")
            return f"CC {kind[0].upper()} {kind[1] if len(kind) > 1 else ''}".rstrip() + f" ({url})"
        return url
    rights = item.field("rights") or item.field("possible-copyright-status")
    return f"no license declared ({rights})" if rights else "no license declared"


def sidecar(item, files: list[dict], processed: dict[str, str] | None = None, handler: str = "generic") -> dict:
    """The iafetch.json record kept with each finished item (R16). No reviews or uploader details (R17).
    `processed` maps each library file to its MD5, for files changed after download (F17)."""
    return {
        "identifier": item.identifier,
        "source": f"https://archive.org/details/{item.identifier}",
        "title": item.title,
        "mediatype": item.mediatype,
        "collection": item.collections,
        "licenseurl": item.field("licenseurl"),
        "rights": item.field("rights"),
        "possible-copyright-status": item.field("possible-copyright-status"),
        "license": license_text(item),
        "downloaded": time.strftime("%Y-%m-%d %H:%M:%S"),
        "by": f"iafetch {__version__}",
        "handler": handler,
        "files": [{"name": f["name"], "size": _int(f.get("size")), "md5": f.get("md5"), "sha1": f.get("sha1")}
                  for f in files],
        "processed": processed or {},
    }


def write_sidecar(folder: Path, record: dict) -> Path:
    path = folder / SIDECAR
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
