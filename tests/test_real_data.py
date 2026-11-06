"""
Real samples captured from the live services on 28 Oct 2026, run through the
actual parsing code: rows from Commons' geo_tags dump and an answer from the
Commons API in exactly the form photos.py asks. Plus disk-space handling and
refusing to resume a partial download of an older version of a file.

    python tests/test_real_data.py         (no extra packages needed)
"""
import sys, os, gzip, json, shutil, subprocess, types, importlib.util, tempfile
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import photos
WORK = tempfile.mkdtemp(prefix="wildfind-real-")
FAILS = []
def ok(label, cond, extra=""):
    if not cond: FAILS.append(label)
    print(("PASS  " if cond else "FAIL  ") + label + (("   " + str(extra)) if extra else ""))

# 1. real rows from the live dump, laid out exactly as the dump file is
real = """/*M!999999\\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.18-MariaDB, for debian-linux-gnu (x86_64)
--
INSERT INTO `geo_tags` VALUES
(53659,18844067,'earth',1,7.24165010,0.00000000,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(2358062,25422664,'earth',1,50.82806778,-0.47367200,1000,'camera',NULL,NULL,NULL,NULL,NULL),
(83914485,13459177,'earth',1,53.02675629,-2.53014708,1000,'camera',NULL,NULL,NULL,NULL,NULL),
(150078858,17181406,'earth',1,54.96307373,-1.61617041,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(152607481,27955036,'earth',1,54.15416718,-4.48116684,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(153534512,27944078,'earth',1,54.15416718,-4.48116684,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(153534850,28117967,'earth',1,54.15416718,-4.48116684,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(153535937,29386017,'earth',1,54.16223145,-4.48951674,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(154177636,17660619,'earth',1,52.63990784,-2.49440289,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(154616451,28897920,'earth',1,54.15410614,-4.48093605,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
(154698358,19444976,'earth',1,51.81466675,-0.80650002,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
"""
dump = os.path.join(WORK, "geo.sql.gz")
with gzip.open(dump, "wt", encoding="utf-8") as f: f.write(real)
sites = [("n1", "Douglas harbour", 54.1545, -4.4810), ("n2", "", 54.9631, -1.6162), ("n3", "", 57.0, -3.0)]
grid = photos.parse_geo_tags(dump, photos.wanted_cells(sites))
c1 = photos.candidates(grid, 54.1545, -4.4810, 600, 6)
ok("real dump rows parse; Douglas site gets the 4 real files within 600 m, not the one 1 km off", sorted(c1) == [27944078, 27955036, 28117967, 28897920], c1)
ok("Newcastle site finds its real file", photos.candidates(grid, 54.9631, -1.6162, 600, 3) == [17181406])
ok("Equator row and far sites ignored", photos.candidates(grid, 57.0, -3.0, 600, 3) == [])

