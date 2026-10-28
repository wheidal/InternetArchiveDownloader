"""Films worth downloading, and the best copy of each (`iafetch picks`).

archive.org has no ratings of films, only of uploads. So:

1. Wikidata (CC0) says which archive.org items are which film: about 6,000 films carry an Internet Archive
   ID (P724) and an IMDb ID (P345), with the year and running time.
2. IMDb's ratings dataset (free for personal, non-commercial use) gives each film's rating and vote count.
3. For the best-rated films, archive.org's metadata of every upload gives its picture: the resolution and
   bitrate of the video iafetch would take. The best upload is the sharpest one that is plausibly the film
   (its length, year and title agree with Wikidata's); a doubtful one is shown but never queued.

Wikidata's list is kept 30 days and IMDb's ratings 7 (--refresh fetches both again); upload metadata goes
through the usual 7-day cache.
"""

import difflib
import gzip
import io
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from iacore.http import ARCHIVE, FetchError
from iacore.search import parse_json

from .api import ID_RE
from .handlers import HANDLERS, selection
from .handlers.video import seconds_of
from .net import user_agent
from .queue import data_dir
from .rights import RESTRICTED_QUERY, restriction

WIKIDATA = "https://query.wikidata.org/sparql"
IMDB_RATINGS = "https://datasets.imdbws.com/title.ratings.tsv.gz"
WIKIDATA_TTL, IMDB_TTL = 30 * 24 * 3600, 7 * 24 * 3600
CACHE_VERSION = 3
LOOKUP_BATCH = 100

# film, short, silent, animated, TV film, documentary
FILM_CLASSES = "wd:Q11424 wd:Q24862 wd:Q226730 wd:Q202866 wd:Q506240 wd:Q93204"
# pd: Wikidata says the film is in the public domain in the United States (or without naming a country)
QUERY = f"""SELECT ?film ?filmLabel ?ia ?imdb (MIN(YEAR(?date)) AS ?year) (MAX(?dur) AS ?minutes) (MAX(?pdus) AS ?pd)
  (GROUP_CONCAT(DISTINCT ?countryName; separator="|") AS ?countries)
WHERE {{
  VALUES ?class {{ {FILM_CLASSES} }}
  ?film wdt:P31 ?class; wdt:P724 ?ia; wdt:P345 ?imdb.
  OPTIONAL {{ ?film wdt:P577 ?date }}
  OPTIONAL {{ ?film wdt:P2047 ?dur }}
  OPTIONAL {{ ?film wdt:P495 ?country. ?country rdfs:label ?countryName. FILTER(LANG(?countryName) = "en") }}
  OPTIONAL {{ ?film p:P6216 ?st. ?st ps:P6216 wd:Q19652. OPTIONAL {{ ?st pq:P1001 ?where }}
             FILTER(!BOUND(?where) || ?where = wd:Q30) BIND(1 AS ?pdus) }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,de,fr,ru,it,es". }}
}} GROUP BY ?film ?filmLabel ?ia ?imdb"""

STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "to", "der", "die", "das", "le", "la", "les", "el"}


@dataclass
class Film:
    qid: str
    title: str
    year: int | None
    minutes: float | None
    imdb: str
    uploads: list[str] = field(default_factory=list)
    public_domain: bool = False  # in the US, per Wikidata
    countries: list[str] = field(default_factory=list)  # of origin, as Wikidata names them
    rating: float | None = None
    votes: int = 0


@dataclass
class Copy:
    """One upload of a film, as iafetch would download it."""
    identifier: str
    width: int = 0
    height: int = 0
    mbps: float = 0.0  # megabits per second: how much detail the encoding keeps
    size: int = 0
    minutes: float = 0.0
    parts: int = 1
    reviews: int = 0
    stars: float | None = None
    doubt: str = ""  # why it may not be the film, or ""

    @property
    def picture(self) -> str:
        res = f"{self.width}x{self.height}" if self.height else "size unknown"
        return res + (f", {self.mbps:.1f} Mbit/s" if self.mbps else "")

    def key(self) -> tuple:
        """Better copies sort first: plausible, then sharper, then less compressed, then better reviewed."""
        return (bool(self.doubt), -_band(self.height), -self.mbps, -self.reviews)


def _band(height: int) -> int:
    for band in (1080, 720, 576, 480, 360):
        if height >= band:
            return band
    return 0


# --- the lists ---------------------------------------------------------------------------------------

def fetch(url: str, *, accept: str = "*/*", timeout: int = 120):
    """An open response from Wikidata or IMDb's datasets (not archive.org: that goes through net.Client)."""
    host = urllib.parse.urlsplit(url).hostname
    if host not in ("query.wikidata.org", "datasets.imdbws.com"):
        raise ValueError(f"picks only reads Wikidata and IMDb's datasets, not {host}")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(), "Accept": accept})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except OSError as exc:
        raise FetchError(f"{host}: {getattr(exc, 'reason', exc)}") from exc


