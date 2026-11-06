#!/usr/bin/env python3
"""
WildFind data fetcher.

    python fetch.py <project_root> [--stage all|osm|photos] [options]

Fills the app with real UK data so it works offline afterwards. Run it again
at any point and it carries on from where it stopped.

  Stage 1, osm     Downloads Geofabrik's nightly OpenStreetMap extract of the
                   UK (~2.1 GB) and the Isle of Man, pulls out every
                   abandoned place with extract-osm.py, writes the app's data
                   files and deletes the extracts again. A few minutes plus
                   the download.

  Stage 2, photos  Runs photos.py: one download of Commons' list of where
                   every photograph was taken, then the photographs
                   themselves one at a time, politely. Many hours, on purpose.

Everything that can be a bulk download is one. Downloads use a single
connection and resume if interrupted. See photos.py for how the photo stage
keeps its requests to Wikimedia's published limits.
"""

import sys, os, json, time, argparse, subprocess, shutil
import urllib.request, urllib.error, http.client

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "WildFind/2.1 (personal offline urbex planner; https://github.com/AtlasDelta7)"

# Order must match KINDS in extract-osm.py and WF_KINDS in the app.
KINDS = ["mine","industrial","military","railway","religious","castle","agrarian","folly","ruins"]

def say(msg=""):
    print(msg, flush=True)

# ---------------------------------------------------------------- one at a time

class SingleInstance:
    """Two fetchers writing the same files would corrupt them. An exclusive
    lock on a file is released by the operating system the moment the
    holder exits, however it exits, so a crashed run never leaves a stale
    lock behind. (Checking a process id instead is not safe on Windows,
    where os.kill(pid, 0) terminates the process rather than probing it.)"""
    def __init__(self, path):
        self.path = path
        self.f = None

    def acquire(self):
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False

# ---------------------------------------------------------------- downloads

def version_of(headers):
    """What identifies this exact copy of a file on the server."""
    return headers.get("ETag") or headers.get("Last-Modified") or ""

def remote_info(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            n = r.headers.get("Content-Length")
            return (int(n) if n else 0), version_of(r.headers)
    except Exception:
        return 0, ""

def free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None

def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def write_text(path, s):
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)

def download(url, dest, label):
    """Single connection, resumes a partial file with a Range request, and
    only renames into place once the whole file has arrived.

    The "latest" files are rebuilt in place (Geofabrik's every night), so a
    partial file from yesterday must never be finished off with today's
    bytes. The version of the file being fetched is noted beside the partial
    copy, and every resume carries If-Range: the server only continues if it
    still has that same version, and otherwise sends the whole new file."""
    want, ver = remote_info(url)
    if os.path.exists(dest) and want and os.path.getsize(dest) == want:
        say("  %s: already downloaded (%.2f GB)" % (label, want / 1024 ** 3))
        return True
    if os.path.exists(dest) and want:
        os.remove(dest)          # an older copy; make room for the new one

    part = dest + ".part"
    idf = part + ".version"
    saved = read_text(idf)
    if os.path.exists(part) and (not saved or (ver and saved != ver)):
        say("  %s: the file has been updated on the server since the last attempt; starting again" % label)
        os.remove(part)

    if want:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        free = free_bytes(os.path.dirname(dest))
        if free is not None and free < (want - have) + 300 * 1024 ** 2:
            say("  %s: not enough disk space. It needs %.1f GB free and the drive has %.1f GB."
                % (label, (want - have) / 1024 ** 3 + 0.3, free / 1024 ** 3))
            say("  Free some space and run this again.")
            return False

    for attempt in range(8):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if want and have > want:
            os.remove(part); have = 0
        if want and have == want:
            break
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = "bytes=%d-" % have
            headers["If-Range"] = saved
            say("  %s: resuming at %.0f MB of %.0f MB" % (label, have / 1024 ** 2, want / 1024 ** 2))
        else:
            say("  %s: downloading %.2f GB" % (label, want / 1024 ** 3) if want else "  %s: downloading" % label)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                if have and r.status != 206:
                    have = 0            # the server ignored the range, or the file changed; start again
                if not have:
                    saved = version_of(r.headers) or ver
                    write_text(idf, saved)
                    n = r.headers.get("Content-Length")
                    if n:
                        want = int(n)
                mode = "ab" if have else "wb"
                got, t0, shown = have, time.time(), -1
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if want:
                            pct = int(100 * got / want)
                            if pct // 10 != shown:
                                shown = pct // 10
                                rate = (got - have) / max(0.1, time.time() - t0) / 1024 ** 2
                                say("    %3d%%  %.0f MB of %.0f MB  (%.1f MB/s)" % (pct, got / 1024 ** 2, want / 1024 ** 2, rate))
            if not want or os.path.getsize(part) == want:
                break
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            wait = min(120, 5 * 2 ** attempt)
            say("    connection problem (%s); retrying in %ds" % (getattr(e, "reason", e), wait))
            time.sleep(wait)
    else:
        say("  %s: could not complete the download. Run again later to resume." % label)
        return False

    if want and os.path.getsize(part) != want:
        say("  %s: download incomplete. Run again later to resume." % label)
        return False
    os.replace(part, dest)
    if os.path.exists(idf):
        os.remove(idf)
    return True