# 2. the live API's answer (pages copied verbatim from the real response) through lookup()
resp = {"batchcomplete": "", "query": {"pages": {
 "25422664": {"pageid": 25422664, "missing": ""},
 "17181406": {"pageid": 17181406, "ns": 6, "title": "File:Queen Elizabeth II Metro Bridge-CRW 2414.jpg", "imagerepository": "local", "imageinfo": [{
   "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/d/df/Queen_Elizabeth_II_Metro_Bridge-CRW_2414.jpg/500px-Queen_Elizabeth_II_Metro_Bridge-CRW_2414.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail",
   "thumbwidth": 500, "thumbheight": 325,
   "url": "https://upload.wikimedia.org/wikipedia/commons/d/df/Queen_Elizabeth_II_Metro_Bridge-CRW_2414.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
   "extmetadata": {"Artist": {"value": "<a rel=\"nofollow\" class=\"external text\" href=\"https://www.flickr.com/people/73422480@N00\">Tom Page</a>", "source": "commons-desc-page"},
                   "LicenseShortName": {"value": "CC BY-SA 2.0", "source": "commons-desc-page", "hidden": ""}}, "mime": "image/jpeg"}]},
 "27944078": {"pageid": 27944078, "ns": 6, "title": "File:TSS Manxman in LMS Livery.jpg", "imagerepository": "local", "imageinfo": [{
   "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/1/1a/TSS_Manxman_in_LMS_Livery.jpg/500px-TSS_Manxman_in_LMS_Livery.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail",
   "extmetadata": {"Artist": {"value": "Unknown author<span style=\"display: none;\">Unknown author</span>", "source": "commons-desc-page"},
                   "LicenseShortName": {"value": "Public domain", "source": "commons-desc-page", "hidden": ""}}, "mime": "image/jpeg"}]},
 "17660619": {"pageid": 17660619, "ns": 6, "title": "File:William Ball.jpg", "imageinfo": [{
   "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a0/William_Ball.jpg/500px-William_Ball.jpg",
   "extmetadata": {"Artist": {"value": "<a href=\"//commons.wikimedia.org/wiki/User:Andy_Dingley\" title=\"User:Andy Dingley\">Andy Dingley</a>"},
                   "LicenseShortName": {"value": "Public domain"}}, "mime": "image/jpeg"}]}}}}
raw = gzip.compress(json.dumps(resp).encode())
class Fake(photos.Polite):
    def get(self, url, binary=False, tries=6):
        self.url = url; return gzip.decompress(raw)
fk = Fake(0.2)
info = fk.lookup([25422664, 17181406, 27944078, 17660619], 500)
ok("deleted file skipped, three real files read", sorted(info) == [17181406, 17660619, 27944078], sorted(info))
ok("image address is the new thumb.wikimedia.org 500 px copy", info[17181406]["url"].startswith("https://thumb.wikimedia.org/") and "/500px-" in info[17181406]["url"])
ok("credits read cleanly", (info[17181406]["author"], info[27944078]["author"], info[17660619]["author"]) == ("Tom Page", "Unknown author", "Andy Dingley"),
   [info[p]["author"] for p in (17181406, 27944078, 17660619)])
ok("licences read", info[17181406]["licence"] == "CC BY-SA 2.0" and info[27944078]["licence"] == "Public domain")
ok("request is the exact form tested live", "maxlag=5" in fk.url and "iiurlwidth=500" in fk.url and "pageids=25422664%7C17181406" in fk.url)

# 2b. the cheap titles-only query, on a real response shape (a deleted file
#     comes back marked "missing" and must be left out)
tresp = {"batchcomplete": "", "query": {"pages": {
  "25422664": {"pageid": 25422664, "missing": ""},
  "17181406": {"pageid": 17181406, "ns": 6, "title": "File:Queen Elizabeth II Metro Bridge-CRW 2414.jpg"},
  "27944078": {"pageid": 27944078, "ns": 6, "title": "File:TSS Manxman in LMS Livery.jpg"}}}}
class FakeT(photos.Polite):
    def get(self, url, binary=False, tries=6):
        self.url = url; return json.dumps(tresp).encode()
ft = FakeT(0.2)
tt = ft.titles([25422664, 17181406, 27944078])
ok("titles-only query: deleted file left out, titles read", tt == {17181406: "File:Queen Elizabeth II Metro Bridge-CRW 2414.jpg",
   27944078: "File:TSS Manxman in LMS Livery.jpg"}, tt)
ok("titles-only query asks for nothing else (no imageinfo, no credits)",
   "prop=" not in ft.url and "extmetadata" not in ft.url and "maxlag=5" in ft.url, ft.url)

# 3. disk space: the big download refuses up front rather than failing part way
spec = importlib.util.spec_from_file_location("fetch", os.path.join(TOOLS, "fetch.py")); fetch = importlib.util.module_from_spec(spec); spec.loader.exec_module(fetch)
fetch.remote_info = lambda url: (2277816883, '"abc"')
fetch.free_bytes = lambda p: 1.5 * 1024 ** 3
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    r = fetch.download("https://example.invalid/uk.pbf", os.path.join(WORK, "uk.osm.pbf"), "united-kingdom")
ok("download refuses cleanly when the drive is too full", r is False and "not enough disk space" in buf.getvalue(), buf.getvalue().strip().splitlines()[0])

