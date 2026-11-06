# WildFind: developer notes

How it all works, for whoever changes it next. For using the app, see
`GUIDE.txt`.

Offline, Tinder-style app for finding abandoned places in the UK. A personal
project. Runs on a Windows laptop by
double-clicking `WildFind.html`. An iPhone version is a possible later step.

## How it works

- `WildFind.html` is the whole app: one file of HTML, CSS and JavaScript,
  opened from disk (`file://`). No server, no CDN, no build step. Data comes
  in through `<script src>` tags because `fetch()` cannot read local files
  under `file://`.
- Swipe right saves a site to a list (tick off when visited, add notes); swipe
  left hides it forever. The `<` `>` arrows on the photo step through that
  site's photographs. Near (town or `lat,lon`) and distance filter at the top.
  No interest-learning or recommendations: deliberately left out.
- Only sites listed in `WF_PHOTOS` are dealt into the deck (`photosOnly()` in
  `buildDeck`); with no photos at all every site is dealt. Saved sites always
  show in Saved. Chosen 31 Oct 2026 (8,124 of 84,363 sites).
- Saves live in `localStorage` under `wildfind.v1`; Menu has JSON backup and
  restore.
- Saved has List and Map views (`S.savedView`). The map (`savedMap`) is a
  canvas on the British National Grid: coast from `data/map.js`, towns from
  `WF_PLACES` by zoom, saved sites as pins (hollow once visited), the Near point,
  grid lines and a scale bar. No tiles, so it works offline. Map view widens
  `#app` (class `wide`).
- Both maps come from `makeMap(o)`: `o.pins` draws the saved sites, `o.overlay`
  draws on top. The Near row's target button opens `picker`, a dialog whose map
  centre is the point and whose slider (1-100 mi) is the radius; it sets `S.loc`
  (named "Map: NY 415 142, near X") and `S.radius`, which `setRadiusSelect` adds
  to the distance list when it is not one of the set values. `enToLatLon`
  inverts the grid conversion by Newton's method.

## Data files (generated; formats are compact on purpose)

- `data/locations.js` `window.WF_KINDS=[...]` and
  `window.WF_LOCATIONS=[[id, name, lat, lon, kindIndex, description], ...]`.
  Ids are OSM ids prefixed `n`/`w`. Unnamed sites are kept and titled
  "Mine near X" from the nearest place.
- `data/places.js` `window.WF_PLACES=[[name, lat, lon, rank], ...]`,
  rank 0 city, 1 town, 2 village.
- `data/photos.js` `WF_AUTHORS`, `WF_LICENCES` and
  `WF_PHOTOS={siteId: [[k, authorIdx, licenceIdx], ...]}`; files at
  `photos/<last two chars of id>/<id>/<k>.jpg`.
- `data/map.js` `window.WF_MAP={unit:100,rings:[[class, [x,y,dx,dy,...]],...]}`:
  Natural Earth 1:10m coast (public domain) in National Grid hundreds of metres,
  class 0 UK, 1 Ireland, 2 the Continent (outline only). Not fetched: made once
  by `node tools/make-map.js data/map.js` (needs `npm install world-atlas@2.0.2
  topojson-client@3.1.0`) and committed.
- All written as pure ASCII (`json.dumps(ensure_ascii=True)`) so Welsh and
  Gaelic names survive any encoding the browser assumes.
- The repository holds the full site list and photo index, but not the
  photographs. `tools\FETCH DATA.bat` rebuilds the data and downloads them.

The kind order must match in three places: `KINDS` in `tools/fetch.py`,
`KINDS` in `tools/extract-osm.py`, and how the app reads `WF_KINDS`.

Kinds are decided in `extract-osm.py` (`categorise` -> `decide`): mine tags,
then railway tags, then a `historic=castle` tag, then the name, then military
tags. Names are read as whole words (Gosforth is not a fort) with the last
keyword as the head noun ("Castle Farm" is a farm, "Church Tower" a church);
Welsh and Gaelic names go by their leading word. `tests/test_kinds.py` holds
real names that were once filed wrongly; add to it when fixing another.

## The fetcher (`tools/`)

`FETCH DATA.bat` -> `fetch-data.ps1` (stops older copies, finds Python 3.9+)
-> `fetch.py` (orchestrates, holds a lock file) -> `extract-osm.py` and
`photos.py`. Progress lives in `tools/state/`; Ctrl+C and re-run resumes. If the photo
stage stops any other way (exit code 3 for a lost connection, or a crash),
fetch.py starts it again after 5 minutes, up to 144 times (12 hours), with a
fresh photos.py each time, so a fixed script is picked up mid-run.

1. **osm stage**: downloads Geofabrik's `united-kingdom-latest.osm.pbf`
   (~2.1 GB) and `isle-of-man-latest.osm.pbf`, filters them with pyosmium in
   two passes (tags first, then only the node positions the matching ways
   need, all filtering in C++), then deletes the extracts.
