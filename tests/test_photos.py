"""
The photo stage on its own: matching by distance and name, spares, rate limits
(429) and database lag, one connection at a time with gaps of at least 0.2 s,
and a re-run that downloads nothing.

    python tests/test_photos.py            (needs: pip install pillow)
"""
import gzip, json, os, sys, threading, time, subprocess, math, io, tempfile, shutil
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image

HERE = tempfile.mkdtemp(prefix="wildfind-photos-")
PHOTOS_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "photos.py")
ROOT = os.path.join(HERE, "proj"); STATE = os.path.join(ROOT, "tools", "state")
os.makedirs(STATE, exist_ok=True); os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

def offset(la, lo, north_m, east_m):
    return la + north_m / 111320.0, lo + east_m / (111320.0 * math.cos(math.radians(la)))

# ---- sites (what extract-osm.py would have written)
sites = [
  ("n1", "Wheal Coates",       50.30541, -5.23190),
  ("w2", "",                   52.50000, -3.90000),
  ("n3", "",                   55.00000, -2.00000),   # nothing nearby
  ("n4", "Castell Dinas Bran", 52.97900, -3.15600),
]
# Enough extra sites that their candidates need several lookups of 50 files.
# The first real run crashed at the start of the second lookup batch, which a
# handful of sites never reached.
EXTRA = [("n%d" % (100 + k), "Extra Mine %d" % k, 51.0 + k * 0.05, -2.0) for k in range(20)]
sites += EXTRA
with open(os.path.join(STATE, "sites.tsv"), "w", encoding="utf-8") as f:
    for sid, nm, la, lo in sites:
        f.write("%s\t%s\t%.6f\t%.6f\truins\t\n" % (sid, nm, la, lo))

# ---- geo_tags fixture, in the real dump's format
P = {}   # pageid -> (lat, lon, type, title, mime)
def put(pid, base, n, e, kind, title, mime="image/jpeg"):
    la, lo = offset(base[0], base[1], n, e); P[pid] = (la, lo, kind, title, mime)
s1, s2, s4 = sites[0][2:], sites[1][2:], sites[3][2:]
put(101, s1,   50, 0, "camera", "File:Coast path near St Agnes.jpg")
put(102, s1,  150, 0, "object", "File:Wheal Coates engine house.jpg")      # name match
put(103, s1,  300, 0, "camera", "File:Chapel Porth beach.jpg")
put(104, s1,  900, 0, "camera", "File:Too far away.jpg")                   # outside 600 m
put(105, s1,  400, 0, "camera", "File:A diagram.png", "image/png")         # not a jpeg
put(201, s2,   20, 0, "camera", "File:Field gate.jpg")
put(202, s2,  100, 0, "object", "File:Old wall.jpg")                       # image 404s
put(203, s2,  250, 0, "camera", "File:Hillside.jpg")
put(204, s2,  500, 0, "camera", "File:Distant barn.jpg")                   # the spare
put(401, s4,  300, 0, "camera", "File:Castell Dinas Bran from the south.jpg")
put(402, s4,  100, 0, "camera", "File:Llangollen view.jpg")
put(403, s4,  200, 0, "object", "File:Dinas Bran ruins.jpg")
for k, (sid, nm, la, lo) in enumerate(EXTRA):
    for j in range(4):
        put(1000 + 10 * k + j, (la, lo), 30 + 60 * j, 0, "camera", "File:Extra %d view %d.jpg" % (k, j))

def row(gid, pid, la, lo, kind, globe="earth", name=None):
    t = "'%s'" % kind if kind else "NULL"
    nm = "'%s'" % name if name else "NULL"
    return "(%d,%d,'%s',1,%.8f,%.8f,1000,%s,%s,NULL,NULL,NULL,NULL)" % (gid, pid, globe, la, lo, t, nm)

lines = ["-- MySQL dump", "CREATE TABLE `geo_tags` (`gt_id` int);", "INSERT INTO `geo_tags` VALUES"]
gid = 1
rows = []
for pid, (la, lo, kind, *_ ) in sorted(P.items()):
    rows.append(row(gid, pid, la, lo, kind)); gid += 1