# a .part left from an older version of the file (no version note) is not resumed
open(os.path.join(WORK, "uk.osm.pbf.part"), "wb").write(b"x" * 1000)
fetch.free_bytes = lambda p: None
calls = []
class Stop(Exception): pass
def fake_urlopen(req, timeout=0):
    calls.append(dict(req.header_items())); raise Stop()
fetch.urllib.request.urlopen = fake_urlopen
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf): fetch.download("https://example.invalid/uk.pbf", os.path.join(WORK, "uk.osm.pbf"), "united-kingdom")
except Stop: pass
ok("old partial file with no version note is started afresh, never resumed",
   "starting again" in buf.getvalue() and calls and "Range" not in calls[0], calls[:1])

# 4. a run stopped part way through a batch redoes it, so the manifest can hold
#    the same photo twice; the app must still list each photo once
root = os.path.join(WORK, "proj"); st = os.path.join(root, "tools", "state")
os.makedirs(st); os.makedirs(os.path.join(root, "data"))
for k in ("1", "2"):
    d = os.path.join(root, "photos", "45", "n12345"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, k + ".jpg"), "wb").write(b"\xff\xd8x")
with open(os.path.join(st, "photos.tsv"), "w", encoding="utf-8") as f:
    f.write("n12345\t1\tA\tCC BY-SA 2.0\tFile:One.jpg\n")
    f.write("n12345\t2\tB\tCC BY-SA 2.0\tFile:Two.jpg\n")
    f.write("n12345\t1\tA\tCC BY-SA 2.0\tFile:One.jpg\n")      # repeated after a restart
photos.write_outputs(st, root)
js = open(os.path.join(root, "data", "photos.js"), encoding="utf-8").read()
entry = js.split('"n12345":[')[1].split("]]")[0]
ok("a photo listed twice in the manifest appears once in the app", entry.count("[1,") == 1 and entry.count("[2,") == 1, entry)

# 5. after warnings the minimum gap rises, then eases back one step per calm
#    spell (two hours in real runs; a fraction of a second here)
import time as _t
pol = photos.Polite(0.2, relax_after=0.05)
pol._warned(); pol._warned()
ok("two warnings raise the minimum gap to 0.45 s", abs(pol.floor - 0.45) < 1e-9, pol.floor)
pol._relax()
ok("no easing before the calm spell is over", abs(pol.floor - 0.45) < 1e-9, pol.floor)
_t.sleep(0.06); pol._relax()
ok("one calm spell eases it one step, to 0.3 s", abs(pol.floor - 0.3) < 1e-9, pol.floor)
pol._relax()
ok("the next step needs a whole new calm spell", abs(pol.floor - 0.3) < 1e-9, pol.floor)
_t.sleep(0.03); pol._warned(); _t.sleep(0.03); pol._relax()
ok("a new warning raises it again and restarts the clock", abs(pol.floor - 0.45) < 1e-9, pol.floor)
for _ in range(4):
    _t.sleep(0.06); pol._relax()
ok("calm spells bring it back to 0.2 s and never below", pol.floor == 0.2, pol.floor)