# ---------------------------------------------------------------- app data files

def js_rows(name, rows, header):
    """ensure_ascii writes every non-ASCII character as an escape, so Welsh
    and Gaelic names survive whatever encoding the browser assumes."""
    body = ",".join(json.dumps(r, ensure_ascii=True, separators=(",", ":")) for r in rows)
    return header + "window.%s=[%s];\n" % (name, body)

def num(x, places):
    return round(float(x), places)

def write_locations(state, data_dir):
    path = os.path.join(state, "sites.tsv")
    if not os.path.exists(path):
        return 0, 0
    seen, rows, named = set(), [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or p[0] in seen:
                continue
            try:
                la, lo = num(p[2], 5), num(p[3], 5)
            except ValueError:
                continue
            seen.add(p[0])
            k = KINDS.index(p[4]) if p[4] in KINDS else KINDS.index("ruins")
            desc = p[5] if len(p) > 5 else ""
            rows.append([p[0], p[1], la, lo, k, desc])
            named += 1 if p[1] else 0
    head = ("/* Generated by tools\\fetch.py - do not hand edit. */\n"
            "/* Sites (c) OpenStreetMap contributors, Open Database Licence. */\n"
            "window.WF_KINDS=%s;\n" % json.dumps(KINDS))
    tmp = os.path.join(data_dir, "locations.js.tmp")
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        f.write(js_rows("WF_LOCATIONS", rows, head))
    os.replace(tmp, os.path.join(data_dir, "locations.js"))
    return len(rows), named

def write_places(state, data_dir):
    path = os.path.join(state, "places.tsv")
    if not os.path.exists(path):
        return 0
    rank = {"city": 0, "town": 1, "village": 2}
    seen, rows = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            try:
                la, lo = num(p[1], 4), num(p[2], 4)
            except ValueError:
                continue
            key = (p[0].lower(), round(la, 2))
            if key in seen:
                continue
            seen.add(key)
            rows.append([p[0], la, lo, rank.get(p[3], 2)])
    head = "/* Generated by tools\\fetch.py. Places (c) OpenStreetMap contributors, ODbL. */\n"
    tmp = os.path.join(data_dir, "places.js.tmp")
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        f.write(js_rows("WF_PLACES", rows, head))
    os.replace(tmp, os.path.join(data_dir, "places.js"))
    return len(rows)

# ---------------------------------------------------------------- stages

def importable(module):
    """Checked in a fresh Python rather than this one. The Microsoft Store
    Python installs libraries into a per-user folder that a running Python
    only notices if the folder existed when it started, which on a new
    install it did not. The scripts that need the library run as fresh
    processes too, so this asks exactly the question that matters."""
    r = subprocess.run([sys.executable, "-c", "import " + module], capture_output=True)
    return r.returncode == 0

def ensure_package(module, package, label):
    if importable(module):
        return True
    say("  installing the %s library (one time, a few seconds)..." % label)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", package],
                       capture_output=True, text=True)
    if importable(module):
        say("  %s installed" % label)
        return True
    tail = [l for l in ((r.stdout or "") + (r.stderr or "")).splitlines() if l.strip()][-6:]
    for l in tail:
        say("    " + l)
    say("  could not install %s. Try by hand:  \"%s\" -m pip install %s" % (label, sys.executable, package))
    return False

def ensure_osmium():
    return ensure_package("osmium", "osmium", "osmium")

def read_lines(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip()}

def stage_osm(a, root, state, data_dir):
    say("")
    say("STAGE 1 of 2  The UK site list, from an OpenStreetMap extract")
    if not ensure_osmium():
        return False

    done_path = os.path.join(state, "extracts-done.txt")
    done = read_lines(done_path)
    if not done:
        # A fresh run: clear anything an earlier version of this tool left
        # (the old Overpass approach wrote some of these) so nothing stale
        # is mixed in.
        for fn in ("sites.tsv", "places.tsv", "boxes-done.txt", "place-boxes-done.txt",
                   "tiles-done.txt", "candidates.tsv"):
            p = os.path.join(state, fn)
            if os.path.exists(p):
                os.remove(p)

    extracts = [
        ("united-kingdom", a.geofabrik + "/europe/united-kingdom-latest.osm.pbf"),
        ("isle-of-man",    a.geofabrik + "/europe/isle-of-man-latest.osm.pbf"),
    ]
    ok = True
    for name, url in extracts:
        if name in done:
            say("  %s: already processed" % name)
            continue
        pbf = os.path.join(state, name + ".osm.pbf")
        if not download(url, pbf, name):
            ok = False
            continue
        say("  filtering %s..." % name)
        r = subprocess.run([sys.executable, os.path.join(HERE, "extract-osm.py"), pbf, state])
        if r.returncode != 0:
            say("  the filter step failed; the extract is kept so it need not download again")
            ok = False
            continue
        with open(done_path, "a", encoding="utf-8") as f:
            f.write(name + "\n")
        if a.keep_extract:
            say("  keeping %s as asked" % os.path.basename(pbf))
        else:
            os.remove(pbf)
            say("  extract deleted; the filtered data is what matters")

    n, named = write_locations(state, data_dir)
    places = write_places(state, data_dir)
    if n:
        say("  data\\locations.js: %d sites (%d named)" % (n, named))
        say("  data\\places.js: %d places" % places)
    return ok