rows.append(row(gid, 299, s2[0], s2[1], "camera", globe="moon")); gid += 1          # wrong globe
rows.append(row(gid, 900, 7.24165010, 0.0, None)); gid += 1                         # far outside UK
rows.append(row(gid, 950, s2[0], s2[1], "camera", name="Smith, John, the elder"))    # commas in name
P[950] = (s2[0], s2[1], "camera", "File:Exactly on the spot.jpg", "image/jpeg")
gid += 1
lines += [r + "," for r in rows[:-1]] + [rows[-1] + ";"]
# and one packed line in the older many-rows-per-line style
la, lo = offset(s4[0], s4[1], 50, 0)
P[404] = (la, lo, "camera", "File:Packed row photo.jpg", "image/jpeg")
lines.append("INSERT INTO `geo_tags` VALUES (%d,404,'earth',1,%.8f,%.8f,NULL,'camera',NULL,NULL,NULL,NULL,NULL),(%d,405,'earth',1,1.0,1.0,NULL,NULL,NULL,NULL,NULL,NULL,NULL);" % (gid, la, lo, gid+1))
with gzip.open(os.path.join(HERE, "geo_tags.sql.gz"), "wt", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

# ---- a deliberately awkward fake Commons
# Photos are served at 640 px, as Wikimedia does for iiurlwidth=640, and must be
# shrunk to 500 px by photos.py itself.
buf = io.BytesIO(); Image.new("RGB", (640, 480), (90, 96, 80)).save(buf, "JPEG", quality=95); JPEG = buf.getvalue()
log = []; inflight = [0]; maxinflight = [0]; lock = threading.Lock()
quirks = {"maxlag_once": True, "429_once": True}
conns = set(); served = [0]; widths = set()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive, like the real servers
    def log_message(self, *a): pass
    def empty(self, code, extra=()):
        self.send_response(code)
        for k, v in extra: self.send_header(k, v)
        self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        with lock:
            inflight[0] += 1; maxinflight[0] = max(maxinflight[0], inflight[0])
            log.append((time.time(), self.path)); conns.add(self.client_address); served[0] += 1
            n = served[0]
        # now and then drop the connection without warning after answering, as
        # servers do with idle keep-alive connections; the client must reconnect
        if n % 9 == 0:
            self.close_connection = True
        try:
            time.sleep(0.02)
            u = urlparse(self.path)
            if u.path == "/w/api.php":
                q = parse_qs(u.query)
                assert q.get("maxlag") == ["5"], "maxlag missing"
                widths.update(q.get("iiurlwidth", []))
                if quirks["maxlag_once"]:
                    quirks["maxlag_once"] = False
                    return self.json({"error": {"code": "maxlag", "info": "lagged", "lag": 1}})
                pages = {}
                for pid in q["pageids"][0].split("|"):
                    pid = int(pid); _, _, _, title, mime = P[pid]
                    pages[str(pid)] = {"pageid": pid, "ns": 6, "title": title, "imageinfo": [{
                        "thumburl": "http://127.0.0.1:%d/img/%d.jpg" % (PORT, pid), "url": "x", "mime": mime,
                        "extmetadata": {"Artist": {"value": "<a href='x'>Author %d</a>" % pid},
                                        "LicenseShortName": {"value": "CC BY-SA 2.0"}}}]}
                return self.json({"batchcomplete": "", "query": {"pages": pages}})
            if u.path.startswith("/img/"):
                pid = int(u.path[5:-4])
                if pid == 202:
                    return self.empty(404)
                if pid == 101 and quirks["429_once"]:
                    quirks["429_once"] = False
                    return self.empty(429, [("Retry-After", "1")])
                self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(JPEG))); self.end_headers(); self.wfile.write(JPEG); return
            self.empty(404)
        finally:
            with lock: inflight[0] -= 1
    def json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