# 6. Which photos name their site. Twelve real sites picked at random from the
#    first run on 30 Oct 2026, with the three photos each had then (all picked
#    for being nearby), and the real town/village positions involved. Each
#    photo was judged by eye; the rule must reject every clearly wrong one and
#    keep the clear hits.
REAL = [["n4762188756", "Quarter Collieries No4 pit", 55.742471, -4.026053, ["File:Pylons and speed limit signs approaching Quarter - geograph.org.uk - 2045484.jpg", "File:Crossroads at Quarter - geograph.org.uk - 1743580.jpg", "File:Limekilnburn Road, Quarter - geograph.org.uk - 6464184.jpg"]], ["n12095548786", "Brookmount", 54.519719, -6.124399, ["File:The Brookmount Road - geograph.org.uk - 1591639.jpg", "File:Brookmount Railway Bridge - geograph.org.uk - 1591616.jpg", "File:Brookmount Railway Bridge - geograph.org.uk - 1591626.jpg"]], ["w906120922", "Shipwreck", 50.887161, -1.295812, ["File:The M27 bridge over the River Hamble - geograph.org.uk - 3320472.jpg", "File:Underneath M27 motorway bridge over River Hamble - geograph.org.uk - 1375257.jpg", "File:M27 motorway bridge over River Hamble - geograph.org.uk - 1375267.jpg"]], ["w938174212", "Plague Cottage", 51.548909, -2.305198, ["File:The site of Plaque Cottage - geograph.org.uk - 8079194.jpg", "File:Grass airstrip near Badminton - geograph.org.uk - 486983.jpg", "File:Landing strip on Badminton Estate - geograph.org.uk - 8079197.jpg"]], ["n4530255701", "Eagle Crag Lead Mine", 54.519235, -2.992888, ["File:Old mine workings on Eagle Crag - geograph.org.uk - 1866563.jpg", "File:Eagle Crag - geograph.org.uk - 549443.jpg", "File:Grisedale path - geograph.org.uk - 1886496.jpg"]], ["n10956026820", "Barnweill Church", 55.53672, -4.528845, ["File:01632 Ruins Parish Church & BG, Barnweill, Craigie 002.jpg", "File:01632 Ruins Parish Church & BG, Barnweill, Craigie 001.jpg", "File:01632 Ruins Parish Church & BG, Barnweill, Craigie 003.jpg"]], ["w390207432", "Bosigran Smelting Works", 50.178535, -5.601054, ["File:Bosigran Smelting Works (13).jpg", "File:Bosigran Smelting Works (15).jpg", "File:Remains of tin processing works at Porthmeor - geograph.org.uk - 3022125.jpg"]], ["w827644782", "Pinner Chalk Mines", 51.602913, -0.390467, ["File:Montesole Playing Fields at Pinner Green - geograph.org.uk - 5700969.jpg", "File:Play area, Pinner Green - geograph.org.uk - 2881171.jpg", "File:An Iron Age Gold stater from GREATER LONDON Trinovantes Celtic Coin Index reference, 98.0396 (FindID 305850).jpg"]], ["w484714602", "The Bomb House", 50.883808, -0.214652, ["File:Building, Devil's Dyke - geograph.org.uk - 5861957.jpg", "File:View from Devil's Dyke across the Low Weald - geograph.org.uk - 7476275.jpg", "File:View through a window - geograph.org.uk - 7476266.jpg"]], ["n12693573109", "Kinclaven Castle", 56.524055, -3.370199, ["File:Kinclaven Castle (Ruins) - Inner Area - geograph.org.uk - 8003060.jpg", "File:Kinclaven Castle (geograph 7010775).jpg", "File:River Tay near Kinclaven - geograph.org.uk - 1644233.jpg"]], ["n208338600", "City Road", 51.529697, -0.097763, ["File:Central Street at the junction of City Road - geograph.org.uk - 7050976.jpg", "File:City Road - geograph-3885615-by-Mike-Quinn.jpg", "File:City Road - geograph-3885623-by-Mike-Quinn.jpg"]], ["n3125511071", "WW2 Latrine Foundations", 50.588554, -3.647434, ["File:Dunley Lane - geograph.org.uk - 2594995.jpg", "File:Lane to Dunley Cross - geograph.org.uk - 5844785.jpg", "File:The road past Dunley House - geograph.org.uk - 142346.jpg"]]]
PLACES = {"church": [[53.75503, -2.38852]], "eagle": [[53.1947, -0.68833]], "chalk": [[51.43211, 0.39936]], "quarter": [[55.73842, -4.0309]]}
for k, v in PLACES.items(): PLACES[k] = [tuple(x) for x in v]
kept = {}
for sid, name, la, lo, titles in REAL:
    w = photos.key_words(name, la, lo, PLACES)
    kept[name] = [t for t in titles if photos.is_match(t, w)]