2. **photos stage**: downloads Commons' `geo_tags` dump (~770 MB) once,
   matches photographs to sites locally (600 m, object locations weighted
   x0.6, named sites first and matched on name), saves a shortlist and
   deletes the dump *before* any image download. Only photos whose titles name
   their site are kept (`key_words`/`is_match`: at least half the name's words,
   ignoring a town or village within 10 km; accents folded); unnamed sites get
   none, and earlier off-topic photos are moved to `photos-removed/`
   (`--any-nearby` restores the old behaviour). Titles are fetched first, 50
   per cheap `pageids`-only query; the dear `imageinfo`+`extmetadata` lookup
   (50 per request, `maxlag=5`) is made only for photos that pass the name
   check, which cut the 5-10 s lookups by about four-fifths. It downloads
   one photo at a time over a
   kept-alive connection, waiting 0.2 s plus half the response time between
   requests. Each 429/5xx raises that 0.2 s floor by x1.5 (max 5 s); every 2
   hours without one it eases back a step, never below 0.2 s. It asks for 640 px copies (mostly pre-rendered by Wikimedia, so
   about 250 ms against 600-1200 ms for on-demand 500 px renders, measured
   30 Oct 2026) and shrinks them to 500 px locally with Pillow, which
   fetch.py installs. Capped at 35,000 sites and 9.5 GB on disk, and never
   fills the disk (leaves 1 GB).

## Rules that must not be broken

- **Bulk downloads, never scraping.** Do not hammer services meant for
  humans. Use published dumps and extracts; the live API only for
  what a dump cannot give, batched, one connection, with backoff.
- Never contact Geograph's servers (their photos come via Commons).
- Do not scrape or copy 28DaysLater or Urbexology (copyright and terms;
  28DL withholds coordinates anyway).
- Never rely on unsigned executables from unofficial sites.
- Keep the user agent identifying the project with a contact address, as
  Wikimedia's policy requires.

## Windows pitfalls already hit

- Windows PowerShell 5.1 with `$ErrorActionPreference="Stop"` turns anything a
  program writes to stderr into a fatal error. The launcher uses `Continue`
  and keeps all logic in Python.
- `os.kill(pid, 0)` *terminates* a process on Windows. The single-instance
  lock uses `msvcrt.locking` / `fcntl.flock` on a file instead.
- `$args` is reserved in PowerShell. Format numbers with the invariant
  culture when passing them to Python.
- Microsoft Store Python installs packages into a per-user folder that a
  running Python cannot see if it did not exist at start-up: check imports in
  a fresh subprocess.
- Geofabrik rebuilds "latest" nightly. Resumes carry `If-Range` so a partial
  file is never finished with a newer version's bytes.
- The project path contains `&` and spaces: always quote it.

## Tests

```
python -m pip install -r tests/requirements.txt
python tests/run_all.py
```

Six files, 145 checks, all using fake local servers and temporary folders:
the extractor on a known OSM sample, how real site names are sorted into
kinds, real samples from the live dump and API
(captured 28 Oct 2026), the automatic restarts, the photo stage alone, and the full pipeline end to
end with cut-off downloads, an overnight file rebuild, rate limits and a
second copy started mid-run. Run them after any change to `tools/`.

## Photographs

`photos/` and `ATTRIBUTION.txt` are git-ignored because they are too big for
Git. If they are ever published, zip them in parts under 2 GB and attach the
zips to a GitHub release rather than committing them.

## Status (30 Oct 2026)

- First real run started 29 Oct 22:50. Stage 1 finished in about 10
  minutes: 84,363 sites (24,988 named), 16,941 places; 83,809 sites have
  photographs nearby. Commons images come from `thumb.wikimedia.org`.
- The photo stage crashed after 8 sites on a variable-name bug, since fixed.
  It then ran at about 640 sites an hour, roughly 55 hours in
  total, because most 500 px thumbnails were rendered on demand. On 30 Oct
  It was then sped up while staying within Wikimedia's policy:
  keep-alive, a pause of 0.2 s plus half the response time, and 640 px
  fetched then shrunk locally. Expected to take about a day.
- 30 Oct evening: a random sample of 12 sites judged by eye showed about half
  the nearby photos were off-topic. Name-matched photos only were chosen: 55%
  of the 24,634 photos so far were set aside, about half the named sites keep
  a real photo, and the remaining ~16,000 named sites take about 10-12 h.
- Photo stage finished 31 Oct 00:40: 8,124 sites with about 17,800 photos.
- 31 Oct: the classifier was rewritten after wrong kinds were spotted (lime
  kilns filed as churches by `kil`, Gosforth Chapel as a fort, collieries'
  railway branches as mines). The laptop's `data/locations.js` was relabelled
  by a one-off script from `sites.tsv`, which keeps no tags, so the script
  inferred them from where the old rules disagreed with the name; 1,520 sites
  changed, 652 of them in the deck. `tools/state/sites.tsv` still holds the old
  kinds. That is harmless: `write_locations` only runs after stage 1 rebuilds it.
- About 440 deck sites are old stations named only after their village, so
  their "name-matched" photos are often of the village. Not yet addressed.
- 31 Oct full check (Playwright, real data and photos, 38 checks): fixed three
  bugs only real photos showed. Dragging a card by its photo started the
  browser's own image drag and cancelled the swipe (img `draggable=false` plus
  a `dragstart` block); a tap on the photo never stepped it, because pointer
  capture retargets `pointerup` to the card; arrow keys acted on the hidden deck
  from the Saved tab.
- 31 Oct: photo credits moved off the picture to a faint line under the
  grid reference (CC BY-SA still wants one near each photo).
