"""The handlers, and which one an item gets (F6): a collection rule from the config first, then a mediatype
rule, then generic. Rules naming a handler that isn't built yet fall back to generic."""

from .deadtag import Deadtag
from .generic import Generic

HANDLERS = {h.name: h for h in (Generic(), Deadtag())}
MEDIATYPES: dict[str, str] = {}  # mediatype -> handler, as handlers are added


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
