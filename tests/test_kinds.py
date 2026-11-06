"""
How extract-osm.py sorts sites into kinds, checked on names taken from the
real UK data, including every kind of mistake the first full run made
(Gosforth Chapel filed as a fort, lime kilns as churches, and so on).

    python tests/test_kinds.py           (needs: pip install osmium)
"""
import os, sys, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("extract", os.path.join(ROOT, "tools", "extract-osm.py"))
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)

RUINS = {'historic': 'ruins'}
STATION = {'disused:railway': 'station'}
CASES = [
    # whole words only
    ("Milecastle 37", RUINS, 55.0, "military"),
    ("Newcastle-under-Lyme Branch", RUINS, 53.0, "railway"),
    ("North Gosforth Chapel", RUINS, 55.0, "religious"),
    ("Fortrose Cathedral", RUINS, 57.6, "religious"),
    ("Shaftesbury Abbey", RUINS, 51.0, "religious"),
    ("Lime Kiln", RUINS, 54.2, "industrial"),
    ("Allanbank", RUINS, 55.8, "ruins"),
    ("Site of Manor House (Earthworks)", RUINS, 52.0, "ruins"),
    # the last keyword is the head noun
    ("Castle House", RUINS, 52.0, "ruins"),
    ("Castlebergh Barn", RUINS, 54.1, "agrarian"),
    ("Covington Tower Dovecote", RUINS, 55.6, "agrarian"),
    ("Castle Point Lime Kilns", RUINS, 54.0, "industrial"),
    ("The Castle Folly", RUINS, 52.0, "folly"),
    ("Formby Power Station", RUINS, 53.5, "industrial"),
    ("Formby Lifeboat Station", RUINS, 53.5, "ruins"),
    ("Whale Head Chain Home Radar Station, Remote Reserve, Tx/Rx station", RUINS, 59.3, "military"),
    ("Chapel of St Anne's Hospital", RUINS, 52.0, "religious"),
    ("Gatehouse of Castle Acre Priory", RUINS, 52.7, "religious"),
    ("Mains of Lesmoir Mill Dam", RUINS, 57.3, "industrial"),
    ("Fort Grange", RUINS, 50.8, "military"),
    ("Hilsea Lines and Ramparts", RUINS, 50.8, "military"),
    ("Old Windmill", RUINS, 52.0, "industrial"),
    ("Stoneacre lathe", RUINS, 54.0, "agrarian"),
    # towers
    ("Tower House", RUINS, 54.5, "castle"),
    ("Burradon Tower", RUINS, 55.3, "castle"),
    ("Tower", RUINS, 51.5, "folly"),
    ("Water Tower", RUINS, 52.0, "industrial"),
    ("Martello Tower 47", RUINS, 51.0, "military"),
    ("St Michael's Tower", RUINS, 51.1, "religious"),
    ("Church Tower", RUINS, 52.0, "religious"),
    # mines
    ("Wheal Coates Engine House", RUINS, 50.3, "mine"),
    ("Cwm Caseg Quarry Barracks", RUINS, 53.2, "mine"),
    ("Coldberry High Level", RUINS, 54.6, "mine"),
    ("Esgair Mwyn", RUINS, 52.3, "mine"),
    # Welsh and Gaelic: the leading word
    ("Castell Dinas Brân", {'historic': 'fort'}, 53.0, "castle"),
    ("Hen Gapel", RUINS, 52.5, "religious"),
    ("Hafod Mansion", RUINS, 52.3, "ruins"),
    ("Dùn Telve", RUINS, 57.2, "castle"),
    ("Gallanach, dun", RUINS, 56.4, "castle"),
    ("Teampull Mhuire", RUINS, 57.6, "religious"),
    # tags
    ("Rufford Colliery Branch", {'abandoned:railway': 'rail'}, 53.1, "railway"),
    ("Wolverhampton Low Level", STATION, 52.6, "railway"),
    ("Spofforth", STATION, 53.9, "railway"),
    ("St Ives", STATION, 50.2, "railway"),
    ("Saint Mary", RUINS, 52.0, "religious"),
    ("Rhydymwyn Valley Works", {'military': 'yes', 'historic': 'ruins'}, 53.2, "military"),
    ("", {'man_made': 'mineshaft'}, 53.0, "mine"),
    ("", RUINS, 53.0, "ruins"),
]

FAILS = []
for name, tags, lat, want in CASES:
    got = ex.categorise(name, tags, lat)
    good = got == want
    if not good:
        FAILS.append(name)
    print(("PASS  " if good else "FAIL  ") + "%-45s %s" % ((name or "(unnamed)")[:45],
          want if good else "wanted %s, got %s" % (want, got)))

sys.exit(1 if FAILS else 0)
