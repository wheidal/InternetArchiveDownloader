"""Settings from ~/.config/iafetch/config.toml.

Everything iafetch writes lives under one root folder (R21), which must already exist (R22): iafetch never
creates it, so an unmounted NAS stops a run instead of filling a local folder. Inside it, each kind of media
has its own folder, and so do the inbox and the archive of originals.

    root    = "/mnt/synology/InternetArchive"
    inbox   = "_Inbox"        # relative paths are inside root
    archive = "_Archive"
    reserve = "20G"           # free space always left on the root's drive

    [net]
    rate = "4M"               # bandwidth cap, or "none"
    api_pause = 1.0           # seconds between API calls, at least 1
    download_pause = 2.0      # seconds between file downloads, at least 1

    [library]                 # one folder per kind, inside root
    movies = "Movies"

    [handlers]                # collection -> handler, tried before the built-in rules
    GratefulDead = "deadtag"
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from iacore.download import parse_rate

CONFIG_FILE = Path("~/.config/iafetch/config.toml")
DEFAULT_ROOT = "/mnt/synology/InternetArchive"

# The respectful-use floors and ceilings (SPEC §3.2). Config can make them stricter, never looser.
MIN_API_PAUSE = 1.0
MIN_DOWNLOAD_PAUSE = 1.0
MAX_CONCURRENCY = 4

LIBRARY = {
    "movies": "Movies", "tv": "TV", "music": "Music", "livemusic": "Live Music", "radio": "Radio",
    "audiobooks": "Audiobooks", "books": "Books", "magazines": "Magazines", "comics": "Comics",
    "games": "Games", "images": "Images", "data": "Data", "other": "Other",
}
HANDLERS = {"GratefulDead": "deadtag", "librivoxaudio": "audiobook", "audio_bookspoetry": "audiobook",
            "audiolibrary": "audiobook", "gutenberg-audiobooks": "audiobook", "audio_poetry": "audiobook",
            "oldtimeradio": "radio", "radioprograms": "radio",
            "netlabels": "music", "georgeblood": "music", "78rpm": "music", "audio_music": "music"}


class ConfigError(Exception):
    pass


@dataclass
class NetSettings:
    rate: float | None = 4 * 1024 ** 2
    rate_text: str = "4M"
    concurrency: int = 1
    api_pause: float = 1.0
    download_pause: float = 2.0


@dataclass
class Config:
    root: Path
    inbox: Path
    archive: Path | None
    net: NetSettings = field(default_factory=NetSettings)
    library: dict[str, Path] = field(default_factory=dict)
    handlers: dict[str, str] = field(default_factory=lambda: dict(HANDLERS))
    acknowledged_terms: str = ""
    settings: dict = field(default_factory=dict)  # per-handler sections, e.g. [video]
    reserve: int = 20 * 1024 ** 3  # R20: free space kept on the root's drive
    path: Path | None = None  # the file it came from

    def folder(self, kind: str) -> Path:
        return self.library.get(kind) or self.library["other"]

    def check_root(self) -> None:
        """R22: the root must exist (iafetch never creates it). Call before writing anything."""
        if not self.root.is_dir():
            raise ConfigError(f"the root folder {self.root} doesn't exist. If it's on the NAS, check that it's "
                              f"mounted; if it's new, create it by hand first (iafetch never creates it)")

    def inside(self, path: Path) -> Path:
        """R21: `path`, resolved, if it's inside the root; else a ConfigError."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ConfigError(f"{path} is outside the root folder {self.root}; iafetch writes only inside it")
        return resolved


def load_config(path: Path | None = None) -> Config:
    path = (path or Path(os.environ.get("IAFETCH_CONFIG") or CONFIG_FILE)).expanduser()
    raw = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    return parse_config(raw, path)


def parse_config(raw: dict, path: Path | None = None) -> Config:
    root_text = raw.get("root", DEFAULT_ROOT)
    root = Path(os.path.normpath(Path(str(root_text)).expanduser()))
    if not root.is_absolute():
        raise ConfigError(f"root must be an absolute path, not {root_text!r}")

    def under(value, what: str) -> Path:
        p = Path(str(value)).expanduser()
        p = Path(os.path.normpath(p if p.is_absolute() else root / p))
        if not p.is_relative_to(root):  # R21 (by name: loading the config never touches the NAS; inside() follows links)
            raise ConfigError(f"{what} ({p}) is outside the root folder {root}; everything iafetch writes "
                              f"must be inside it")
        return p

    inbox = under(raw.get("inbox", "_Inbox"), "inbox")
    archive = under(raw.get("archive", "_Archive"), "archive") if raw.get("archive", "_Archive") else None
    library = {kind: under(raw.get("library", {}).get(kind, name), f"library.{kind}") for kind, name in LIBRARY.items()}
    for kind in raw.get("library", {}):
        if kind not in LIBRARY:
            raise ConfigError(f"[library] {kind}: not a kind iafetch knows ({', '.join(LIBRARY)})")
    try:
        reserve = int(parse_rate(str(raw.get("reserve", "20G"))) or 0)
    except ValueError as exc:
        raise ConfigError(f"reserve: {exc}") from exc
    return Config(root, inbox, archive, _net(raw.get("net", {})), library,
                  {**HANDLERS, **{str(k): str(v) for k, v in raw.get("handlers", {}).items()}},
                  str(raw.get("acknowledged_terms", "")), _settings(raw), reserve, path=path)


def _settings(raw: dict) -> dict:
    video = dict(raw.get("video", {}))
    quality = video.setdefault("quality", "h264")
    if quality not in ("h264", "original", "smallest"):
        raise ConfigError(f"[video] quality must be h264, original or smallest, not {quality!r}")
    video.setdefault("subtitles", True)
    video.setdefault("nfo", True)
    return {**{k: v for k, v in raw.items() if isinstance(v, dict) and k not in ("net", "library", "handlers")},
            "video": video}


def _net(raw: dict) -> NetSettings:
    net = NetSettings()
    if "rate" in raw:
        try:
            net.rate, net.rate_text = parse_rate(str(raw["rate"])), str(raw["rate"])
        except ValueError as exc:
            raise ConfigError(f"[net] rate: {exc}") from exc
    try:
        net.concurrency = int(raw.get("concurrency", net.concurrency))
        net.api_pause = float(raw.get("api_pause", net.api_pause))
        net.download_pause = float(raw.get("download_pause", net.download_pause))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[net]: {exc}") from exc
    if not 1 <= net.concurrency <= MAX_CONCURRENCY:
        raise ConfigError(f"[net] concurrency must be 1 to {MAX_CONCURRENCY} (archive.org's limit for bots)")
    if net.api_pause < MIN_API_PAUSE or net.download_pause < MIN_DOWNLOAD_PAUSE:
        raise ConfigError(f"[net] api_pause and download_pause must be at least {MIN_API_PAUSE:g} s")
    return net


def acknowledge_terms(config: Config, today: str) -> None:
    """R18: record that the user read the terms notice, in the config file (created if need be)."""
    path = config.path or CONFIG_FILE.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f'acknowledged_terms = "{today}"\n'
    lines = [x for x in text.splitlines(keepends=True) if not x.startswith("acknowledged_terms")]
    path.write_text(line + "".join(lines), encoding="utf-8")
    config.acknowledged_terms = today
