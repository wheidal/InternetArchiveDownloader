"""What every handler does (F18): choose an item's files, then plan and carry out filing them in the library.

A plan that a handler isn't sure of is written as JSON for review, the deadtag way (F19); a sure one is
carried out at once.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..select import Profile

PLAN_SUFFIX = ".iafetch-plan.json"


@dataclass
class Plan:
    identifier: str
    handler: str
    source: str  # the downloaded folder, in the inbox
    dest: str  # the item's folder in the library
    moves: list[list[str]] = field(default_factory=list)  # [path in source, path in dest]
    review: list[str] = field(default_factory=list)  # why it needs a person to look (empty: it doesn't)
    warnings: list[str] = field(default_factory=list)
    approved: bool = False

    def write(self, folder: Path) -> Path:
        path = folder / f"{self.identifier}{PLAN_SUFFIX}"
        path.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> "Plan":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


class Handler:
    name = "base"
    downloads = True  # False: the item is handed to another tool instead (deadtag)

    def kind(self, item) -> str:
        """The library folder the item goes in (a key of Config.library)."""
        return "other"

    def profile(self, item) -> Profile:
        return Profile()

    def plan(self, item, staged: Path, selection, config) -> Plan:
        raise NotImplementedError

    def process(self, plan: Plan, item, selection, config) -> Path:
        raise NotImplementedError
