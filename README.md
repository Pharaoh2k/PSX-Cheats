# PSX Cheats

Trainer files and a shared PlayStation cover snapshot for Phoenix Trainers.

- `ShnExt` contains the original ShnExt trainer files. File contents are preserved.
- `Covers` contains JPEG and PNG artwork named by title ID, including titles from this collection and HEN Cheats Collection. Accepted prefixes are CUSA, PPSA, SLUS, SLES, SLPM, SCUS, SLPS and SCES; filenames omit the serial's hyphen.
- `metadata.json`, when present, supplies verified presentation headers keyed by source and relative filename. Each record is tied to the trainer's SHA256.

The cover collection is a snapshot, not a regularly refreshed service. Missing covers must remain eligible for the application's cover lookup: Sony for PS4/PS5, and serial-based collections for PS1/PS2. Some supplied trainer title IDs have no exact matching cover filename, so a repository miss must never disable those fallbacks. Artwork aliases keep the original trainer ID as the filename; they do not change trainer identity or establish console compatibility.

The October 29, 2026 snapshot adds 131 PS1/PS2 covers and three verified Sony concept covers for PPSA16387, PPSA16388 and PPSA28601, bringing the collection to 2,039 images. Legacy artwork comes from [xlenore's PS1 covers](https://github.com/xlenore/psx-covers), [PS2 covers](https://github.com/xlenore/ps2-covers) and [Libretro thumbnails](https://github.com/libretro-thumbnails). These collections retain their respective artwork credits; this repository claims no ownership of the artwork.

## Library bundles

Releases contain one trainer pack for this collection, one for the upstream [HEN Cheats Collection](https://github.com/TeeKay87/HEN-Cheats-Collection), sixteen stable cover packs, a catalogue and a manifest. Empty cover packs are omitted. Trainer payloads remain byte-identical. Exact duplicate trainer bytes merge only when the filename-derived title ID and version also match. The original source files remain in this repository.

Covers in release packs are limited to 300 pixels on their longest edge. Larger images are resized for library browsing. Opaque covers use optimized JPEG at quality 88; images with actual transparency retain PNG. Source artwork is retained unchanged. Entries are accessed individually, so applications do not need to extract every file or decode every image at startup.

The manifest records source commits and each asset's SHA256 and length. The catalogue maps logical filenames to pack entries. Pack and catalogue assets have immutable SHA256 filenames. The release tag identifies catalogue content, so a compression-only toolchain difference does not create another release. Identical content can retain an earlier valid source commit in its published provenance.

Known unsupported payloads are omitted from release packs only when their bytes match a SHA256-bound `supported:false` metadata record. The original source files stay intact. Every omission and reason appears in the release's `build-report.json`, together with merged filename aliases and missing cover IDs. Changed bytes do not inherit an old exclusion. The initial snapshot identified 13 unsupported SHNX version-1 containers and 21 HEN JSON files rejected by the application's section-offset validation.

Stable manifest URL:

https://github.com/Pharaoh2k/PSX-Cheats/releases/latest/download/manifest.json

Clients must derive all asset URLs from that manifest's `releaseTag`, not repeatedly use `latest` while downloading a generation. Verify hashes and lengths before activation. Keep the old generation available until the replacement is complete. User-authored files must remain independent of generated packs.

## Build and publish

Python 3.9 or newer, Pillow and Git are needed to build. Publishing additionally uses an authenticated GitHub CLI with write access to this repository.

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tools/build_bundles.py --output dist
python tools/publish_bundles.py --output dist
```

Building downloads a commit-pinned upstream HEN archive and verifies each selected trainer against its Git tree size and blob hash. A cached `commit.json`, `tree.json` and `archive.zip` directory can be supplied with `--hen-input` for offline builds. Build reports list merged aliases and missing cover IDs. This publisher checks transport structure and metadata; trainer execution remains the application's responsibility.

The workflow runs after relevant pushes, on manual request, and every six hours at 01:23, 07:23, 13:23 and 19:23 Asia/Bangkok (UTC cron). Scheduled execution can be delayed by GitHub. It reads current HEN data but never fetches new Sony artwork. Unchanged catalogue content skips publication. A new release stays a draft until all required assets are uploaded, then becomes the latest published generation. An interrupted draft can resume with the same build output; existing assets are verified and never overwritten.

Only data and packaging automation belong here. App source, credentials, private keys and local machine reports are not part of this repository. Trainer authors retain their credits in filenames and metadata; artwork comes from the PlayStation catalogue and the legacy cover collections linked above.