def stage_photos(a, root, state):
    say("")
    say("STAGE 2 of 2  Photographs from Wikimedia Commons")
    if not os.path.exists(os.path.join(state, "sites.tsv")):
        say("  No sites yet. Run the osm stage first.")
        return False
    # Photos are fetched at 640 px and shrunk here; without Pillow they are
    # kept at 640 px, which still works but fits fewer in the size limit.
    ensure_package("PIL", "pillow", "Pillow image")
    script = os.path.join(HERE, "photos.py")
    common = [sys.executable, script, state, root,
              "--max-sites", str(a.max_sites), "--per-site", str(a.per_site),
              "--width", str(a.width), "--fetch-width", str(a.fetch_width),
              "--max-gb", str(a.max_gb), "--delay", str(a.delay),
              "--api", a.api]

    def needs_dump():
        r = subprocess.run([sys.executable, script, state, root, "--needs-dump"], capture_output=True, text=True)
        return r.stdout.strip().endswith("yes")

    if needs_dump():
        dump = os.path.join(state, "commons-geo_tags.sql.gz")
        say("  First run: one download of Commons' photo location list (about 770 MB).")
        if not download(a.dump_url, dump, "commons location list"):
            return False
        # Two steps, so the 770 MB list is gone before the first image
        # arrives and the whole run stays inside the size limit.
        subprocess.run(common + ["--dump", dump, "--shortlist-only"])
        if needs_dump():
            say("  could not build the shortlist; the location list is kept so it need not download again")
            return False
        os.remove(dump)
        say("  location list deleted; the shortlist it produced is saved")
    # Left running overnight, a dropped connection or a crash should not end
    # the night's work. Any stop other than Ctrl+C is retried after a pause,
    # with a fresh copy of photos.py each time (so a corrected script is
    # picked up), for as long as --retries allows.
    for attempt in range(a.retries + 1):
        rc = subprocess.run(common).returncode
        if rc == 0:
            return True
        if rc == 130 or attempt == a.retries:
            break
        say("")
        say("  The photo stage stopped (code %d). Trying again in %.0f minutes (%d of %d)."
            % (rc, a.retry_wait / 60, attempt + 1, a.retries))
        say("  Close this window to stop for good; everything so far is saved.")
        time.sleep(a.retry_wait)
    return False

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Fill WildFind with real UK data.")
    ap.add_argument("root", help="the WildFind folder")
    ap.add_argument("--stage", choices=["all", "osm", "photos"], default="all")
    ap.add_argument("--max-sites", type=int, default=35000)
    ap.add_argument("--per-site", type=int, default=3)
    ap.add_argument("--width", type=int, default=500)
    ap.add_argument("--fetch-width", type=int, default=640)
    ap.add_argument("--max-gb", type=float, default=9.5)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--keep-extract", action="store_true")
    ap.add_argument("--retries", type=int, default=144)          # 12 hours at 5 minutes each
    ap.add_argument("--retry-wait", type=float, default=300.0)   # seconds
    # where things come from; only ever changed by the tests
    ap.add_argument("--geofabrik", default="https://download.geofabrik.de")
    ap.add_argument("--dump-url", default="https://dumps.wikimedia.org/commonswiki/latest/commonswiki-latest-geo_tags.sql.gz")
    ap.add_argument("--api", default="https://commons.wikimedia.org/w/api.php")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    state = os.path.join(root, "tools", "state")
    data_dir = os.path.join(root, "data")
    os.makedirs(state, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    lock = SingleInstance(os.path.join(state, "fetch.lock"))
    if not lock.acquire():
        say("Another copy of the fetcher is already running on this folder.")
        say("Let that one finish, or close its window, then try again.")
        return 2

    say("WildFind data fetcher")
    say("project folder: " + root)
    say("Ctrl+C stops it at any point. Running it again resumes where it stopped.")
    try:
        if a.stage in ("all", "osm"):
            if not stage_osm(a, root, state, data_dir) and a.stage == "all":
                say("\n  Stage 1 did not finish, so the photo stage is skipped for now.")
                return 1
        if a.stage in ("all", "photos"):
            if not stage_photos(a, root, state):
                say("\n  The photo stage has not finished. Run this again to carry on where it stopped.")
                return 3
        say("")
        say("Done. Open WildFind.html and it will pick up the new data.")
        return 0
    except KeyboardInterrupt:
        say("\nStopped. Nothing is lost; run it again to carry on.")
        return 130

if __name__ == "__main__":
    sys.exit(main())
