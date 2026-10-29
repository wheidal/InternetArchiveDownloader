# iafetch

Find, download, verify and file media from the [Internet Archive](https://archive.org): films, TV, music, radio, audiobooks, books and more, each into a library folder its player understands. It's deadfetch (from [DeadShowTagger](https://github.com/wheidal/DeadShowTagger)) generalized, built on the same engine, [iacore](https://github.com/wheidal/iacore).

**Status: phase 1 (the engine), and the video, texts, software, audiobook, radio and music handlers (live music still to come).** Search, show, the queue and verified downloads work. Films go to `Movies/` and TV series to `TV/`, named and with `.nfo` files the way Jellyfin expects. Books and manuals go to `Books/` as searchable PDFs with Calibre's `metadata.opf`, and magazines to `Magazines/{magazine}/{year}/`, one PDF per issue, named so they sort in order. Audiobooks go to `Audiobooks/{author}/{title}/` as LibriVox's ready-made M4B (chapters included) or the chapter MP3s, with the cover, description and narrator. Music (netlabels, 78 rpm records, album uploads) goes to `Music/{artist}/{year} - {album}/` or `Music/78rpm/{artist}/`, tagged and with its cover. Old-time radio goes to `Radio/{series}/{year}/`, one file per episode, named and (with the `audio` extra) tagged by date, number and title; choose episodes with `--episodes 1-52` or `--years 1952`. Games and other software go to `Games/{platform}/`, kept as published for emulators. Other kinds are mirrored as-is by the generic handler until their handlers are written. Grateful Dead items are handed to deadfetch. See [SPEC.md](SPEC.md) for the whole plan, [RESEARCH.md](RESEARCH.md) for archive.org's rules and layout, and [PRIOR_ART.md](PRIOR_ART.md) for what other downloaders taught us.

## Being a good citizen

iafetch keeps archive.org's [rules for automated clients](https://archive.org/developers/bots.html) and [terms of use](https://archive.org/about/terms), and they can't be switched off:

- Every request names iafetch in its User-Agent (and the agent, when an AI agent runs it).
- One request at a time, with at least a second between API calls and two seconds between downloads, under a bandwidth cap (4 MB/s by default).
- When archive.org says to slow down (429/503), every request waits as long as it asks. After 5 refusals in a row the run stops and says to write to info@archive.org rather than retry.
- Downloads don't count as views (`cnt=0`), resume instead of starting over, and are checked against archive.org's size and MD5.
- Restricted items (lending library, stream-only, print-disabled), private files and DRM-wrapped copies are never requested.
- Jobs over 500 GB or 10,000 items need `--large-job`, after writing to info@archive.org.
- Each item's license is shown, or plainly "no license declared", and recorded in its `iafetch.json`.

## Where things go

Everything iafetch writes is under one root folder, by default `/mnt/synology/InternetArchive`. Create it once by hand: iafetch never creates the root, so an unmounted NAS stops a run instead of filling a local folder. Inside it:

```
_Inbox/      downloads in progress (hidden .partial folders), and plans waiting for review
_Archive/    original downloads kept after processing
Movies/  TV/  Music/  Live Music/  Radio/  Audiobooks/  Books/  Magazines/  Comics/  Games/  Images/  Data/  Other/
```

A setting that points outside the root is refused. Grateful Dead items are queued with deadfetch, which keeps its own folders.

## Use

```sh
iafetch search --collection prelinger --year 1930-1939 --license pd
iafetch show AboutBan1935                     # metadata, license, handler, and which files it would take
iafetch queue add AboutBan1935                # a film -> Movies/About Bananas (1935)/
iafetch queue add get-smart --glob '*S01E*'    # season 1 of a series -> TV/Get Smart/Season 01/
iafetch queue add --search '"commodore 64" AND programming' --collection manuals_commodore   # how many, how big
iafetch queue add --search '"commodore 64" AND programming' --collection manuals_commodore --yes   # queue them
iafetch picks --top 25                        # the best-rated public-domain films, each with its sharpest copy
iafetch picks --year 1920-1929 --queue        # ... and queue them
iafetch picks --country France --year 1895-1930   # by country of origin
iafetch queue list
iafetch queue remove --from unwanted.txt         # many at once: one identifier a line
iafetch queue removed                         # what was taken out; queue restore <id> or --last puts it back
iafetch queue export queue.json               # save the queue; queue import queue.json (or a list of identifiers) brings it back
iafetch start                                 # download the queue in the background (iafetch get runs it here instead)
iafetch status                                # what it's doing
iafetch stop                                  # pause it (--after-current: finish the item it's on first); start resumes
iafetch process <identifier>                  # file an item after reviewing its plan
iafetch dashboard                             # a live page of the downloads: http://localhost:8643 (--lan for the home network)
iafetch tui                                   # the terminal UI: search, filter, tick and queue; reorder the queue
iafetch queue move top <identifier>           # or up, down, bottom: get follows the queue's order
```

The first `get` shows the Archive's terms notice once and asks you to acknowledge it (`iafetch terms`).

`picks` uses [Wikidata](https://www.wikidata.org) (CC0) to know which archive.org items are which film and whether each is public domain in the US, and [IMDb's ratings dataset](https://developer.imdb.com/non-commercial-datasets/) (for personal, non-commercial use) to rank them. Wikidata's copyright status is kept by volunteers: a good guide, not legal advice.

Settings are in `~/.config/iafetch/config.toml` (see [SPEC.md §7](SPEC.md#7-configuration-sketch)); the queue, metadata cache and status are in `~/.local/share/iafetch/`.

## Installing

Python 3.11 or newer, no other dependencies beyond iacore.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .            # fetches iacore from its GitHub release
.venv/bin/pip install -e ".[tui]"     # with the terminal UI (adds Textual)
.venv/bin/pip install -e ".[audio]"   # tag radio and music (adds mutagen and Pillow)
# working on iacore too: .venv/bin/pip install -e ../iacore
```

## Developing

```sh
.venv/bin/python -m unittest discover -s tests
```

The tests never touch the network or the NAS: they run against a fake archive.org in temporary folders.

## License

MIT. See [LICENSE](LICENSE).
