"""
End to end: fetch.py -> extract-osm.py -> photos.py against fake Geofabrik and
Commons servers with bad days built in - a cut-off 2 GB download, an overnight
rebuild of the extract, a cut-off image, database lag, and a second copy
started while one runs. Works on a throwaway copy of the project.

    python tests/test_end_to_end.py        (needs: pip install osmium pillow)
"""
import os, sys, io, json, gzip, math, time, shutil, socket, threading, subprocess, tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import osmium, osmium.osm.mutable as m
from PIL import Image

HERE = tempfile.mkdtemp(prefix="wildfind-e2e-")
SRC  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(HERE, "WildFind")
FILES = os.path.join(HERE, "served"); os.makedirs(FILES, exist_ok=True)

# ---------------------------------------------------------------- a fresh copy of the project
shutil.copytree(SRC, PROJ, ignore=shutil.ignore_patterns("__pycache__", "state", "photos", "tests", ".git"))
st = os.path.join(PROJ, "tools", "state"); os.makedirs(st, exist_ok=True)
# leftovers from the old Overpass version, which must not survive
open(os.path.join(st, "sites.tsv"), "w").write("n999\tStale Overpass Row\t51.0\t-1.0\truins\t\n")
open(os.path.join(st, "boxes-done.txt"), "w").write("49.85,-6.50,51.00,-4.50\n")

def off(la, lo, n, e): return la + n / 111320.0, lo + e / (111320.0 * math.cos(math.radians(la)))

