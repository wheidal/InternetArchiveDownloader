"""Cleaning up song titles: capitalizing all-lowercase ones, and spotting abbreviations.

Tapers often type setlists in a hurry: "brown eyed women", "BIODTL", "LL Rain". The plan
capitalizes the first kind always; the abbreviations are only expanded when asked
(`deadtag plan --expand-titles`), from archive.org's title or the file name's.

Adapted from deadtag (DeadShowTagger, the same author), which keeps its own Grateful-Dead-specific copy;
this one is iafetch's, for every etree band.
"""

import re

from .infotxt import normalize_title

_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to"}
_DOTTED = re.compile(r"(?:[a-z]\.){2,}")  # b.i.o.d.t.l.
_SEGMENT = re.compile(r"\s*(?:->|>)\s*|\s+/\s+")


def capitalize(title: str) -> str:
    """Title-case a title typed in all lowercase; any other title is returned unchanged."""
    if not title or title != title.lower():
        return title
    out = []
    start = True  # first word of the title, or of a part after > ( - :
    for word in title.split(" "):
        if _DOTTED.fullmatch(word):
            out.append(word.upper())
        elif word in _SMALL and not start:
            out.append(word)
        else:
            out.append(re.sub(r"[a-z]", lambda m: m.group(0).upper(), word, count=1))
        start = bool(re.search(r"(?:^|[>(:-])$", word))
    return " ".join(out)


def expansion(short: str, full: str | None) -> str | None:
    """The full title when `short` abbreviates it (BIODTL, LL Rain, China Cat > IKYR), else None."""
    if not full:
        return None
    full_title = normalize_title(full)[0]
    if _words(short) == _words(full_title):
        return None
    shorts = _SEGMENT.split(short.strip())
    fulls = _SEGMENT.split(full_title)
    if len(shorts) != len(fulls) or not all(_abbreviates(s, f) for s, f in zip(shorts, fulls)):
        return None
    return capitalize(full_title)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9& ]+", "", text.lower().replace("&", " & ")).split()


def _abbreviates(short: str, full: str) -> bool:
    """Each word of `short` is an initialism of the next words of `full`, or a shortening of one.

    A short title of two or more words may stop early ("China Cat" for "China Cat Sunflower").
    """
    tokens = [t for t in (re.sub(r"[^a-z0-9]", "", w) for w in short.lower().split()) if t]
    words = _words(full)
    words = [w for w in words if w != "&"]
    if not tokens or not words:
        return False
    i = 0
    for token in tokens:
        if i >= len(words):
            return False
        span = _initialism(token, words[i:])
        if span:
            i += span
        elif _shortens(token, words[i]):
            i += 1
        else:
            return False
    return i == len(words) or len(tokens) > 1


def _initialism(token: str, words: list[str]) -> int:
    """How many words `token` is the initials of (2 or more), or 0."""
    if len(token) < 2 or len(token) > len(words):
        return 0
    if all(words[k][0] == c for k, c in enumerate(token)):
        return len(token)
    return 0


def _shortens(token: str, word: str) -> bool:
    """"sugar"/"sugar", "mag"/"magnolia", "rr"/"railroad": same first letter, letters in order."""
    if token == word:
        return True
    if not token.isalpha() or token[0] != word[0] or len(token) >= len(word):
        return False
    rest = iter(word[1:])
    return all(c in rest for c in token[1:])