def wikidata_films(opener=fetch) -> list[Film]:
    url = WIKIDATA + "?" + urllib.parse.urlencode({"query": QUERY, "format": "json"})
    with opener(url, accept="application/sparql-results+json") as response:
        rows = json.loads(response.read())["results"]["bindings"]
    films: dict[str, Film] = {}
    for r in rows:
        value = {k: v["value"] for k, v in r.items()}
        qid = value["film"].rsplit("/", 1)[-1]
        film = films.get(qid)
        if film is None:
            minutes = _float(value.get("minutes"))
            film = films[qid] = Film(qid, value.get("filmLabel", qid), _int(value.get("year")),
                                     minutes if minutes and 1 <= minutes <= 400 else None, value["imdb"])
        film.public_domain = film.public_domain or value.get("pd") == "1"
        for country in filter(None, value.get("countries", "").split("|")):
            if country not in film.countries:
                film.countries.append(country)
        if ID_RE.fullmatch(value["ia"]) and value["ia"] not in film.uploads:
            film.uploads.append(value["ia"])
    return [f for f in films.values() if f.uploads and f.imdb.startswith("tt")]


def imdb_ratings(wanted: set[str], opener=fetch) -> dict[str, tuple[float, int]]:
    """{IMDb id: (rating, votes)} for the wanted films, read from IMDb's daily dataset as it streams in."""
    out = {}
    with opener(IMDB_RATINGS) as response:
        for line in io.TextIOWrapper(gzip.GzipFile(fileobj=response), encoding="utf-8"):
            tconst, _, rest = line.partition("\t")
            if tconst in wanted:
                rating, votes = rest.split("\t")
                out[tconst] = (float(rating), int(votes))
    return out


class Lists:
    """Wikidata's films with IMDb's ratings, cached in ~/.local/share/iafetch/picks.json."""

    def __init__(self, path: Path | None = None, opener=None, clock=time.time, log=lambda line: None):
        self.path, self.clock, self.log = path or data_dir() / "picks.json", clock, log
        self.opener = opener or (lambda *a, **kw: fetch(*a, **kw))  # looked up when called

    def films(self, refresh: bool = False) -> list[Film]:
        cache = self._read()
        now = self.clock()
        if (refresh or now - cache.get("wikidata_at", 0) > WIKIDATA_TTL or not cache.get("films")
                or cache.get("version") != CACHE_VERSION):
            self.log("Reading Wikidata's list of films on archive.org (about half a minute)...")
            cache["films"] = [asdict(f) for f in wikidata_films(self.opener)]
            cache["wikidata_at"] = now
            cache["imdb_at"] = 0
            cache["version"] = CACHE_VERSION
        films = [Film(**f) for f in cache["films"]]
        if refresh or now - cache.get("imdb_at", 0) > IMDB_TTL:
            self.log("Reading IMDb's ratings (a 9 MB download)...")
            cache["ratings"] = imdb_ratings({f.imdb for f in films}, self.opener)
            cache["imdb_at"] = now
        ratings = cache.get("ratings", {})
        for f in films:
            if f.imdb in ratings:
                f.rating, f.votes = ratings[f.imdb][0], int(ratings[f.imdb][1])
        self._write(cache)
        return films

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, cache: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(self.path)


# --- ranking and the best copy -----------------------------------------------------------------------------

# A country also means the states it was before (or split into): "Germany" finds Weimar and East German films.
COUNTRY_ALIASES = {
    "germany": {"germany", "west germany", "east germany", "weimar republic", "nazi germany", "german empire"},
    "russia": {"russia", "soviet union", "russian empire", "russian soviet federative socialist republic"},
    "italy": {"italy", "kingdom of italy"},
    "japan": {"japan", "empire of japan"},
    "china": {"people's republic of china", "republic of china", "china", "hong kong", "british hong kong"},
    "uk": {"united kingdom"}, "britain": {"united kingdom"}, "england": {"united kingdom"},
    "us": {"united states"}, "usa": {"united states"}, "america": {"united states"},
    "czech republic": {"czech republic", "czechoslovakia"}, "czechia": {"czech republic", "czechoslovakia"},
}


def country_names(wanted: str, films: list[Film]) -> set[str]:
    """The country names (as Wikidata gives them) that `wanted` means. Raises ValueError, with suggestions, for
    a country no film is from."""
    key = wanted.strip().lower()
    known = {c.lower() for f in films for c in f.countries}
    names = COUNTRY_ALIASES.get(key, {key}) & known
    if not names:
        close = difflib.get_close_matches(key, sorted(known), n=4, cutoff=0.6)
        raise ValueError(f"no film on archive.org is from {wanted!r} (per Wikidata)"
                         + (f"; did you mean {', '.join(close)}?" if close else ""))
    return names


def ranked(films: list[Film], *, min_votes: int = 5000, years: tuple[int, int] | None = None,
           any_rights: bool = False, countries: set[str] | None = None) -> list[Film]:
    """Rated films, best first (ties: more votes first); only public-domain ones unless any_rights; only from
    these countries (lowercase Wikidata names), if given."""
    keep = [f for f in films if f.rating is not None and f.votes >= min_votes and (any_rights or f.public_domain)
            and (not countries or any(c.lower() in countries for c in f.countries))
            and (years is None or (f.year is not None and years[0] <= f.year <= years[1]))]
    return sorted(keep, key=lambda f: (-f.rating, -f.votes))


def available(archive, identifiers: list[str]) -> dict[str, dict]:
    """Of these uploads, the ones archive.org still has and iafetch may download, with their reviews."""
    out = {}
    for i in range(0, len(identifiers), LOOKUP_BATCH):
        batch = identifiers[i:i + LOOKUP_BATCH]
        query = "identifier:(" + " OR ".join(f'"{x}"' for x in batch) + ") " + RESTRICTED_QUERY
        params = [("q", query), ("rows", str(len(batch))), ("output", "json")]
        params += [("fl[]", f) for f in ("identifier", "mediatype", "avg_rating", "num_reviews")]
        data = parse_json(archive.client.get(f"{ARCHIVE}/advancedsearch.php?" + urllib.parse.urlencode(params)))
        for d in (data.get("response") or {}).get("docs") or []:
            if d.get("mediatype") == "movies":
                out[d["identifier"]] = d
    return out


def assess(item, film: Film, config=None) -> Copy:
    """What iafetch would download from this upload, and whether it's plausibly the film."""
    sel = selection(HANDLERS["video"], item, {}, config)
    videos = [f for f in sel.files if not f["name"].lower().endswith((".srt", ".vtt"))]
    copy = Copy(item.identifier, parts=len(videos))
    if not videos:
        copy.doubt = "no video iafetch would take"
        return copy
    main = max(videos, key=lambda f: int(f.get("size") or 0))
    copy.width, copy.height = _int(main.get("width")) or 0, _int(main.get("height")) or 0
    copy.size = sum(int(f.get("size") or 0) for f in videos)
    seconds = sum(seconds_of(f.get("length")) for f in videos)
    copy.minutes = seconds / 60
    if seconds:
        copy.mbps = copy.size * 8 / seconds / 1e6
    copy.doubt = _doubt(item, film, copy, main["name"])
    return copy


def _doubt(item, film: Film, copy: Copy, main: str) -> str:
    """Why this upload may not be the film, or "": a running time far from the film's, another year in its
    title or file name, or a title that hardly shares a word with the film's."""
    if film.minutes and copy.minutes and not 0.8 * film.minutes <= copy.minutes <= 1.5 * film.minutes:
        return f"runs {copy.minutes:.0f} min; the film is {film.minutes:.0f}"
    if film.year:
        for text in (item.title, main):
            other = [int(y) for y in re.findall(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", text)]
            if other and all(abs(y - film.year) > 1 for y in other):
                return f"says {other[0]}; the film is {film.year}"
    words = _words(film.title)
    if words:
        seen = _words(item.title) | _words(re.sub(r"(?<=[a-z])(?=[A-Z])|[._-]", " ", item.identifier))
        if sum(1 for w in words if _near(w, seen)) * 2 < len(words):
            return f"titled {item.title!r}"
    return ""


def best_copies(films: list[Film], archive, *, top: int, config=None, log=lambda line: None) -> list[tuple[Film, list[Copy]]]:
    """The first `top` films with an upload iafetch may download, each with its uploads, best first."""
    out: list[tuple[Film, list[Copy]]] = []
    i = 0
    while len(out) < top and i < len(films):
        chunk = films[i:i + max(top * 2, 20)]
        i += len(chunk)
        live = available(archive, [u for f in chunk for u in f.uploads])
        for film in chunk:
            copies = []
            for upload in film.uploads:
                if upload not in live:
                    continue
                try:
                    item = archive.item(upload)
                except FetchError:
                    continue
                if restriction(item):
                    continue
                c = assess(item, film, config)
                c.reviews, c.stars = int(live[upload].get("num_reviews") or 0), live[upload].get("avg_rating")
                copies.append(c)
            if copies:
                out.append((film, sorted(copies, key=Copy.key)))
                log(f"  {len(out)}/{top}: {film.title}")
                if len(out) >= top:
                    break
    return out


def _near(word: str, seen: set[str]) -> bool:
    """The word, or one spelled nearly the same (Nayagan, Nayakan), is among `seen`."""
    return word in seen or any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.8 for w in seen)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and (len(w) > 2 or w.isdigit())}


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