wrong = {"Quarter Collieries No4 pit": 3, "Shipwreck": 3, "Pinner Chalk Mines": 3, "WW2 Latrine Foundations": 3}
ok("photos of the wrong thing rejected (Quarter village, M27 bridge, playing fields, lanes)",
   all(not kept[n] for n in wrong), {n: kept[n] for n in wrong if kept[n]})
ok("a village named in the site's name is ignored only when it is the local one",
   photos.key_words("Quarter Collieries No4 pit", 55.742471, -4.026053, PLACES) == ["collieries"]
   and "eagle" in photos.key_words("Eagle Crag Lead Mine", 54.519235, -2.992888, PLACES)
   and "church" in photos.key_words("Barnweill Church", 55.53672, -4.528845, PLACES)
   and "chalk" in photos.key_words("Pinner Chalk Mines", 51.602913, -0.390467, PLACES))
ok("airstrip and hill-path photos dropped, the site photos kept",
   kept["Plague Cottage"] == ["File:The site of Plaque Cottage - geograph.org.uk - 8079194.jpg"]
   and any("Old mine workings on Eagle Crag" in t for t in kept["Eagle Crag Lead Mine"])
   and not any("Grisedale" in t for t in kept["Eagle Crag Lead Mine"]), kept["Plague Cottage"])
ok("clear hits kept (Barnweill Church ruins, Bosigran Smelting Works, Kinclaven Castle)",
   len(kept["Barnweill Church"]) == 3 and len(kept["Bosigran Smelting Works"]) >= 2
   and sum("Kinclaven Castle" in t for t in kept["Kinclaven Castle"]) == 2)

# 7. photos downloaded before the rule are set aside, not deleted, and the app
#    stops listing them
proj = os.path.join(WORK, "aside"); st2 = os.path.join(proj, "tools", "state")
os.makedirs(st2); os.makedirs(os.path.join(proj, "data"))
rows = [("n1", "1", "File:Bosigran Smelting Works (13).jpg"), ("n1", "2", "File:Field gate.jpg"),
        ("n2", "1", "File:Crossroads at Quarter - geograph.org.uk - 1743580.jpg")]
for sid, k, t in rows:
    d = os.path.join(proj, "photos", sid[-2:], sid); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, k + ".jpg"), "wb").write(b"\xff\xd8x")
with open(os.path.join(st2, "photos.tsv"), "w", encoding="utf-8") as f:
    for sid, k, t in rows: f.write("%s\t%s\tA\tCC BY-SA 2.0\t%s\n" % (sid, k, t))
site_list = [("n1", "Bosigran Smelting Works", 50.178535, -5.601054), ("n2", "Quarter Collieries No4 pit", 55.742471, -4.026053)]
moved = photos.set_aside_offtopic(st2, proj, site_list, PLACES)
left = open(os.path.join(st2, "photos.tsv"), encoding="utf-8").read()
ok("off-topic photos moved to photos-removed, not deleted", moved == 2
   and os.path.exists(os.path.join(proj, "photos-removed", "n1"[-2:], "n1", "2.jpg"))
   and os.path.exists(os.path.join(proj, "photos-removed", "n2"[-2:], "n2", "1.jpg"))
   and os.path.exists(os.path.join(proj, "photos", "n1"[-2:], "n1", "1.jpg")), moved)
ok("and dropped from the manifest, so the app stops showing them",
   "Field gate" not in left and "Crossroads" not in left and "Smelting" in left)
ok("setting aside twice changes nothing", photos.set_aside_offtopic(st2, proj, site_list, PLACES) == 0)

shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILS else 0)