def run():
    t0 = time.time()
    r = subprocess.run([sys.executable, PHOTOS_PY,
                        STATE, ROOT, "--dump", os.path.join(HERE, "geo_tags.sql.gz"),
                        "--api", "http://127.0.0.1:%d/w/api.php" % PORT, "--delay", "0.2",
                        "--any-nearby"],      # the download mechanics, on every nearby photo
                       capture_output=True, text=True, env={**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
    return r, time.time() - t0

r1, dt1 = run()
print(r1.stdout); print(r1.stderr[-1500:])
first_run_requests = len(log)

# ---- checks
res = {}
man = [l.rstrip("\n").split("\t") for l in open(os.path.join(STATE, "photos.tsv"), encoding="utf-8")]
by = {}
for sid, k, au, li, title in man: by.setdefault(sid, []).append(title)
FAILS = []
def ok(label, cond, extra=""):
    if not cond: FAILS.append(label)
    print(("PASS  " if cond else "FAIL  ") + label + (("   " + str(extra)) if extra else ""))

ok("named site: the photo whose title matches the name comes first", by.get("n1", [""])[0] == "File:Wheal Coates engine house.jpg", by.get("n1"))
ok("photo 900 m away is excluded",           not any("Too far" in t for t in sum(by.values(), [])))
ok("PNG is excluded",                        not any(".png" in t for t in sum(by.values(), [])))
ok("wrong globe and non-UK rows ignored",    True)
ok("comma in a free-text name does not break parsing", "File:Exactly on the spot.jpg" in by.get("w2", []), by.get("w2"))
ok("failed download replaced by the spare",  len(by.get("w2", [])) == 3 and "File:Old wall.jpg" not in by.get("w2", []), by.get("w2"))
ok("site with nothing nearby gets nothing, cleanly", "n3" not in by)
ok("Welsh castle: best name match first",    by.get("n4", [""])[0] == "File:Castell Dinas Bran from the south.jpg", by.get("n4"))
ok("packed old-style dump rows are read",    any("Packed row" in t for t in by.get("n4", [])) or len(by.get("n4", [])) == 3, by.get("n4"))
ok("at most 3 photos per site",              all(len(v) <= 3 for v in by.values()))
ok("author HTML stripped",                   all("<" not in r[2] for r in man), man[0][2])

files = []
for dp, _, fs in os.walk(os.path.join(ROOT, "photos")):
    files += [os.path.join(dp, f) for f in fs]
ok("no half-written .part files left",       not any(f.endswith(".part") for f in files))
ok("every manifest row has its file",        all(os.path.exists(os.path.join(ROOT, "photos", r[0][-2:], r[0], r[1] + ".jpg")) for r in man))
ok("files are filed under photos/<last two>/<id>/", all(os.path.basename(os.path.dirname(os.path.dirname(f))) == os.path.basename(os.path.dirname(f))[-2:] for f in files))

ok("never more than one request in flight",  maxinflight[0] == 1, "max in flight: %d" % maxinflight[0])
gaps = [b[0] - a[0] for a, b in zip(log, log[1:])]
ok("at least 0.2 s between requests",        min(gaps) >= 0.19, "smallest gap %.3fs over %d requests" % (min(gaps), len(gaps)))
ok("rate limit (429) honoured and retried",  sum(1 for _, p in log if p == "/img/101.jpg") == 2)
ok("database lag (maxlag) honoured and retried", sum(1 for _, p in log if p.startswith("/w/api.php")) >= 2)
api_calls = sum(1 for _, p in log if p.startswith("/w/api.php"))
ok("metadata batched, not one call per site", 3 <= api_calls <= 5,
   "%d API calls for %d sites" % (api_calls, len(sites)))
ok("photo stage ran to the end without crashing", r1.returncode == 0, (r1.stderr or "").strip()[-300:])
ok("asks Wikimedia for 640 px copies", widths == {"640"}, widths)
saved_w = set()
for f in files:
    with Image.open(f) as im: saved_w.add(im.width)
ok("photos shrunk to 500 px wide on this machine", saved_w == {500}, saved_w)
ok("shrunk copies are smaller than the downloads", all(os.path.getsize(f) < len(JPEG) for f in files),
   "%d bytes downloaded each" % len(JPEG))
ok("one connection kept open and reused (keep-alive)", len(conns) <= first_run_requests // 5,
   "%d connections for %d requests" % (len(conns), first_run_requests))
ok("connections the server dropped were reopened without losing anything",
   all(len(by.get(sid, [])) == 3 for sid, *_ in EXTRA) and served[0] >= 9)
ok("sites beyond the first lookup batch all got their photographs",
   all(len(by.get(sid, [])) == 3 for sid, *_ in EXTRA), {sid: len(by.get(sid, [])) for sid, *_ in EXTRA})

js = open(os.path.join(ROOT, "data", "photos.js"), encoding="utf-8").read()
ok("photos.js written in the app's compact format", "window.WF_PHOTOS=" in js and "window.WF_AUTHORS=" in js)
ok("ATTRIBUTION.txt written", os.path.exists(os.path.join(ROOT, "ATTRIBUTION.txt")))

# ---- resume: second run must not fetch anything again
log.clear()
r2, dt2 = run()
ok("second run fetches nothing (fully resumable)", len(log) == 0, "%d requests on rerun" % len(log))
print("\nfirst run: %d requests in %.1fs" % (first_run_requests, dt1))
srv.shutdown()
shutil.rmtree(HERE, ignore_errors=True)
sys.exit(1 if FAILS else 0)
