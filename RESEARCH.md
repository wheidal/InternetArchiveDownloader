# Internet Archive: rules and media survey

Research behind [SPEC.md](SPEC.md). Gathered 2026-10-28 from archive.org's own pages and live API queries. Counts are from `advancedsearch.php` on that date.

## 1. The rules

### Terms of Use (archive.org/about/terms)

The live page is now rendered by JavaScript. The text below is from the Wayback copy of 2023-06-01, which is the current wording as far as can be checked. The clauses that bind a downloader:

| Clause (quoted) | What it means for the tool |
| --- | --- |
| "Access to the Archive's Collections is provided at no cost to you and is granted for scholarship and research purposes only." | Personal and research use. The tool is not built for redistribution or resale. |
| "You agree not to interfere with the work of other users or Archive personnel, servers, or resources." | This is the load clause: rate limits, concurrency caps and backing off are obligations, not courtesies. |
| "You certify that your use of any part of the Archive's Collections will be limited to noninfringing or fair use under copyright law." | The user carries copyright risk. The tool should show each item's license or rights and keep a record of it. |
| "If a Creative Commons or other license has been declared … to the extent you trust the declaration and declarer (which is rarely the Internet Archive), you may use the content according to … the applicable license." | `licenseurl` is the uploader's claim, not a guarantee. Show it, but don't present it as certain. |
| "(c) not to use or attempt to use another person's password, (d) not to collect or store personal data about anyone" | Only the user's own credentials. Don't harvest reviewer names or uploader emails. |
| "Some of the content … may be governed by local, national, and/or international laws … your use of such content is solely at your own risk." | Say so in the README and on first run. |
| "If the author or publisher … does not want his or her work in our Collections … we may remove that portion … without notice." | Items disappear. A dark item must be handled cleanly, not retried forever. |
| "The Archive may immediately terminate this Agreement … and bar you from accessing the Collections." | Getting blocked is a real outcome. The tool must stop when it's refused, not push on. |
| "If you use the Archive's Collections for any research that results in an article, a book … list the Archive as a resource." | Keep the source URL with each item, so it can be cited. |

The rights help page (help.archive.org/help/rights) adds: the Archive "does not make guarantees as to the copyright status of items"; much material is "non commercial use" and "with attribution"; and fair use still applies.

### Bots and automated access (archive.org/developers/bots.html)

This page sets the operational rules for tools like ours:

- **"All automated requests to archive.org must include a descriptive User-Agent header"**: the tool name and version, and for AI agents, the model.
- Add delays between requests in bulk operations.
- **Honor `429 Too Many Requests` and `Retry-After`.**
- Skip unchanged files by checksum (`ia download --checksum`).
- Limit concurrency. Their example: `parallel -j4 --delay 1 'ia download {}'`, i.e. **4 concurrent downloads at most, started 1 s apart**.
- Authenticate when possible. Cache responses. Prefer bulk endpoints over many single requests. Retry with exponential backoff.

### "Let us serve you, but don't bring us down" (blog, 2023-05-29)

Written after 64 AWS hosts sending tens of thousands of requests per second took the site down twice:

- "Those wanting to use our materials in bulk should **start slowly, and ramp up**."
- "If you are starting a large project please **contact us at info@archive.org**."
- "If you find yourself blocked, please **don't just start again, reach out**."

### robots.txt

`User-agent: *`. It disallows `/control/` and `/report/` and gives a sitemap. Download and API paths are not disallowed.

### API limits found by testing

- **`advancedsearch.php` refuses deep paging past 10,000 results** (`[DEEP_PAGING] … use the Scraping API`).
- **The scrape API** (`/services/search/v1/scrape`) pages with a cursor, needs `count` ≥ 100 (max 10,000), and returns `total`.
- **The Wayback availability API returned a 429** after about ten quick requests during this research. The limits are real and tighter on some services.
- **`/metadata/<id>`** lists every file with `source` (original or derivative), `format`, `size`, `md5`, `sha1` and `private`. Files that are only available to borrow are marked `"private": "true"`.
- **Torrents:** most items have `<id>_archive.torrent` (served with a 302 redirect to a datanode), and some also have uploader torrents. They're useful for very large items.

### What deadfetch already does (and the new tool keeps)

`deadfetch/download.py` and `catalog.py` in DeadShowTagger already do all of this: a descriptive User-Agent (`deadfetch/x.y (+github URL; personal collection)`), one file at a time, a token-bucket bandwidth cap (4 MB/s default), a 2 s pause between files and 1 s between search pages, resuming with `Range`, MD5 and size checks against `_files.xml`, and backing off on 429/503 using `Retry-After`. It also skips `stream_only` and `access-restricted-item:true`. The new tool generalizes that code; it doesn't replace it.

