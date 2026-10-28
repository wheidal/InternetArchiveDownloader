# Prior art: other Internet Archive downloaders

A review of GitHub projects that do what iafetch will do, read on 2026-10-28 from shallow clones (code read, not run). It covers what each project does, what to borrow, and what to avoid. Requirements that came out of it are marked in [SPEC.md](SPEC.md) with *(prior art)*.

## The projects

| Project | Stars | Lang | What it is | Verdict |
| --- | ---: | --- | --- | --- |
| [jjjake/internetarchive](https://github.com/jjjake/internetarchive) | 1,929 | Python | **The official library and `ia` CLI** (v5.12, updated this week) | The reference. Several details to copy |
| [john-corcoran/internetarchive-downloader](https://github.com/john-corcoran/internetarchive-downloader) | 184 | Python | Bulk, resumable, hash-verified downloader built on the official library | Good verify mode and real-world bug notes. Too aggressive by default |
| [jbdemonte/synology-archive-downloader](https://github.com/jbdemonte/synology-archive-downloader) ("Archive Station") | 6 | Python | Synology DSM package: queue, schedule, verify/repair, update detection | **The most carefully engineered of the lot.** Closest to your NAS setup |
| [godzooka/grab-ia](https://github.com/godzooka/grab-ia) | 4 | Python | GUI/CLI/Docker downloader with SQLite state | Good concurrency control ideas |
| [dsync89/archive-org-bulk-downloader](https://github.com/dsync89/archive-org-bulk-downloader) | 0 | C# | Windows GUI that syncs whole collections | Correct `Retry-After` handling, but multiplies connections |
| [fedemarkco/IAD](https://github.com/fedemarkco/IAD-Internet-Archive-Downloader-) | 5 | Python | Qt GUI, "speeds that can exceed 30 MB/s" | **What not to do** |
| [brewsterkahle/internet-archive-skills](https://github.com/brewsterkahle/internet-archive-skills) | 0 | Markdown | Claude Code skill wrapping `ia`, in the Archive founder's account | Confirms the etiquette: checksums, delays, respect rate limits |
| MiniGlome/Archive.org-Downloader (1,369), elementdavv/internet_archive_downloader (1,673), jamessucla/archiveripper, debiyach/archive-org-download | n/a | various | Tools that pull **borrowed (DRM-lent) books** out of the lending library | **Out of bounds.** They get around the lending controls (spec R14) |

**No prior art exists for media-aware post-processing.** Every downloader above stops at "files in a folder named after the identifier". Searches for etree/live-music taggers, old-time-radio taggers, archive.org → Jellyfin/NFO tools, and MAME ROM-set tools found nothing relevant. deadtag's approach (understand the item, build a plan, tag and file it, stop when unsure) is what sets iafetch apart. Downstream there are general tools worth wrapping instead of rewriting: **[m4b-tool](https://github.com/sandreas/m4b-tool)** (1.6k stars, merges and chapterizes audiobooks), ffmpeg, Calibre's `ebook-meta`, and exiftool.

## Worth borrowing

### From the official library (`internetarchive`)

1. **`cnt=0` on every download.** The library adds `?cnt=0` by default so automated downloads **don't count as views** (`files.py`: "By default the library injects `cnt=0`"; `count_views=True` turns it off). Bulk tools would otherwise inflate the download counts that people, and deadfetch's own ranking, rely on. **deadfetch doesn't do this today.**
2. **Path-traversal guard.** File names in `_files.xml` are uploader-supplied. The library refuses any path that resolves outside the destination (`is_path_within_directory`). It also sanitizes names for Windows. File names are untrusted input.
3. **"On-the-fly" derivatives:** `EPUB`, `MOBI`, `DAISY` and `MARCXML` are **not listed in `_files.xml`**. They're generated on request at `<id>.epub`, `<id>.mobi`, `<id>_daisy.zip` and `<id>_archive_marc.xml`. The texts handler needs to know this. They have no checksum, and generating them costs the server work, so fetch them only when asked.
4. **Filter by `source`** (original / derivative / metadata) as well as by format and glob. This is the same idea as spec R11, and it confirms `source` is the reliable field.
5. **Retry set:** 429, 500–504, with backoff factor 1, `respect_retry_after_header=True`, and redirects not retried. **Timeout 12 s** for connects.
6. **Skip by mtime and size:** after download, the file's mtime is set from `Last-Modified`. A later run skips a file whose mtime and size match without rehashing it. A `_checksum_archive.txt` records files already verified, so a large library isn't re-hashed on every run.
7. **User-Agent:** `internetarchive/<ver> (<OS>; N; <lang>; <access key>) Python/<ver>`, plus a user suffix. (Sending the access key is their choice. Ours won't, since R17 limits stored and sent personal data.)

### From Archive Station (Synology)

1. **Strict resume validation.** On a `206`, it parses `Content-Range`, checks the start equals our offset and the total equals the size in the metadata, and **fails rather than appending if anything is off**. On a `200` to a range request, it truncates and starts over (deadfetch does this too). On a `416`, it discards the partial file and starts once from zero, because "416 alone proves neither integrity nor identity".
2. **`Accept-Encoding: identity`** on downloads, so a compressed transfer can't throw off byte offsets and sizes.
3. **Permanent vs. transient errors:** 401/403/404 are never retried. Others back off `5 × 2^n` s (max 300 s), or `Retry-After` up to 1 h, whichever is longer.
4. **Disk full (`ENOSPC`/`EDQUOT`) pauses the job** rather than failing it.
5. **Prefer SHA-1, fall back to MD5.** Count and report `private` files instead of silently dropping them.
6. **Transfer schedule:** weekday time slots with an **alternate speed limit outside them** (e.g. full speed overnight, 1 MB/s in the evening). Good for the NAS and the household network, and kinder to the Archive at peak times.
7. **Update detection ("refresh"):** re-read the item's metadata and diff it against what was downloaded. It lists **added** and **changed** files (by size and checksum) and lets you choose what to fetch. Items do change upstream: re-derives, added files, takedowns.
8. **A report per item:** a plain-text record of what was fetched, any errors, and a link back to the item page. Partial files stay in a hidden parts folder and get their final names only after verification. It also flushes each chunk so a killed process resumes from the bytes actually on disk.

### From grab-ia

1. **One global backoff shared by all workers.** When any worker gets a 429, **every** worker pauses (30–60 s random). Without this, N workers each discover the limit separately and keep hammering.
2. **Adaptive concurrency:** start at 1 worker, add one after a run of successes, drop one on any failure (additive increase, multiplicative decrease). This puts the blog's "start slowly, and ramp up" into code.
3. SQLite in WAL mode for job state, so a GUI and a CLI can share it. A metadata-only mode.

### From john-corcoran's downloader

1. **The 416 corruption bug.** Archive servers sometimes answer a resume with `416`, and with some clients the **HTML error page ends up appended to the partial file**. The author found the data had diverged *earlier* in the file too, so trimming the tail doesn't fix it; the partial has to be thrown away. (This is why Archive Station validates `Content-Range`.)
2. **`_files.xml` can't be verified against itself.** Its own listed hash often doesn't match, since it changes whenever the item does. Exclude it from checksum checks.
3. **`verify --nopaths`:** check that each expected checksum exists *somewhere* under a folder, regardless of path. This is useful for iafetch, because handlers rename and move files.
4. A cached metadata/hash file per run, so `verify` works offline later.

## To avoid

| Pattern | Seen in | Why not |
| --- | --- | --- |
| **Segmented downloads** (several ranged connections to one file) | john-corcoran `--split`, dsync89 ("K ranged connections per file") | Multiplies connections beyond the 4-connection guidance: 4 files × 4 segments = 16 |
| **High thread counts:** 200 threads for HEAD requests, 32 for downloads | IAD | The kind of load the 2023 outage post describes |
| **5 threads by default** | john-corcoran | Over the 4-at-most example on the bots page, with no ramp-up |
| **Per-worker backoff only** | john-corcoran, IAD | Other workers keep going while one is being told to slow down |
| **Credentials on the command line** (`-c email password`) | john-corcoran | Ends up in shell history and logs. Use `ia.ini` (spec R15) |
| **Lending-library extraction** | MiniGlome, archiveripper, elementdavv, debiyach | Gets around access controls. Against the terms, and possibly unlawful |

## Is this already "baked into" the model?

Partly. I knew the big projects (the official library, the `ia` CLI, the general shape of bulk downloaders) from training. Reading the current code still added things I wouldn't have produced reliably from memory:

- The exact `cnt=0` default and the on-the-fly file names (version-specific details).
- The 416 tail-corruption failure, from one author's testing notes.
- Archive Station's resume validation and update diffing. It's a two-week-old project, after anything I could have seen.
- The fact that **nobody does media-aware post-processing**, which you can only establish by searching now.

Training gives the broad patterns. The current code gives the specifics and what's new, and it's what can be cited. So it's worth repeating before each phase, especially for the handler domains (Jellyfin NFO formats, Calibre OPF, MAME ROM-set versions).

## Gaps this exposes in deadfetch

All three were **fixed in DeadShowTagger on 2026-10-28**, with tests (351 pass):

1. **`cnt=0`:** deadfetch's downloads currently count as views on the Archive.
2. **No `Content-Range` check on 206 responses:** it accepts any 206 and appends.
3. **A `416` on resume fails the file** instead of discarding the partial and starting once from zero.
