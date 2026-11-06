"""
extract-osm.py on a small OpenStreetMap file with known contents: which tags
count as a site, Welsh names categorised correctly, unnamed sites kept, and the
middle of a closed area worked out without counting its first corner twice.

    python tests/test_extract.py           (needs: pip install osmium)
"""
import os, sys, subprocess, tempfile, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sample.osm.pbf")
OUT = tempfile.mkdtemp(prefix="wildfind-extract-")

FAILS = []
def ok(label, cond, extra=""):
    if not cond: FAILS.append(label)
    print(("PASS  " if cond else "FAIL  ") + label + (("   " + str(extra)) if extra else ""))

r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "extract-osm.py"), FIXTURE, OUT],
                   capture_output=True, text=True)
print(r.stdout.strip())
ok("runs cleanly", r.returncode == 0, r.stderr.strip()[-300:])

def rows(name):
    p = os.path.join(OUT, name)
    return [l.rstrip("\n").split("\t") for l in open(p, encoding="utf-8")] if os.path.exists(p) else []

sites = {r[0]: r for r in rows("sites.tsv")}
places = rows("places.tsv")

ok("exactly 6 sites", len(sites) == 6, sorted(sites))
ok("4 of them named", sum(1 for r in sites.values() if r[1]) == 4)
ok("unnamed sites kept", sites.get("n1002", [""] * 5)[1] == "" and "w5002" in sites)
ok("mine shaft is a mine", sites.get("n1001", [""] * 5)[4] == "mine")
ok("Welsh chapel (Capel) is religious", sites.get("n1005", [""] * 5)[4] == "religious")
ok("Castell Dinas Bran is a castle, not a fort", sites.get("n1006", [""] * 5)[4] == "castle")
ok("Welsh name with a circumflex survives", sites.get("n1006", [""] * 2)[1] == "Castell Dinas Brân")
ok("Hen Felin (old mill) is industrial", sites.get("w5001", [""] * 5)[4] == "industrial")
ok("closed area centred without double-counting its first corner",
   sites.get("w5001", ["", "", "0", "0"])[2:4] == ["53.101000", "-1.598000"], sites.get("w5001"))
ok("one place (Sheffield, a city)", places == [["Sheffield", "53.38110", "-1.47010", "city"]], places)

shutil.rmtree(OUT, ignore_errors=True)
sys.exit(1 if FAILS else 0)