## 2. What's on the Archive

### By media type

| mediatype | Items | Notes |
| --- | ---: | --- |
| texts | 52.9 M | Scanned books, Gutenberg, magazines, comics, community uploads |
| web | 24.4 M | Web crawl WARCs. These are the Wayback Machine's raw material, not media |
| movies | 17.5 M | Films, TV, Prelinger, NASA, community video |
| audio | 14.2 M | 78s, netlabels, old-time radio, LibriVox, podcasts, community audio |
| data | 7.5 M | Datasets: CSV, TSV, JSON, dumps |
| image | 5.8 M | Photos, scans, art |
| collection | 3.8 M | Collections themselves (folders, not media) |
| software | 1.5 M | DOS, console, arcade and other software, many with an in-browser emulator |
| etree | 0.3 M | The Live Music Archive, the home of the Grateful Dead collection |
| **all** | **128.3 M** | |

### What can be downloaded

| Query | Items | Meaning |
| --- | ---: | --- |
| `access-restricted-item:true` | 38.7 M (30%) | Borrow-only or stream-only. **Out of bounds for the tool** |
| `collection:inlibrary` | 4.4 M | Lending library books (DRM: LCP/ACS-encrypted PDF and EPUB) |
| `collection:printdisabled` | 5.5 M | For print-disabled patrons only |
| `collection:stream_only` | 4.1 M | Playable on the site, not downloadable (most GD soundboards) |
| `licenseurl:*creativecommons*` | 15.1 M | Has a declared CC license |
| `licenseurl:*publicdomain*` | 4.7 M | Declared public domain |

Most items have **no `licenseurl` at all**. "Downloadable" doesn't mean "free to reuse". For example, `get-smart` in Classic TV is a 46 GB user upload of a copyrighted series with no license declared.

### The major top-level collections

| Collection | Items | Type |
| --- | ---: | --- |
| `americana` (American Libraries) | 4.40 M | Scanned books |
| `inlibrary` (Texts to Borrow) | 4.43 M | Lending library: **not downloadable** |
| `tvarchive` (TV News) | 4.65 M | Mostly restricted, streaming |
| `opensource_audio` (Community Audio) | 3.06 M | Anything |
| `opensource_movies` (Community Video) | 2.76 M | Anything |
| `opensource` (Community Texts) | 1.38 M | Anything |
| `audio_music` (Music, Arts & Culture) | 553 K | Music |
| `78rpm` / `georgeblood` | 334 K / 187 K | 78 rpm discs, 24-bit FLAC transfers |
| `etree` (Live Music Archive) | 306 K | Live concerts: FLAC/SHN with info .txt |
| `softwarelibrary` | 278 K | Emulated software |
| `nasa` | 209 K | Images, video, audio |
| `netlabels` | 81 K | CC-licensed albums from net labels |
| `gutenberg` | 59 K | Project Gutenberg texts |
| `feature_films` | 29 K | Public-domain features |
| `consolelivingroom` | 25 K | Console ROMs |
| `classic_tv` | 11 K | Old TV series |
| `prelinger` | 10 K | Ephemeral films: public domain, high quality |
| `oldtimeradio` | 9 K | Radio series, usually one item per series |
| `softwarelibrary_msdos_games` | 8.9 K | DOS games |
| `internetarcade` | 2.7 K | MAME arcade ROM sets |

Web crawls (`web`, `archiveteam`, `commoncrawl`, …) rank highest by download volume but are WARC files for the Wayback Machine. They aren't media and are out of scope.

## 3. What an item looks like, by kind

Sampled from the most-downloaded unrestricted items in each collection. Every item also has `_meta.xml`, `_files.xml`, `__ia_thumb.jpg` (the "Item Tile") and usually an `_archive.torrent`. Derivatives are files the Archive generates from the originals.

