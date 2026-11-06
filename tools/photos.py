#!/usr/bin/env python3
"""
WildFind - photographs for the sites, fetched politely.

    python photos.py <state_dir> <project_root> --dump <geo_tags.sql.gz> [options]
    python photos.py <state_dir> <project_root> --needs-dump

The dump is only read once. Each site's shortlist of nearby photographs is
saved to state/candidates.tsv, a few megabytes, and the 770 MB dump can be
deleted before a single image is downloaded. A resumed run reads the
shortlist and never needs the dump again.

How it avoids hammering anything
--------------------------------
Finding photographs near 35,000 places could be done with 35,000 spatial
searches against the Commons API. That is the wrong way: Wikimedia's robot
policy says bulk data should come from dumps, not the API.

So the locations of every geotagged file on Commons come from one download,
the geo_tags dump (about 770 MB), and the matching happens here on your
machine. The API is then only used for what the dump cannot give - each
file's name, image address and licence - and those lookups go fifty files at
a time, single file, with a pause that grows if the servers are slow.

The images themselves come one at a time from Wikimedia's image servers,
which is how every Wikipedia page loads its pictures. Again a single
connection with a pause between requests, as the robot policy asks, and at
a standard thumbnail width that is already cached rather than rendered fresh.

Geograph's own site is never touched: its photos reach Commons through its
bulk upload, and Geograph asks bulk users to stay off its live servers.

Everything is resumable. Stop it with Ctrl+C and run it again.
"""

import sys, os, io, gzip, json, math, time, re, argparse, random, shutil
import html as htmllib
import unicodedata
from array import array
try:
    from PIL import Image, ImageOps     # Pillow, installed by fetch.py
except ImportError:
    Image = None
import urllib.request, urllib.error, urllib.parse, http.client

UA = "WildFind/2.1 (personal offline urbex planner; https://github.com/AtlasDelta7)"
API = "https://commons.wikimedia.org/w/api.php"

# Great Britain, Northern Ireland and the Isle of Man, with a little margin.
UK = (49.7, -8.8, 61.0, 2.1)

# 0.01 degree cells: at least 640 m across even in Shetland, so a site plus its
# eight neighbouring cells always covers the whole search radius.
CELL = 100.0

def cell(lat, lon):
    return (int(math.floor(lat * CELL)), int(math.floor(lon * CELL)))

def metres(la1, lo1, la2, lo2):
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    y = math.radians(la2 - la1)
    return 6371000.0 * math.hypot(x, y)

def say(msg):
    print(msg, flush=True)

# ---------------------------------------------------------------- inputs