# ---------------------------------------------------------------- fake Geofabrik extracts
def build_uk(path, rebuilt=False):
    if os.path.exists(path): os.remove(path)
    w = osmium.SimpleWriter(path)
    nid = 1
    def node(la, lo, tags=None):
        nonlocal nid; w.add_node(m.Node(id=nid, location=(lo, la), tags=tags or {})); nid += 1; return nid - 1
    # places
    node(53.3811, -1.4701, {"place": "city", "name": "Sheffield"})
    node(53.2591, -1.9115, {"place": "town", "name": "Buxton"})
    node(52.9700, -3.1700, {"place": "town", "name": "Llangollen"})
    # node sites
    node(50.30541, -5.23190, {"historic": "ruins", "name": "Wheal Coates"})
    node(53.17250, -1.68890, {"man_made": "mineshaft"})                       # unnamed
    node(52.97900, -3.15600, {"ruins": "yes", "name": "Castell Dinas Brân"})   # Welsh, diacritic
    node(51.44352, -2.85357, {"military": "bunker", "name": "ROC Post"})
    # noise: thousands of untagged and irrelevant objects the filters must skip
    for i in range(5000):
        node(52 + (i % 100) / 100, -2 + (i // 100) / 100, {"highway": "crossing"} if i % 7 == 0 else None)
    # a ruined mill as a closed way, and an abandoned railway with an odd value
    a = [node(*off(53.10, -1.60, n, e)) for n, e in ((0, 0), (40, 0), (40, 40), (0, 40))]
    b = [node(53.30 + i / 1000, -1.70) for i in range(4)]
    c = [node(53.20, -1.50 + i / 1000) for i in range(3)]
    w.add_way(m.Way(id=1, nodes=a + [a[0]], tags={"building": "ruins", "name": "Hen Felin"}))
    w.add_way(m.Way(id=2, nodes=b, tags={"abandoned:railway": "weird_value"}))
    w.add_way(m.Way(id=3, nodes=c, tags={"building": "yes"}))                    # must be skipped
    if rebuilt:                                                                   # appears in the next night's file
        node(52.50, -3.50, {"historic": "ruins", "name": "Rebuilt Site"})
    w.close()

def build_iom(path):
    w = osmium.SimpleWriter(path)
    w.add_node(m.Node(id=1, location=(-4.4814, 54.1509), tags={"place": "town", "name": "Douglas"}))
    w.add_node(m.Node(id=2, location=(-4.4567, 54.2650), tags={"historic": "mine", "name": "Laxey Mine"}))
    w.close()

os.makedirs(os.path.join(FILES, "europe"), exist_ok=True)
build_uk(os.path.join(FILES, "europe", "united-kingdom-latest.osm.pbf"))
build_iom(os.path.join(FILES, "europe", "isle-of-man-latest.osm.pbf"))

# ---------------------------------------------------------------- fake Commons location dump
P = {}
def photo(pid, base, n, kind, title): la, lo = off(base[0], base[1], n, 0); P[pid] = (la, lo, kind, title)
photo(101, (50.30541, -5.23190),  60, "object", "File:Wheal Coates engine house.jpg")
photo(102, (50.30541, -5.23190), 150, "camera", "File:St Agnes Head.jpg")
photo(103, (50.30541, -5.23190), 250, "camera", "File:Chapel Porth.jpg")
photo(201, (52.97900, -3.15600), 100, "camera", "File:Castell Dinas Bran from the south.jpg")
photo(301, (54.26500, -4.45670),  80, "object", "File:Laxey Wheel and mine.jpg")
rows = ["(%d,%d,'earth',1,%.8f,%.8f,NULL,'%s',NULL,NULL,NULL,NULL,NULL)" % (i, pid, la, lo, k)
        for i, (pid, (la, lo, k, _)) in enumerate(sorted(P.items()), 1)]
os.makedirs(os.path.join(FILES, "commonswiki", "latest"), exist_ok=True)
with gzip.open(os.path.join(FILES, "commonswiki", "latest", "commonswiki-latest-geo_tags.sql.gz"), "wt") as f:
    f.write("INSERT INTO `geo_tags` VALUES\n" + ",\n".join(rows) + ";\n")

ARTIST = {101: 'Unknown author<span style="display: none;">Unknown author</span>',
          301: '<a href="//commons.wikimedia.org/wiki/User:A_%26_B" title="User:A &amp; B">A &amp; B</a>'}
buf = io.BytesIO(); Image.new("RGB", (500, 333), (80, 90, 70)).save(buf, "JPEG"); JPEG = buf.getvalue()

# ---------------------------------------------------------------- the server, with bad days built in
log, inflight, peak, lock = [], [0], [0], threading.Lock()
quirks = {"cut_uk_once": True, "cut_img_101_once": True, "lag_once": True}
VER = {}                                     # file path -> version number, bumped on "rebuild"
def etag(p): return '"v%d"' % VER.get(p, 1)
dump_present_at_image = []

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def _enter(self):
        with lock:
            inflight[0] += 1; peak[0] = max(peak[0], inflight[0])
            log.append((time.time(), self.command, self.path, self.headers.get("Range"), self.headers.get("If-Range")))
    def _leave(self):
        with lock: inflight[0] -= 1
    def do_HEAD(self):
        self._enter()
        try:
            p = os.path.join(FILES, urlparse(self.path).path.lstrip("/"))
            if not os.path.isfile(p): self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Length", str(os.path.getsize(p)))
            self.send_header("ETag", etag(p)); self.end_headers()
        finally: self._leave()
    def do_GET(self):
        self._enter()
        try:
            time.sleep(0.01)
            u = urlparse(self.path)
            if u.path == "/w/api.php": return self.api(parse_qs(u.query))
            if u.path.startswith("/img/"):
                dump_present_at_image.append(any("geo_tags" in f for f in os.listdir(st)))
                pid = int(u.path[5:-4])
                self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(JPEG))); self.end_headers()
                if pid == 101 and quirks["cut_img_101_once"]:
                    quirks["cut_img_101_once"] = False
                    self.wfile.write(JPEG[: len(JPEG) // 3]); return          # connection drops mid-image
                self.wfile.write(JPEG); return
            p = os.path.join(FILES, u.path.lstrip("/"))
            if not os.path.isfile(p): self.send_response(404); self.end_headers(); return
            data = open(p, "rb").read(); start = 0
            rng = self.headers.get("Range")
            ifr = self.headers.get("If-Range")
            if rng and ifr and ifr != etag(p):
                rng = None                                   # file changed since: send it whole
            if rng and rng.startswith("bytes="):
                start = int(rng[6:].split("-")[0])
                self.send_response(206); self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
            else:
                self.send_response(200)
            body = data[start:]
            self.send_header("Content-Length", str(len(body))); self.send_header("ETag", etag(p)); self.end_headers()
            if "united-kingdom" in p and quirks["cut_uk_once"]:
                quirks["cut_uk_once"] = False
                self.wfile.write(body[: int(len(body) * 0.4)])             # drop the big download at 40%...
                build_uk(p, rebuilt=True); VER[p] = 2                       # ...and Geofabrik rebuilds overnight
                return
            self.wfile.write(body)
        finally: self._leave()
    def api(self, q):
        if quirks["lag_once"]:
            quirks["lag_once"] = False
            return self.json({"error": {"code": "maxlag", "lag": 1}})
        pages = {}
        for pid in q["pageids"][0].split("|"):
            pid = int(pid); title = P[pid][3]
            pages[str(pid)] = {"pageid": pid, "title": title, "imageinfo": [{
                "thumburl": "http://127.0.0.1:%d/img/%d.jpg" % (PORT, pid), "url": "x", "mime": "image/jpeg",
                "extmetadata": {"Artist": {"value": ARTIST.get(pid, "<b>Geograph user %d</b>" % pid)}, "LicenseShortName": {"value": "CC BY-SA 2.0"}}}]}
        self.json({"query": {"pages": pages}})
    def json(self, o):
        b = json.dumps(o).encode(); self.send_response(200)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT
ENV = {**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
CMD = [sys.executable, os.path.join(PROJ, "tools", "fetch.py"), PROJ, "--delay", "0.1",
       "--geofabrik", BASE, "--dump-url", BASE + "/commonswiki/latest/commonswiki-latest-geo_tags.sql.gz",
       "--api", BASE + "/w/api.php"]

# ---------------------------------------------------------------- run it
t0 = time.time()
r = subprocess.run(CMD, capture_output=True, text=True, env=ENV)
print(r.stdout[-3000:]); print(r.stderr[-1500:])
first_requests = list(log)

# a second copy while one holds the lock must refuse
sys.path.insert(0, os.path.join(PROJ, "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("f", os.path.join(PROJ, "tools", "fetch.py")); fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
held = fm.SingleInstance(os.path.join(st, "fetch.lock")); assert held.acquire()
r_lock = subprocess.run(CMD, capture_output=True, text=True, env=ENV)
held.f.close()

log.clear()
r2 = subprocess.run(CMD, capture_output=True, text=True, env=ENV)
rerun_requests = list(log)

# ---------------------------------------------------------------- checks
FAILS = []
def ok(label, cond, extra=""):
    if not cond: FAILS.append(label)
    print(("PASS  " if cond else "FAIL  ") + label + (("   " + str(extra)) if extra else ""))
print()
ok("finished cleanly", r.returncode == 0, "exit %d" % r.returncode)
ok("the 2 GB extract was cut off at 40% and resumed with a Range request",
   any(e[1] == "GET" and "united-kingdom" in e[2] and e[3] for e in first_requests))
ok("the resume asked for the same version it started (If-Range)",
   any(e[1] == "GET" and "united-kingdom" in e[2] and e[3] and e[4] == '"v1"' for e in first_requests))
ok("a cut-off image was retried rather than crashing the run",
   sum(1 for e in first_requests if e[2] == "/img/101.jpg") == 2)
ok("never more than one request in flight, across the whole run", peak[0] == 1, "peak %d" % peak[0])
ok("extracts deleted afterwards",  not any(f.endswith(".osm.pbf") or f.endswith(".part") for f in os.listdir(st)), os.listdir(st))
ok("location dump deleted afterwards", not any("geo_tags" in f for f in os.listdir(st)))
ok("location dump was already gone before the first image request",
   dump_present_at_image and not any(dump_present_at_image), dump_present_at_image[:3])

loc = open(os.path.join(PROJ, "data", "locations.js"), "rb").read()
ok("locations.js is pure ASCII (no encoding can garble it)", all(b < 128 for b in loc))
ok("stale Overpass rows were cleared", b"Stale Overpass Row" not in loc)
L = json.loads(loc.decode().split("window.WF_LOCATIONS=")[1].rstrip().rstrip(";"))
names = {row[1] for row in L}
ok("named and unnamed sites present", {"Wheal Coates", "ROC Post", "Hen Felin", "Laxey Mine"} <= names and "" in names, sorted(names))
ok("Welsh name round-trips intact", "Castell Dinas Brân" in names)
ok("Isle of Man included", "Laxey Mine" in names)
ok("exactly the 8 planted sites; ordinary buildings and road crossings skipped", len(L) == 8, "%d sites" % len(L))
ok("rebuilt overnight file was fetched whole, not stitched to the old one", "Rebuilt Site" in names)
ok("both unnamed sites kept (mineshaft, odd-valued abandoned railway)", sum(1 for row in L if row[1] == "") == 2)
pl = open(os.path.join(PROJ, "data", "places.js")).read()
ok("places include Sheffield and Douglas", "Sheffield" in pl and "Douglas" in pl)

man = [l.split("\t") for l in open(os.path.join(st, "photos.tsv"), encoding="utf-8").read().splitlines()]
got = {}
for row in man: got.setdefault(row[0], []).append(row[4])
wc = [k for k, v in got.items() if "File:Wheal Coates engine house.jpg" in v]
ok("Wheal Coates got only the photo whose title names it", wc and got[wc[0]] == ["File:Wheal Coates engine house.jpg"],
   got.get(wc[0]) if wc else got)
ok("nearby photos that do not name the site were never downloaded",
   not any(e[2] in ("/img/102.jpg", "/img/103.jpg") for e in first_requests))
ok("Welsh site matched despite the accent (Bran / Bran with circumflex)",
   any("Castell Dinas Bran" in t for v in got.values() for t in v))
from urllib.parse import urlparse as _up, parse_qs as _pq
_api = [_pq(_up(e[2]).query) for e in first_requests if e[2].startswith("/w/api.php")]
_credit_ids = {int(x) for q in _api if q.get("prop") == ["imageinfo"] for x in q["pageids"][0].split("|")}
_title_calls = sum(1 for q in _api if "prop" not in q)
ok("titles asked first; authors and licences only for photos that name their site",
   _title_calls >= 1 and _credit_ids and not (_credit_ids & {102, 103}), sorted(_credit_ids))
ok("unnamed sites get no photos", all(sid in {row[0] for row in L if row[1]} for sid in got))
ok("Laxey Mine got its photo", any("Laxey" in t for v in got.values() for t in v))
authors = {row[2] for row in man}
ok("credits cleaned: hidden duplicate text dropped, &amp; decoded", "Unknown author" in authors and "A & B" in authors
   and not any("Unknown authorUnknown" in a or "&amp;" in a for a in authors), sorted(authors))
ok("a second copy refuses to run while one is going", r_lock.returncode == 2, (r_lock.returncode, r_lock.stdout.strip()[-120:]))
ok("a re-run downloads nothing at all", len(rerun_requests) == 0, "%d requests" % len(rerun_requests))
print("\nfirst run: %d requests in %.0fs" % (len(first_requests), time.time() - t0))
srv.shutdown()
shutil.rmtree(HERE, ignore_errors=True)
sys.exit(1 if FAILS else 0)
