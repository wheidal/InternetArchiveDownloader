"""Downloading an item's selected files into the inbox, verified (F15, F15a-d, F16).

Files go to a hidden <inbox>/.<identifier>.partial/ folder, each through iacore's resumable, MD5-checked
fetch. Only when every one has passed is the folder renamed to <inbox>/<identifier>/. A run that's stopped
resumes from the partial folder next time; a file already there and correct isn't fetched again.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from iacore.download import DownloadError, RemoteFile, Throttle, check, fetch_file
from iacore.http import download_url

from .select import Selection, size_of

# Characters a Windows/SMB share (the NAS) can't hold in a name.
UNSAFE = re.compile(r'[<>:"\\|?*\x00-\x1f]')


class DiskFull(Exception):
    """Too little space left: the queue pauses, the item isn't failed (F15d, R20)."""


@dataclass
class Staged:
    folder: Path
    files: int
    bytes: int  # downloaded in this run
    reused: int  # files already there from an earlier, interrupted run


def staging(inbox: Path, identifier: str) -> Path:
    return inbox / f".{identifier}.partial"


def safe_name(name: str) -> str:
    """One path segment made safe for the NAS: unsafe characters replaced, no trailing dots or spaces."""
    clean = UNSAFE.sub("_", name).rstrip(" .")
    return clean or "_"


def safe_path(folder: Path, name: str) -> Path:
    """Where a file named `name` (archive.org's path within the item, untrusted) goes inside `folder`. Raises
    ValueError for one that would land outside it (F15b)."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or name.startswith("/") or ".." in parts:
        raise ValueError(f"refusing the file name {name!r}: it points outside the item's folder")
    path = folder.joinpath(*(safe_name(p) for p in parts))
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError(f"refusing the file name {name!r}: it points outside the item's folder")
    return path


def download_item(identifier: str, selection: Selection, inbox: Path, client, *, throttle: Throttle | None = None,
                  reserve: int = 0, log=lambda line: None, progress=None, disk_usage=None) -> Staged:
    """Fetch the selection into the inbox; returns the finished <inbox>/<identifier>/ folder."""
    disk_usage = disk_usage or shutil.disk_usage
    final = inbox / identifier
    if final.exists():
        raise DownloadError(f"{final} is already there (downloaded earlier but not filed); file or remove it first")
    part = staging(inbox, identifier)
    part.mkdir(parents=True, exist_ok=True)
    wanted = [(f, _remote(f)) for f in selection.files]
    wanted += [(f, RemoteFile(f["name"], None, None)) for f in selection.record]  # records: never self-checked
    if selection.tile:
        wanted.append((selection.tile, _remote(selection.tile)))
    got = reused = 0
    left = sum(size_of(f) for f, _ in wanted)
    for n, (f, remote) in enumerate(wanted, 1):
        dest = safe_path(part, f["name"])
        if progress:
            progress.file(n, len(wanted), f["name"], remote.size)
        if remote.size is not None and dest.is_file() and not check(dest, remote):
            reused += 1
            left -= remote.size
            if progress:
                progress.have_file(remote.size)
            continue
        free = disk_usage(inbox).free
        if free - left < reserve:
            raise DiskFull(f"{inbox} has {free / 1024**3:.1f} GB free; {identifier} needs about {left / 1024**3:.1f} "
                           f"GB more and {reserve / 1024**3:.0f} GB is kept free")
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"  {f['name']}" + (f" ({remote.size / 1024**2:.1f} MB)" if remote.size else ""))
        fetch_file(download_url(identifier, f["name"]), remote, dest, throttle=throttle, opener=client.open,
                   log=log, progress=progress)
        got += dest.stat().st_size
        left -= size_of(f)
    part.rename(final)
    return Staged(final, len(wanted), got, reused)


def _remote(f: dict) -> RemoteFile:
    return RemoteFile(f["name"], size_of(f) if f.get("size") is not None else None, f.get("md5"))