def load_sites(state_dir):
    """Named sites first: their photos can be matched by name as well as
    position, so they get the best pictures before any size limit bites."""
    path = os.path.join(state_dir, "sites.tsv")
    if not os.path.exists(path):
        sys.exit("No sites.tsv yet. Run the osm stage first.")
    seen, named, plain = set(), [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or p[0] in seen:
                continue
            try:
                la, lo = float(p[2]), float(p[3])
            except ValueError:
                continue
            seen.add(p[0])
            (named if p[1] else plain).append((p[0], p[1], la, lo))
    return named + plain

def wanted_cells(sites):
    cells = set()
    for _, _, la, lo in sites:
        cy, cx = cell(la, lo)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                cells.add((cy + dy, cx + dx))
    return cells

# ---------------------------------------------------------------- the dump

def parse_geo_tags(dump_path, cells):
    """Stream the dump and keep only coordinates that fall near a site.
    Fields used: page id, globe, lat, lon, type - all of which come before the
    free-text name column, so splitting on the first commas is safe."""
    grid = {}
    kept = scanned = 0
    t0 = time.time()
    s, w, n, e = UK
    opener = gzip.open if dump_path.endswith(".gz") else open
    with opener(dump_path, "rt", encoding="utf-8", errors="replace") as f:
        for raw in f:
            if not raw.startswith("(") and "VALUES" not in raw:
                continue
            # newer dumps put one row per line; older ones pack many per line
            chunks = raw.split("),(") if "),(" in raw else [raw]
            for row in chunks:
                row = row.strip()
                if row.startswith("INSERT"):
                    i = row.find("(")
                    if i < 0:
                        continue
                    row = row[i:]
                row = row.lstrip("(")
                parts = row.split(",", 8)
                if len(parts) < 8:
                    continue
                scanned += 1
                if parts[2] != "'earth'":
                    continue
                try:
                    la = float(parts[4]); lo = float(parts[5])
                except ValueError:
                    continue
                if not (s <= la <= n and w <= lo <= e):
                    continue
                c = cell(la, lo)
                if c not in cells:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                # four doubles per photo in a flat array: around a fifth of the
                # memory of a Python tuple, which matters with millions kept
                obj = 1.0 if parts[7] == "'object'" else 0.0
                a = grid.get(c)
                if a is None:
                    a = grid[c] = array("d")
                a.extend((pid, la, lo, obj))
                kept += 1
            if scanned and scanned % 5_000_000 < len(chunks):
                say("    %dM rows read, %d near a site, %.0fs" % (scanned // 1_000_000, kept, time.time() - t0))
    say("  dump read: %d rows, %d near a site, %.0fs" % (scanned, kept, time.time() - t0))
    return grid

def candidates(grid, la, lo, radius, limit):
    """Nearest files first. A coordinate marking what was photographed
    (object) counts as nearer than where the photographer stood (camera)."""
    cy, cx = cell(la, lo)
    found = {}
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            a = grid.get((cy + dy, cx + dx))
            if not a:
                continue
            for j in range(0, len(a), 4):
                pid, pla, plo, obj = int(a[j]), a[j + 1], a[j + 2], a[j + 3]
                d = metres(la, lo, pla, plo)
                if d > radius:
                    continue
                if obj:
                    d *= 0.6
                if pid not in found or d < found[pid]:
                    found[pid] = d
    return [pid for pid, _ in sorted(found.items(), key=lambda kv: kv[1])[:limit]]

# ---------------------------------------------------------------- polite http

def plain_text(v):
    """Commons credits arrive as HTML. Some carry a hidden copy of the same
    text for screen readers ("Unknown author<span style="display: none;">
    Unknown author</span>"), so hidden elements go first, then tags, then
    entities like &amp;."""
    v = re.sub(r"<(\w+)[^>]*display\s*:\s*none[^>]*>.*?</\1\s*>", "", v or "", flags=re.S | re.I)
    v = re.sub(r"<[^>]+>", "", v)
    v = htmllib.unescape(v)
    return re.sub(r"\s+", " ", v).strip()

def shrink(data, width):
    """Resize a downloaded JPEG to `width` pixels wide on this machine.

    Asking Wikimedia for 640 px copies is faster and spares its servers the
    resizing, because most 640 px copies already exist (and many originals
    are 640 px to begin with). Shrinking to 500 px here keeps the photo
    folder inside its size limit. Anything Pillow cannot read, or anything
    already narrow enough, is kept exactly as downloaded."""
    if Image is None:
        return data
    try:
        im = Image.open(io.BytesIO(data))
        if im.width <= width:
            return data
        im = ImageOps.exif_transpose(im).convert("RGB")
        h = max(1, round(im.height * width / im.width))
        im = im.resize((width, h), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue()
    except Exception:
        return data

class NetworkDown(Exception):
    """The connection itself is gone, as opposed to the server being busy."""

class ShortRead(Exception):
    pass

def _via_proxy(parts):
    """True when the system says to reach this host through a proxy. Then the
    standard library's urlopen is used, which knows how; otherwise a direct
    connection is kept open between requests."""
    try:
        p = urllib.request.getproxies().get(parts.scheme)
        return bool(p) and not urllib.request.proxy_bypass(parts.hostname or "")
    except Exception:
        return False

class Polite:
    """One request at a time, never in parallel.

    - Keeps its connection open between requests (HTTP keep-alive) rather
      than setting up a new encrypted connection for every photo: less work
      for Wikimedia's servers as well as for this machine.
    - Waits `floor` seconds plus half as long as the last answer took before
      asking again, so it slows down whenever the servers slow down, as
      Wikimedia's robot policy asks.
    - Backs off on 429 and 5xx answers and honours Retry-After, raising its
      floor by half each time (up to 5 s). After two hours with no such
      answer the floor comes down one step, and so on back to where it
      started; any new warning puts it up again and restarts the clock."""
    STEP = 1.5
    def __init__(self, floor, base_api=API, relax_after=2 * 3600):
        self.floor = floor
        self.base_floor = floor
        self.relax_after = relax_after
        self.calm_since = time.time()
        self.api = base_api
        self.last = 0.0
        self.pause = floor
        self.requests = 0
        self.conn_fail = 0          # consecutive failures to reach the server at all
        self.conns = {}             # (scheme, host, port) -> open connection

    def _warned(self):
        self.floor = min(5.0, self.floor * self.STEP)
        self.calm_since = time.time()

    def _relax(self):
        """One step back towards the starting floor per calm spell."""
        if self.floor > self.base_floor and time.time() - self.calm_since >= self.relax_after:
            eased = self.floor / self.STEP
            self.floor = self.base_floor if eased < self.base_floor * 1.01 else eased
            self.calm_since = time.time()
            say("    no slow-down requests for %.0f hours; minimum gap eased to %.2f s"
                % (self.relax_after / 3600, self.floor))

    def _wait(self):
        gap = time.time() - self.last
        if gap < self.pause:
            time.sleep(self.pause - gap)

    def _drop(self, key):
        c = self.conns.pop(key, None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def close(self):
        for key in list(self.conns):
            self._drop(key)

    def _fetch(self, url, redirects=3):
        """One GET. Returns (status, headers, body)."""
        parts = urllib.parse.urlsplit(url)
        headers = {"User-Agent": UA, "Accept-Encoding": "gzip"}
        if _via_proxy(parts):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, b""
        https = parts.scheme == "https"
        key = (parts.scheme, parts.hostname, parts.port or (443 if https else 80))
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        for fresh in (False, True):
            reused = key in self.conns
            c = self.conns.get(key)
            if c is None:
                c = (http.client.HTTPSConnection(key[1], key[2], timeout=90) if https
                     else http.client.HTTPConnection(key[1], key[2], timeout=90))
                self.conns[key] = c
            try:
                c.request("GET", path, headers=headers)
                r = c.getresponse()
            except (http.client.HTTPException, OSError):
                self._drop(key)
                if reused and not fresh:
                    continue        # the server quietly closed an idle connection: open a new one
                raise
            try:
                body = r.read()
            except (http.client.HTTPException, OSError):
                self._drop(key)
                raise
            if r.will_close:
                self._drop(key)
            if r.status in (301, 302, 303, 307, 308) and r.getheader("Location") and redirects:
                return self._fetch(urllib.parse.urljoin(url, r.getheader("Location")), redirects - 1)
            return r.status, r.headers, body
        raise OSError("could not connect to %s" % key[1])

    def get(self, url, binary=False, tries=6):
        backoff = 5.0
        for attempt in range(tries):
            self._wait()
            t0 = time.time()
            try:
                status, headers, data = self._fetch(url)
                if status == 200:
                    if headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                    want = headers.get("Content-Length")
                    if binary and want and headers.get("Content-Encoding") != "gzip" and len(data) != int(want):
                        raise ShortRead()
                    took = time.time() - t0
                    self.last = time.time()
                    self.requests += 1
                    self.conn_fail = 0
                    self._relax()
                    # slower whenever the servers are slower, as their policy asks
                    self.pause = min(30.0, self.floor + 0.5 * took)
                    return data
                self.last = time.time()
                self.requests += 1
                self.conn_fail = 0
                if status == 404:
                    return None
                wait = backoff
                ra = headers.get("Retry-After") if headers else None
                if ra and ra.isdigit():
                    wait = max(wait, float(ra))
                if status in (429, 500, 502, 503, 504):
                    say("    server asked us to slow down (%d), waiting %.0fs" % (status, wait))
                    self._warned()                              # and stay slower for a while
                    time.sleep(wait)
                    backoff = min(300.0, backoff * 2)
                    continue
                return None
            except (ShortRead, http.client.HTTPException):
                self.last = time.time()          # a truncated file or response: just try it again
                time.sleep(backoff)
                backoff = min(300.0, backoff * 2)
            except (urllib.error.URLError, OSError) as e:
                # Could not reach the server at all. A few quick retries cover a
                # blip; after that the connection is gone, so stop rather than
                # retrying all night.
                self.last = time.time()
                self.conn_fail += 1
                if self.conn_fail >= 4:
                    raise NetworkDown(str(getattr(e, "reason", e)))
                time.sleep(min(backoff, 20.0))
                backoff = min(60.0, backoff * 2)
        return None

    def _api(self, q):
        """One API query, parsed. maxlag tells the API to refuse us whenever
        its databases are behind, the standard courtesy for automated
        clients; we then wait and ask again."""
        q = dict(q, action="query", format="json", maxlag="5")
        for attempt in range(6):
            raw = self.get(self.api + "?" + urllib.parse.urlencode(q))
            if raw is None:
                return None
            try:
                j = json.loads(raw)
            except ValueError:
                return None
            err = j.get("error", {})
            if err.get("code") == "maxlag":
                lag = float(err.get("lag", 5) or 5)
                say("    Commons is under load (lag %.0fs), waiting" % lag)
                time.sleep(max(5.0, lag))
                continue
            return j
        return None

    def titles(self, pageids):
        """Just the titles of up to fifty files: about the cheapest question
        the API can be asked. Used to decide which photos name their site
        before asking for anything costly."""
        j = self._api({"pageids": "|".join(str(p) for p in pageids)})
        out = {}
        for p in ((j or {}).get("query", {}).get("pages", {}) or {}).values():
            if "missing" not in p and p.get("pageid") and p.get("title"):
                out[int(p["pageid"])] = p["title"]
        return out

    def lookup(self, pageids, width):
        """Image addresses, authors and licences for up to fifty files. The
        author and licence come from each file's description page, which
        makes this one of the dearer queries, so only files that will be
        kept are asked about."""
        j = self._api({
            "pageids": "|".join(str(p) for p in pageids),
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiextmetadatafilter": "Artist|LicenseShortName",
            "iiurlwidth": str(width),
        })
        if j is None:
            return {}
        out = {}
        for p in (j.get("query", {}).get("pages", {}) or {}).values():
            ii = (p.get("imageinfo") or [None])[0]
            if not ii or "jpeg" not in (ii.get("mime") or ""):
                continue
            em = ii.get("extmetadata") or {}
            artist = plain_text((em.get("Artist") or {}).get("value", ""))
            lic = plain_text((em.get("LicenseShortName") or {}).get("value", ""))
            out[int(p["pageid"])] = {
                "title": p.get("title", ""),
                "url": ii.get("thumburl") or ii.get("url"),
                "author": artist, "licence": lic,
            }
        return out

# ---------------------------------------------------------------- matching

STOP = {"the","of","and","a","an","at","on","in","near","old","file","jpg","jpeg","geograph","org","uk"}

NEAR_PLACE = 10000      # metres: a town or village this close names the area, not the site

def fold(s):
    """Lower case without accents, so the Welsh 'Brân' matches 'Bran'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()

def load_places(state_dir):
    """Positions of every one-word town and village name, from places.tsv."""
    idx = {}
    path = os.path.join(state_dir, "places.tsv")
    if not os.path.exists(path):
        return idx
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            name = fold(p[0]).strip() if p else ""
            if len(p) < 3 or not name or not re.fullmatch(r"[a-z0-9]+", name):
                continue
            try:
                idx.setdefault(name, []).append((float(p[1]), float(p[2])))
            except ValueError:
                pass
    return idx

def key_words(name, la, lo, places):
    """The words of a site's name that a photograph of it should mention.

    A word that is also the name of a town or village within 10 km is left
    out, unless nothing else would be left: Quarter Collieries is named
    after the village of Quarter next door, so photos of Quarter's streets
    are not photos of the colliery. A namesake far away does not count:
    Eagle in Lincolnshire says nothing about Eagle Crag in the Lakes."""
    ws = [w for w in re.split(r"[^a-z0-9]+", fold(name)) if len(w) > 3 and w not in STOP]
    keep = [w for w in ws
            if not any(metres(la, lo, pla, plo) <= NEAR_PLACE for pla, plo in places.get(w, ()))]
    return keep or ws

def match_score(title, words):
    """Share of the key words found in a photograph's title, 0 to 1."""
    if not words:
        return 0.0
    t = fold(title)
    return sum(1 for w in words if w in t) / len(words)

def is_match(title, words):
    """At least half the key words in the title. Checked against a random
    sample of real results on 30 Oct 2026: about half of all photographs
    picked only for being nearby showed roads and fields, not the site."""
    return match_score(title, words) >= 0.5

def set_aside_offtopic(state_dir, root, sites, places):
    """Photographs already downloaded whose titles do not name their site are
    moved to photos-removed (nothing is deleted) and dropped from the
    manifest, so the app stops showing them. Returns how many were moved."""
    man = os.path.join(state_dir, "photos.tsv")
    if not os.path.exists(man):
        return 0
    where = {sid: (name, la, lo) for sid, name, la, lo in sites}
    with open(man, encoding="utf-8") as f:
        lines = [l if l.endswith("\n") else l + "\n" for l in f if l.strip()]
    keep, moved = [], 0
    for line in lines:
        p = line.rstrip("\n").split("\t")
        if len(p) < 5:
            continue
        sid, k, title = p[0], p[1], p[4]
        name, la, lo = where.get(sid, ("", 0.0, 0.0))
        if name and is_match(title, key_words(name, la, lo, places)):
            keep.append(line)
            continue
        src = os.path.join(root, "photos", sid[-2:], sid, k + ".jpg")
        if os.path.exists(src):
            dst = os.path.join(root, "photos-removed", sid[-2:], sid, k + ".jpg")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            moved += 1
            try:
                os.rmdir(os.path.dirname(src))      # only succeeds once the folder is empty
            except OSError:
                pass
    if len(keep) != len(lines):
        tmp = man + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, man)
    return moved

# ---------------------------------------------------------------- outputs

def esc_js(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ").replace("</script", "<\\/script").strip()

def write_outputs(state_dir, root):
    """photos.js in the compact form the app reads, plus ATTRIBUTION.txt."""
    man = os.path.join(state_dir, "photos.tsv")
    if not os.path.exists(man):
        return 0
    authors, a_idx, lics, l_idx, by, credits = [], {}, [], {}, {}, []
    # A run stopped part way through a batch redoes that batch next time, so
    # a photo can appear twice in the manifest: the last row for it wins.
    rows = {}
    with open(man, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5:
                rows[(p[0], p[1])] = p[:5]
    for sid, k, au, li, title in rows.values():
        if not os.path.exists(os.path.join(root, "photos", sid[-2:], sid, k + ".jpg")):
            continue        # never point the app at a file that is not there
        if au not in a_idx: a_idx[au] = len(authors); authors.append(au)
        if li not in l_idx: l_idx[li] = len(lics); lics.append(li)
        by.setdefault(sid, []).append("[%s,%d,%d]" % (k, a_idx[au], l_idx[li]))
        credits.append("%s\r\n    photos/%s/%s/%s.jpg\r\n    %s / %s" % (title, sid[-2:], sid, k, au or "unknown", li or "see file page"))

    js = ["/* Generated by tools\\photos.py. Photographs from Wikimedia Commons. */",
          "/* Author and licence for every file. Full credits in ATTRIBUTION.txt. */",
          "window.WF_AUTHORS=[" + ",".join('"%s"' % esc_js(a) for a in authors) + "];",
          "window.WF_LICENCES=[" + ",".join('"%s"' % esc_js(l) for l in lics) + "];",
          "window.WF_PHOTOS={" + ",".join('"%s":[%s]' % (esc_js(s), ",".join(v)) for s, v in by.items()) + "};", ""]
    tmp = os.path.join(root, "data", "photos.js.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(js))
    os.replace(tmp, os.path.join(root, "data", "photos.js"))

    att = ["WildFind - picture credits", "==========================", "",
           "Site names and positions: (c) OpenStreetMap contributors, Open Database Licence.",
           "  https://www.openstreetmap.org/copyright", "",
           "Photographs: Wikimedia Commons. Author and licence for each file below.",
           "Most are Geograph photographs under CC BY-SA 2.0.", ""] + credits
    with open(os.path.join(root, "ATTRIBUTION.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(att) + "\r\n")
    return len(by)

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state_dir"); ap.add_argument("root")
    ap.add_argument("--dump", default=None)
    ap.add_argument("--needs-dump", action="store_true")
    ap.add_argument("--shortlist-only", action="store_true",
                    help="build the shortlist from --dump and stop, so the dump can be deleted before any image downloads")
    ap.add_argument("--max-sites", type=int, default=35000)
    ap.add_argument("--per-site", type=int, default=3)
    ap.add_argument("--width", type=int, default=500)          # kept on disk
    ap.add_argument("--fetch-width", type=int, default=640)    # asked of Wikimedia
    ap.add_argument("--max-gb", type=float, default=9.5)
    ap.add_argument("--radius", type=float, default=600)
    ap.add_argument("--any-nearby", action="store_true",
                    help="also keep nearby photographs whose titles do not name the site")
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--api", default=API)       # tests point this at a local server
    a = ap.parse_args()

    root, state = a.root, a.state_dir
    photo_dir = os.path.join(root, "photos")
    os.makedirs(photo_dir, exist_ok=True)

    sites = load_sites(state)
    cand_path = os.path.join(state, "candidates.tsv")
    sites_path = os.path.join(state, "sites.tsv")
    fresh = os.path.exists(cand_path) and os.path.getmtime(cand_path) >= os.path.getmtime(sites_path)

    if a.needs_dump:
        print("no" if fresh else "yes"); return

    if not fresh:
        if not a.dump or not os.path.exists(a.dump):
            sys.exit("The Commons location dump is needed once to build the shortlist. Pass --dump.")
        say("  reading the Commons location dump (only coordinates near a site are kept)...")
        grid = parse_geo_tags(a.dump, wanted_cells(sites))
        tmp = cand_path + ".tmp"
        with_any = 0
        with open(tmp, "w", encoding="utf-8") as f:
            for sid, name, la, lo in sites:
                lim = a.per_site * 2 if name else a.per_site + 1     # spares, and room to match names
                c = candidates(grid, la, lo, a.radius, lim)
                if c:
                    with_any += 1
                f.write(sid + "\t" + ",".join(str(x) for x in c) + "\n")
        os.replace(tmp, cand_path)
        del grid
        say("  shortlist saved: %d of %d sites have photographs nearby" % (with_any, len(sites)))

    if a.shortlist_only:
        return

    cands = {}
    with open(cand_path, encoding="utf-8") as f:
        for line in f:
            sid, _, rest = line.rstrip("\n").partition("\t")
            cands[sid] = [int(x) for x in rest.split(",") if x]

    done_path = os.path.join(state, "photos-done.txt")
    done = set()
    if os.path.exists(done_path):
        with open(done_path, encoding="utf-8") as f:
            done = {l.strip() for l in f if l.strip()}
    places = load_places(state)
    if a.any_nearby:
        wanted = sites
    else:
        # Only photographs whose titles name their site. Unnamed sites have
        # nothing to match, so they get none (the app shows its sketch).
        say("  keeping only photographs whose titles name their site")
        moved = set_aside_offtopic(state, root, sites, places)
        if moved:
            say("  moved %d earlier photographs that do not name their site to photos-removed"
                " (safe to delete)" % moved)
        wanted = [s for s in sites if s[1]]
    todo = [s for s in wanted if s[0] not in done][: max(0, a.max_sites - len(done))]
    say("  %d sites in total, %d already done, %d to do now" % (len(wanted), len(done & {s[0] for s in wanted}), len(todo)))
    if not todo:
        n = write_outputs(state, root); say("  nothing left to do; %d sites have photographs" % n); return

    used = 0
    for dp, _, fs in os.walk(photo_dir):
        for fn in fs:
            used += os.path.getsize(os.path.join(dp, fn))
    cap = a.max_gb * 1024 ** 3
    say("  photos folder is %.2f GB of %.1f GB allowed" % (used / 1024 ** 3, a.max_gb))
    # never fill the drive: leave a gigabyte free whatever the limit says
    try:
        free = shutil.disk_usage(photo_dir).free
        room = used + max(0, free - 1024 ** 3)
        if room < cap:
            cap = room
            say("  only %.1f GB free on this drive, so photographs stop at %.1f GB to leave 1 GB spare"
                % (free / 1024 ** 3, cap / 1024 ** 3))
    except OSError:
        pass

    if Image is None:
        say("  Pillow is not installed, so photos are kept at %d px; fewer will fit in the limit" % a.fetch_width)
    http = Polite(a.delay, a.api)
    man = open(os.path.join(state, "photos.tsv"), "a", encoding="utf-8")
    donef = open(done_path, "a", encoding="utf-8")
    t0 = time.time(); last_report = t0; got_sites = 0; got_files = 0
    BATCH = 50
    rc = 0          # 3 tells fetch.py the connection was lost, so it can try again later

    try:
        i = 0
        while i < len(todo):
            if used >= cap:
                say("  size limit reached. Run again with a bigger --max-gb to continue.")
                break
            # Gather sites until about fifty photos are worth asking about.
            # Titles come first, fifty at a time, because they are cheap; the
            # dear lookup of authors and licences is then made only for the
            # photos whose titles name their site.
            queue, wanted_n = [], 0
            while i < len(todo) and wanted_n < BATCH:
                # one titles request: sites until their candidates fill fifty;
                # a site that would overflow waits for the next request
                chunk, ids, idset = [], [], set()
                while i < len(todo):
                    sid, name, la, lo = todo[i]
                    c = [p for p in cands.get(sid, []) if p not in idset]
                    if chunk and len(ids) + len(c) > BATCH:
                        break
                    chunk.append((todo[i], c)); ids.extend(c); idset.update(c); i += 1
                if a.any_nearby:
                    for site, c in chunk:
                        queue.append((site, c)); wanted_n += len(c)
                    continue
                names = http.titles(ids) if ids else {}
                for site, c in chunk:
                    words = key_words(site[1], site[2], site[3], places) if site[1] else []
                    keep = [p for p in c if p in names and is_match(names[p], words)]
                    queue.append((site, keep)); wanted_n += len(keep)

            wanted = [p for _, c in queue for p in c]
            info = {}
            for k0 in range(0, len(wanted), BATCH):
                info.update(http.lookup(wanted[k0:k0 + BATCH], a.fetch_width))

            for (sid, name, la, lo), site_cands in queue:
                words = key_words(name, la, lo, places) if name else []
                picks = [p for p in site_cands if p in info
                         and (a.any_nearby or is_match(info[p]["title"], words))]
                picks.sort(key=lambda p: -match_score(info[p]["title"], words))   # stable: distance order otherwise
                k = 0
                for p in picks:                  # spares step in if a download fails
                    if k >= a.per_site:
                        break
                    meta = info[p]
                    data = http.get(meta["url"], binary=True)
                    if not data or data[:2] != b"\xff\xd8":     # must actually be a JPEG
                        continue
                    data = shrink(data, a.width)
                    k += 1
                    d = os.path.join(photo_dir, sid[-2:], sid)
                    os.makedirs(d, exist_ok=True)
                    tmp = os.path.join(d, "%d.jpg.part" % k)
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, os.path.join(d, "%d.jpg" % k))   # never a half file
                    used += len(data); got_files += 1
                    man.write("%s\t%d\t%s\t%s\t%s\n" % (sid, k,
                              meta["author"].replace("\t", " "), meta["licence"].replace("\t", " "),
                              meta["title"].replace("\t", " ")))
                if k:
                    got_sites += 1
                donef.write(sid + "\n")

            man.flush(); donef.flush()
            if time.time() - last_report > 60:
                last_report = time.time()
                rate = i / max(1.0, time.time() - t0)
                left = (len(todo) - i) / max(0.01, rate) / 3600
                say("  [%d/%d] %d sites with photos, %d files, %.2f GB, about %.1f h left"
                    % (i, len(todo), got_sites, got_files, used / 1024 ** 3, left))
                write_outputs(state, root)
    except KeyboardInterrupt:
        say("\n  stopped. Everything so far is saved; run again to carry on.")
        rc = 130
    except NetworkDown as e:
        say("\n  lost the connection to Wikimedia (%s)." % e)
        say("  Everything so far is saved.")
        rc = 3
    finally:
        http.close()
        man.close(); donef.close()
        n = write_outputs(state, root)
        say("  %d sites now have photographs, %.2f GB on disk, %d requests made"
            % (n, used / 1024 ** 3, http.requests))
    return rc

if __name__ == "__main__":
    sys.exit(main() or 0)
