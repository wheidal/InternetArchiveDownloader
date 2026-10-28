"""The fallback for every kind (§6.1): the item's selected files, as archive.org has them, in
<library>/<kind>/<identifier>/, with _meta.xml, _files.xml, the item tile as folder.jpg and iafetch.json."""

import shutil
from pathlib import Path

from .. import rights
from ..download import safe_name, safe_path
from ..select import TILE
from .base import Handler, Plan

KINDS = {"data": "data", "image": "images"}


class Generic(Handler):
    name = "generic"

    def kind(self, item) -> str:
        return KINDS.get(item.mediatype, "other")

    def plan(self, item, staged: Path, selection, config) -> Plan:
        dest = config.inside(config.folder(self.kind(item)) / safe_name(item.identifier))
        moves = []
        for f in selection.files + selection.record:
            rel = safe_path(staged, f["name"]).relative_to(staged).as_posix()
            moves.append([rel, rel])
        if selection.tile:
            moves.append([TILE, "folder.jpg"])
        plan = Plan(item.identifier, self.name, str(staged), str(dest), moves, approved=True)
        if dest.exists():
            plan.review.append(f"{dest} already exists")
            plan.approved = False
        return plan

    def process(self, plan: Plan, item, selection, config) -> Path:
        return file_plan(plan, item, selection, config, self.name)


def file_plan(plan: Plan, item, selection, config, handler: str) -> Path:
    """Carry out a plan's moves into the library and write the sidecar. Every destination is checked to be
    inside the item's folder, and the folder inside the root (R21)."""
    source, dest = Path(plan.source), config.inside(Path(plan.dest))
    dest.mkdir(parents=True, exist_ok=True)
    for src_rel, dest_rel in plan.moves:
        src, target = safe_path(source, src_rel), safe_path(dest, dest_rel)
        if not src.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, target)
    rights.write_sidecar(dest, rights.sidecar(item, selection.files, handler=handler))
    _remove_empty(source)
    return dest


def _remove_empty(folder: Path) -> None:
    """Remove the downloaded folder if moving its files left nothing (empty subfolders aside)."""
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass
