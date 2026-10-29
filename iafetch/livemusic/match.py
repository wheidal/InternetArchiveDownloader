"""Pair the info .txt's track titles with the show's audio files.

Adapted from deadtag (DeadShowTagger, the same author), which keeps its own Grateful-Dead-specific copy;
this one is iafetch's, for every etree band.
"""

import re
from dataclasses import dataclass, field, replace

from .infotxt import TitleEntry
from .names import TrackLabel, natural_key, split_track_name

CLEAN = "clean"
MISMATCH = "mismatch"
NO_TITLES = "no-titles"


@dataclass
class AudioFile:
    name: str  # member name inside the zip/folder
    label: TrackLabel | None
    size: int
    length: str | None = None  # from the shntool table, or archive.org's _files.xml
    seconds: float | None = None
    archive_title: str | None = None  # from _files.xml, when there is one
    name_title: str | None = None  # written in the file name, e.g. "t02_Hell In A Bucket"

    @property
    def basename(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    @property
    def stem(self) -> str:
        return self.basename.rsplit(".", 1)[0]


@dataclass
class Track:
    """One output track: a file, its title, and where it sits in the show."""

    file: AudioFile
    title: TitleEntry
    index: int  # 1-based across the whole show
    disc: int
    disc_track: int
    set_name: str | None


@dataclass
class Group:
    """Files and titles that should pair up: one disc, or one set for shows tracked by set."""

    number: int
    files: list[AudioFile]
    titles: list[TitleEntry]

    @property
    def balanced(self) -> bool:
        return len(self.files) == len(self.titles)

    @property
    def name(self) -> str:
        kinds = {f.label.kind for f in self.files if f.label}
        return f"{'Set' if kinds == {'s'} else 'Disc'} {self.number}"

    def rows(self) -> list[tuple["AudioFile | None", TitleEntry | None]]:
        """Files beside titles for review: lined up by label when both sides have one, else by position."""
        if all(f.label and f.label.kind for f in self.files) and all(t.label for t in self.titles):
            files = {f.label.track: f for f in self.files}
            titles = {t.label.track: t for t in self.titles}
            return [(files.get(n), titles.get(n)) for n in sorted(files.keys() | titles.keys())]
        n = max(len(self.files), len(self.titles))
        return [(self.files[i] if i < len(self.files) else None,
                 self.titles[i] if i < len(self.titles) else None) for i in range(n)]


@dataclass
class MatchResult:
    status: str
    tracks: list[Track] = field(default_factory=list)  # filled only when status is clean
    groups: list[Group] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)  # fixes made while pairing (a song split at a disc break)
    unchecked: set[int] = field(default_factory=set)  # ids of files whose archive.org titles are known to be off
    split: set[int] = field(default_factory=set)  # ids of files found to start a song split at a disc break

    @property
    def clean(self) -> bool:
        return self.status == CLEAN


def build_audio_files(names_and_sizes: list[tuple[str, int]]) -> list[AudioFile]:
    files = []
    for name, size in names_and_sizes:
        stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        label, title = split_track_name(stem)
        files.append(AudioFile(name, label, size, name_title=title))
    if all(f.label for f in files):
        files.sort(key=lambda f: (f.label.group, f.label.track, natural_key(f.name)))
    else:
        files.sort(key=lambda f: natural_key(f.name))
    return files


def match_titles(files: list[AudioFile], titles: list[TitleEntry]) -> MatchResult:
    if not titles:
        return MatchResult(NO_TITLES, groups=[Group(1, files, [])],
                           notes=["the info .txt has no track titles"])

    file_groups = _group_files(files)
    title_groups = _group_titles(titles)
    result = MatchResult(MISMATCH)

    if len(file_groups) == len(title_groups):
        result.groups = [Group(n, fg, tg) for n, (fg, tg) in
                         enumerate(zip(file_groups, title_groups), start=1)]
        if not all(g.balanced for g in result.groups):
            _split_at_disc_break(result)
        if all(g.balanced for g in result.groups):
            result.status = CLEAN
            _note_label_scheme(files, titles, result)
    elif len(files) == len(titles):
        # e.g. the .txt lists Set 1 / Set 2 / Encore while files are split by disc
        result.groups = [Group(1, files, titles)]
        result.status = CLEAN
        result.notes.append(f"the .txt has {_groups(len(title_groups))} and the files have "
                            f"{_groups(len(file_groups))}; paired in order across the whole show")
    else:
        result.groups = [Group(1, files, titles)]

    if result.status == CLEAN:
        conflicts = _label_conflicts([f for f in files if id(f) not in result.split], titles)
        if conflicts:
            result.status = MISMATCH
            result.notes.extend(conflicts)

    if result.status == CLEAN:
        result.tracks = _tracks(result.groups, file_groups)
    else:
        for n, g in enumerate(result.groups):
            if not g.balanced:
                diff = len(g.titles) - len(g.files)
                more = "title" if diff > 0 else "file"
                note = (f"{g.name}: {abs(diff)} more {more}{'s' if abs(diff) > 1 else ''} "
                        f"than {'files' if diff > 0 else 'titles'} ({len(g.titles)} titles, {len(g.files)} files)")
                nxt = result.groups[n + 1] if n + 1 < len(result.groups) else None
                if diff == -1 and nxt and nxt.balanced and nxt.titles:
                    last, song = g.files[-1], nxt.titles[0].title
                    note += (f"; its last file, {last.basename}{_length(last)}, may be the start of {song!r} split at "
                             f"the disc break: if so, title it \"{song} (start)\"")
                result.notes.append(note)
    return result


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def _length(f: AudioFile) -> str:
    return f" ({f.length.split('.')[0]})" if f.length else ""


def _split_at_disc_break(result: MatchResult) -> None:
    """A song cut in two where a disc ran out: the disc has one file more than titles, the next disc pairs
    cleanly, and archive.org (or the file name) calls the disc's last file by the next disc's first title.
    Title the two halves "<song> (start)" and "<song> (end)". archive.org numbers the split song once, so
    its titles on the next disc run one place behind; when they do, they aren't held against the pairing."""
    for g, nxt in zip(result.groups, result.groups[1:]):
        if len(g.files) != len(g.titles) + 1 or not nxt.balanced or not nxt.titles:
            continue
        last, first = g.files[-1], nxt.titles[0]
        said = last.archive_title or last.name_title
        if not said or _key(said) != _key(first.title):
            continue
        g.titles.append(replace(first, title=f"{first.title} (start)", segue=False, label=None, notes=[]))
        nxt.titles[0] = replace(first, title=f"{first.title} (end)")
        result.split.add(id(last))  # the .txt has no label for it, so the label check leaves it out
        result.resolved.append(f"{last.basename}{_length(last)} is the start of {first.title!r}, split at the disc "
                               f"break (archive.org agrees): titled \"{first.title} (start)\", and "
                               f"{nxt.files[0].basename} \"{first.title} (end)\"")
        behind = [f.archive_title for f in nxt.files[:-1]]
        ahead = [t.title for t in nxt.titles[1:]]
        if behind and all(a and _key(a) == _key(t) for a, t in zip(behind, ahead)):
            result.unchecked |= {id(f) for f in nxt.files}
            result.resolved.append(f"archive.org's titles for {nxt.name} are one place behind (it counts the split "
                                   "song once), so they weren't used to check it")


def _label_conflicts(files: list[AudioFile], titles: list[TitleEntry]) -> list[str]:
    """Counts can balance while labels disagree: one dropped line shifts every title after it.

    When the .txt numbers its titles the same way the files are numbered, every title must
    point at a file that exists, and every file must have a title pointing at it.
    """
    if not files or not all(f.label for f in files):
        return []
    if all(t.label for t in titles) and all(f.label.kind for f in files):
        have = {(f.label.group, f.label.track): f for f in files}
        want = {(t.label.group, t.label.track): t for t in titles}
    elif all(t.number for t in titles) and not any(f.label.kind for f in files):
        numbers = [t.number for t in titles]
        if len(set(numbers)) != len(numbers):
            return []  # numbering restarts in each set, so it can't be compared to file numbers
        have = {(1, f.label.track): f for f in files}
        want = {(1, t.number): t for t in titles}
    else:
        return []

    problems = []
    for key in sorted(want.keys() - have.keys()):
        t = want[key]
        problems.append(f"the .txt lists {t.label or t.number} ({t.title}) but no file has that number")
    for key in sorted(have.keys() - want.keys()):
        f = have[key]
        problems.append(f"no .txt title is numbered for {f.basename}")
    return problems


def _groups(n: int) -> str:
    return f"{n} group{'s' if n != 1 else ''}"


def _group_files(files: list[AudioFile]) -> list[list[AudioFile]]:
    groups: dict[int, list[AudioFile]] = {}
    for f in files:
        groups.setdefault(f.label.group if f.label else 1, []).append(f)
    return [groups[k] for k in sorted(groups)]


def _group_titles(titles: list[TitleEntry]) -> list[list[TitleEntry]]:
    groups: dict[int, list[TitleEntry]] = {}
    for t in titles:
        groups.setdefault(t.group, []).append(t)
    return list(groups.values())  # in the order the .txt lists them


def _tracks(groups: list[Group], file_groups: list[list[AudioFile]]) -> list[Track]:
    disc_of = {id(f): n for n, fg in enumerate(file_groups, start=1) for f in fg}
    tracks = []
    per_disc: dict[int, int] = {}
    for g in groups:
        for f, t in zip(g.files, g.titles):
            disc = disc_of[id(f)]
            per_disc[disc] = per_disc.get(disc, 0) + 1
            set_name = t.set_name or (f"Set {f.label.group}" if f.label and f.label.kind == "s" else None)
            tracks.append(Track(f, t, len(tracks) + 1, disc, per_disc[disc], set_name))
    return tracks


def _note_label_scheme(files: list[AudioFile], titles: list[TitleEntry], result: MatchResult) -> None:
    file_kinds = {f.label.kind for f in files if f.label}
    title_kinds = {t.label.kind for t in titles if t.label}
    if file_kinds and title_kinds and file_kinds != title_kinds:
        describe = {"d": "disc", "s": "set", "": "track"}
        result.notes.append(
            f"the .txt labels tracks by {'/'.join(describe[k] for k in sorted(title_kinds))} "
            f"but the files are labeled by {'/'.join(describe[k] for k in sorted(file_kinds))}; "
            "paired by group and position")