| Kind | Example item | Originals | Useful derivatives | Generated derivatives (skip) | Metadata worth having |
| --- | --- | --- | --- | --- | --- |
| Live music (etree) | `oar2006-01-14.mix.flac16` | FLAC or SHN tracks, info `.txt`, `.md5`/`.ffp` | VBR MP3, Ogg | PNG/Spectrogram, Columbia Peaks, Essentia | `creator`, `date`, `venue`, `coverage`, `source`, `lineage`, `taper`, `transferer` |
| 78 rpm | `78_house-of-the-rising-sun_…` | **24-bit FLAC** (restored), a ZIP of raw transfers (600 MB), a **TIFF label scan** | VBR MP3, ASR subtitles | Spectrogram, Peaks | `creator`, `title`, `publisher`, `publisher-catalog-number`, `date`, `genre` |
| Netlabel album | `NS050` | MP3 tracks (sometimes FLAC), a ZIP of the album, cover JPEG | Ogg | Spectrogram, Essentia | `creator`, `title`, `date`, `licenseurl` (CC) |
| Old-time radio | `OTRR_Gunsmoke_Singles` | **480+ episode MP3s in one item**, logs, PDF | Ogg, SubRip | Spectrogram, Peaks | Series-level only; episodes parsed from file names |
| Audiobook (LibriVox) | `art_of_war_librivox` | Chapter MP3s, **an `.m4b` audiobook** (64 kbps), a ZIP of the MP3s, a JSON | 64 kbps MP3, Ogg | OCR of the PDF | `creator` (author), `title`, `runtime`, `language` |
| Feature film | `sex_madness` | MPEG2 (2.8 GB) | **h.264 MP4**, 512Kb MPEG4, Ogg, SubRip | Thumbnails (dozens), Animated GIF | `title`, `date`/`year`, `director`, `runtime`, `color`, `sound` |
| TV series | `get-smart` | **138 episode MP4s in one item**, `SxxEyy (Title)` file names, a torrent | `h.264 IA` MP4 (smaller) | Thumbnails | Series-level; episodes from file names |
| Ephemeral film | `AboutBan1935` (Prelinger) | MPEG2, Cinepack, SubRip | h.264, MPEG4, Ogg, MP3, WebVTT, Whisper ASR | Thumbnails | `shotlist`, `sponsor`, `licenseurl` (PD) |
| Scanned book | `meditationsofmar00marc` | JP2 page images (tar/zip), scandata, MARC | **Text PDF**, **EPUB**, DjVu, DjVuTXT (OCR) | hOCR, chOCR, logs | `creator`, `title`, `date`, `publisher`, `language`, `isbn`, `oclc-id`, `openlibrary_*` |
| Gutenberg | `alicesadventures19002gut` | `.txt`, `.html` + illustrations, ZIPs | none | none | `creator`, `title`, `language` |
| Magazine run | `DragonMagazine260_201801` | **443 PDFs in one item** (141 GB) | EPUB per issue | OCR outputs | Run-level; issues from file names |
| Comics | `invincible-compendiums` | **CBZ/CBR** | PDF, EPUB | OCR | `creator`, `title` |
| DOS game | `Doom-2` | One ZIP, screenshots | none | none | `emulator`, `emulator_start`, `mobygames_*`, `year` |
| Arcade | `arcade_invaders` | MAME ROM set ZIP(s) | none | none | `emulator`, `emulator_ext` (the MAME driver), `mobygames_*` |
| Console | `scheroes_bin` | ROM ZIP/BIN, screenshots | none | none | `emulator`, platform via collection |
| Image | `MichaelChan_…` | JPEG, PNG, TIFF | JPEG thumb | none | `creator`, `date`, `description`, `subject` |
| Dataset | `third-eye` | 6,300 TSVs (54 GB) | none | none | `description` only |
| Lending book | `ihavenomouthimus0000harl` | everything `private: true` | **LCP/ACS-encrypted PDF/EPUB only** | none | Out of scope: DRM |

### Patterns that shape the design

1. **An item isn't always one "thing".** It might be one album or film (etree, netlabel, 78, film), or a whole series (OTR, TV, magazine runs, NASA reels). The tool has to work below item level, selecting episodes or issues from inside an item.
2. **Originals vs. derivatives.** By default, take the originals and skip the generated files: spectrograms, peaks, Essentia, thumbnails, OCR intermediates, logs. That cuts load on the Archive and clutter on disk. For video and books, a derivative (h.264 MP4, Text PDF, EPUB) is often what the user actually wants, so selection is per kind.
3. **Same content, several sizes.** A ZIP of the whole item, the per-file originals and a torrent are three routes to the same bytes. Pick one; never fetch the ZIP and the files.
4. **Metadata quality varies by collection.** Curated collections (etree, georgeblood, Prelinger, LibriVox, Internet Arcade) have rich, consistent fields. Community collections are freeform. Post-processing has to degrade gracefully.
5. **Checksums are everywhere.** `_files.xml` carries MD5, SHA-1 and size for every file, so every download can be verified.
