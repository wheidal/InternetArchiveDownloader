"""The handlers, and which one an item gets (F6): a collection rule from the config first, then a mediatype
rule, then generic. Rules naming a handler that isn't built yet fall back to generic."""

from .deadtag import Deadtag
from .generic import Generic
from .video import Video

HANDLERS = {h.name: h for h in (Generic(), Deadtag(), Video())}
MEDIATYPES = {"movies": "video"}  # mediatype -> handler


def choose(item, config, override: str = "") -> tuple[object, str]:
    """(handler, why)."""
    if override:
        if override not in HANDLERS:
            raise ValueError(f"--as {override}: no such handler ({', '.join(HANDLERS)})")
        return HANDLERS[override], "chosen with --as"
    for collection in item.collections:
        name = config.handlers.get(collection)
        if name:
            if name in HANDLERS:
                return HANDLERS[name], f"collection {collection}"
            return HANDLERS["generic"], f"collection {collection} maps to {name}, which isn't built yet"
    name = MEDIATYPES.get(item.mediatype)
    if name:
        return HANDLERS[name], f"mediatype {item.mediatype}"
    return HANDLERS["generic"], "no other handler applies"


def selection(handler, item, options: dict, config=None):
    """The files to download: the handler's profile, adjusted by the command line's options, then narrowed
    by the handler (unless formats were named: those are taken as asked)."""
    from ..select import select
    p = handler.profile(item, config)
    if options.get("all"):
        p.originals = False
    p.formats = options.get("formats") or p.formats
    p.globs = options.get("globs") or p.globs
    p.exclude = p.exclude + options.get("exclude", [])
    sel = select(item, p)
    return sel if options.get("formats") else handler.narrow(item, sel, config)
