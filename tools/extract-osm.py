#!/usr/bin/env python3
"""
WildFind - pull abandoned places out of an OpenStreetMap extract.

Reads a .osm.pbf downloaded from Geofabrik and appends to two tab separated
files that the rest of the pipeline turns into the app's data:

    sites.tsv    id, name, lat, lon, category, description
    places.tsv   name, lat, lon, place type

Usage:
    python extract-osm.py england-latest.osm.pbf out_dir

Why two passes
--------------
Ways have no position of their own, only a list of member nodes. The obvious
way to resolve those is osmium's node location index, but that holds every
node in the file: England alone has well over a hundred million, which runs
to gigabytes of memory.

Only a tiny fraction of nodes matter here - the ones belonging to the few
tens of thousands of ways that actually match. So pass one notes which node
ids are wanted, pass two collects just those, and memory stays in the tens
of megabytes. Reading the file twice is far cheaper than indexing all of it.

Why it is fast
--------------
The UK file holds roughly 250 million nodes, nearly all of them untagged
corners of roads and buildings. Handing each one to Python costs about an
hour. Instead osmium's own filters, which run in C++, discard everything
that cannot possibly match before Python sees it: exact tags in pass one,
exact node ids in pass two. Python still applies is_site() to everything
that gets through, so the filters only ever skip, never decide.
"""

import sys, os, re, unicodedata

try:
    import osmium
except ImportError:
    sys.exit("osmium is not installed. Run:  pip install osmium")

# ---------------------------------------------------------------- matching

def is_site(t):
    """True when these tags describe somewhere abandoned, ruined or disused."""
    if t.get('historic') in ('ruins', 'mine', 'mine_shaft', 'fort', 'aircraft_wreck'):
        return True
    if t.get('ruins') == 'yes':                      return True
    if t.get('building') == 'ruins':                 return True
    if t.get('man_made') in ('mineshaft', 'adit'):   return True
    if t.get('military') == 'bunker':                return True
    if 'abandoned:railway' in t:                     return True
    if t.get('disused:railway') == 'station':        return True
    return False

PLACE_TYPES = ('city', 'town', 'village')

# Exact tags that can make is_site() true, plus the place tags. Kept next to
# is_site() so the two cannot drift: anything is_site() accepts must appear
# here (or under ANY_VALUE_KEYS) or the C++ filter would hide it.
SITE_TAGS = (
    [('historic', v) for v in ('ruins', 'mine', 'mine_shaft', 'fort', 'aircraft_wreck')] +
    [('ruins', 'yes'), ('building', 'ruins'),
     ('man_made', 'mineshaft'), ('man_made', 'adit'),
     ('military', 'bunker'), ('disused:railway', 'station')] +
    [('place', v) for v in PLACE_TYPES]
)
# Keys that qualify whatever their value, so they need a key filter instead.
ANY_VALUE_KEYS = ('abandoned:railway',)

# Order must match KINDS in fetch.py and WF_KINDS in the app.
KINDS = ["mine","industrial","military","railway","religious",
         "castle","agrarian","folly","ruins"]

def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

# Words and phrases that say what a site is, each with a role:
#   T  a kind of site: "Chapel", "Lime Kiln", "Milecastle"
#   O  some other kind of building: "House", "Hall", "Pier" - other ruins,
#      and a keyword before it is only part of its name ("Castle House")
#   P  part of something: "Tower", "Chimney", "Turret" - takes the kind of a
#      keyword before it ("Church Tower"), or its own when alone
# English puts the head noun last, so the last keyword decides. Matching is
# on whole words, so Gosforth is not a fort and Newcastle not a castle.
L = {}
def add(kind, role, *words):
    for w in words:
        L[tuple(w.split())] = (kind, role)

add('mine', 'T', 'quarry', 'colliery', 'mine', 'pit', 'shaft', 'mineshaft', 'adit',
    'level', 'wheal', 'consols', 'workings', 'tin workings', 'count house',
    'dressing floor', 'dressing floors', 'buddle', 'pit head', 'pithead',
    'bell pit', 'drift mine', 'hush', 'ore works', 'ore house', 'ore hearth',
    'bouse team', 'crusher house', 'leadmine', 'coalmine', 'mine level', 'stamps',
    'mwyn', 'mwynglawdd', 'chwarel')
add('industrial', 'T', 'mill', 'works', 'kiln', 'furnace', 'foundry', 'forge',
    'engine', 'engine house', 'engines house', 'pumping station', 'pump house', 'pumping house',
    'boiler house', 'winding house', 'power station', 'water tower', 'shot tower',
    'cooling tower', 'gasholder', 'gas holder', 'smelter', 'smelt mill',
    'blowing house', 'brickyard', 'tannery', 'brewery', 'distillery', 'maltings',
    'malt house', 'warehouse', 'factory', 'ropewalk', 'saltpan', 'salt pan',
    'wheelpit', 'wheel pit', 'waterwheel', 'water wheel', 'drum house',
    'gunpowder', 'whaling station', 'ice works', 'wheel house', 'workshop',
    'manufactory', 'pumping', 'smelting')
add('military', 'T', 'fort', 'fortlet', 'fortress', 'hillfort', 'hill fort',
    'bunker', 'battery', 'pillbox', 'pill box', 'magazine', 'barracks', 'barrack',
    'aerodrome', 'airfield', 'air field', 'airbase', 'raf', 'blockhouse',
    'block house', 'gun emplacement', 'emplacement', 'gun site', 'redoubt',
    'roc post', 'observation post', 'royal observer corps', 'searchlight',
    'anti aircraft', 'tank trap', 'tank traps', 'dragons teeth', 'camp',
    'milecastle', 'signal station', 'radar station', 'radar', 'listening post',
    'decoy', 'spigot mortar', 'rifle range', 'firing range', 'air raid shelter',
    'crash site', 'martello', 'martello tower', 'drill hall', 'armoury', 'arsenal',
    'ordnance', 'torpedo', 'stop line', 'nissen hut', 'gun battery',
    'coastal defence', 'military', 'chain home',
    'fortification', 'fortifications', 'defences', 'defence', 'control tower',
    'gun tower')
add('railway', 'T', 'railway', 'rly', 'station', 'viaduct', 'tunnel', 'siding',
    'tramway', 'tramroad', 'wagonway', 'waggonway', 'plateway', 'halt', 'junction',
    'signal box', 'signalbox', 'goods shed', 'goods yard', 'goods',
    'engine shed', 'loco shed', 'turntable', 'level crossing', 'branch',
    'branch line', 'line', 'trackbed', 'funicular', 'walkway', 'trailway', 'greenway', 'railway lines', 'railway bridge',
    'rail bridge', 'station house')
add('religious', 'T', 'church', 'chapel', 'abbey', 'priory', 'friary', 'nunnery',
    'kirk', 'oratory', 'cathedral', 'minster', 'monastery', 'convent',
    'preceptory', 'commandery', 'hermitage', 'chantry', 'churchyard', 'kirkyard',
    'graveyard', 'burial ground', 'cemetery', 'meeting house', 'tabernacle',
    'synagogue', 'chapel of ease', 'holy well', 'church hall', 'mission hall',
    'mission church', 'chapter house', 'cloister', 'cloisters', 'chuch', 'round tower',
    'greyfriars', 'blackfriars', 'whitefriars', 'friars')
add('castle', 'T', 'castle', 'broch', 'keep', 'tower house', 'pele tower',
    'peel tower', 'pele', 'peel house', 'bastle', 'bastle house', 'fortified house',
    'fortified manor', 'motte', 'motte and bailey', 'bailey', 'ringwork',
    'donjon', 'barmkin', 'fortalice', 'curtain wall', 'barbican', 'tower keep')
add('agrarian', 'T', 'barn', 'farm', 'farmhouse', 'farm house', 'farmstead',
    'farm buildings', 'byre', 'steading', 'cottage', 'croft', 'crofthouse',
    'croft house', 'bothy', 'shieling', 'sheiling', 'shiel', 'dairy', 'dovecote',
    'dovecot', 'doocot', 'columbarium', 'pigeon house', 'pigsty', 'pig sty',
    'stable', 'granary', 'cart shed', 'linhay', 'longhouse', 'long house',
    'lathe', 'laithe', 'shippon', 'cowshed', 'cow shed', 'cow house', 'sheepfold',
    'sheep fold', 'pinfold', 'homestead', 'grange', 'mains', 'stackyard',
    'farmyard', 'cart house', 'fferm')
add('folly', 'T', 'folly', 'grotto', 'obelisk', 'mausoleum', 'icehouse',
    'ice house', 'temple', 'summer house', 'summerhouse', 'banqueting house',
    'eyecatcher', 'eye catcher', 'sham castle', 'sham ruin', 'monument',
    'column', 'gazebo', 'belvedere', 'pagoda', 'shell house', 'pyramid')
add(None, 'O', 'house', 'hall', 'manor', 'manor house', 'mansion', 'palace',
    'villa', 'lodge', 'hospital', 'asylum', 'sanatorium', 'school', 'schoolhouse',
    'college', 'hotel', 'inn', 'pub', 'tavern', 'workhouse', 'almshouse',
    'almshouses', 'prison', 'gaol', 'jail', 'courthouse', 'toll house',
    'tollhouse', 'lighthouse', 'boathouse', 'boat house', 'bath house',
    'bathhouse', 'baths', 'cinema', 'theatre', 'post office', 'coach house',
    'wash house', 'bakehouse', 'bake house', 'cairn', 'stone circle', 'settlement',
    'hut circle', 'roundhouse', 'round house', 'tomb', 'barrow', 'burial chamber',
    'cromlech', 'dolmen', 'standing stone', 'henge', 'souterrain', 'crannog',
    'village', 'holiday camp', 'bridge', 'lord line', 'clock tower', 'hut',
    'towers', 'township', 'lifeboat station', 'police station', 'fire station',
    'coastguard station', 'coastguard lookout', 'weather station',
    'research station', 'filling station', 'petrol station', 'service station',
    'fishing station', 'telegraph station', 'cable station', 'pier', 'jetty',
    'slipway', 'quay', 'harbour', 'dock', 'wharf', 'laboratory')
add('castle', 'P', 'tower')            # alone: castle in the north, folly south
add('military', 'P', 'turret', 'rampart')
add('industrial', 'P', 'chimney', 'flue')
add('railway', 'P', 'incline', 'platform')
add('religious', 'P', 'nave', 'chancel', 'spire', 'steeple', 'undercroft')

# Welsh and Gaelic put the noun first: only the leading word counts.
LEAD = {}
def lead(kind, role, *words):
    for w in words:
        LEAD[w] = (kind, role)
lead('castle', 'T', 'castell', 'gastell', 'caisteal', 'dun')
lead('military', 'T', 'caer', 'gaer', 'bryngaer', 'dinas')
lead('religious', 'T', 'eglwys', 'capel', 'gapel', 'abaty', 'mynachlog',
     'cille', 'cill', 'teampull', 'eaglais')
lead('industrial', 'T', 'melin', 'felin', 'odyn', 'gwaith', 'ffatri', 'muileann')
lead('agrarian', 'T', 'ysgubor', 'sgubor', 'beudy', 'fferm', 'bwthyn', 'hafod',
     'hafoty', 'hendre', 'taigh', 'tigh', 'both', 'bothan', 'airigh')
lead('railway', 'T', 'gorsaf', 'traphont', 'rheilffordd', 'twnnel')
lead('castle', 'P', 'twr')
lead(None, 'O', 'plas', 'ty', 'neuadd', 'llys')
LEAD_SKIP = {'y', 'yr', 'r', 'an', 'am', 'na', 'nam', 'nan', 'hen', 'the', 'old'}
WELSH_COMBO = {('gwaith', 'glo'): 'mine', ('pwll', 'glo'): 'mine'}

SAINT = {'st', 'saint', 'saints', 'sant', 'santes', 'holy', 'ss'}
MAXP = 4
NO_PLURAL = {'line'}          # "Hilsea Lines" are fortifications

GENERIC = {'ruin', 'ruins', 'remains', 'foundations', 'site', 'remnant', 'remnants',
           'footings', 'traces', 'ruined', 'derelict', 'disused', 'former'}

def lookup(w):
    """Exact word, then plural, then compound endings (windmill, brickworks)."""
    if w in GENERIC or w in ('earthwork', 'earthworks', 'outworks'):
        return None
    for c in (w, w[:-3] + 'y' if w.endswith('ies') else None,
              w[:-2] if w.endswith('es') else None, w[:-1] if w.endswith('s') else None):
        if c and (c,) in L and not (c != w and c in NO_PLURAL):
            return L[(c,)]
    if len(w) > 5 and w.endswith(('mill', 'mills')):
        return ('industrial', 'T')
    if len(w) > 6 and w.endswith('works'):
        return ('industrial', 'T')
    if len(w) > 5 and w.endswith(('kiln', 'kilns')):
        return ('industrial', 'T')
    for end in ('house', 'houses'):        # farmhouse, pumphouse; not Woodhouse
        if len(w) > len(end) + 2 and w.endswith(end):
            return L.get((w[:-len(end)], 'house'))
    return None

def matches(words):
    """Greedy longest phrases, left to right: (index, key, kind, role)."""
    out, i = [], 0
    while i < len(words):
        for n in range(min(MAXP, len(words) - i), 0, -1):
            key = tuple(words[i:i + n])
            if n == 1:
                hit = lookup(key[0])
            else:
                hit = L.get(key)
                last = key[-1]
                if not hit and last.endswith('s') and key[:-1] + (last[:-1],) in L:
                    hit = L[key[:-1] + (last[:-1],)]
            if hit:
                out.append((i, key, hit[0], hit[1]))
                i += n
                break
        else:
            i += 1
    # "Radar Station, Tx/Rx station": the plain "station" is not a railway
    if any(len(k) > 1 and k[-1] == 'station' for _, k, _, _ in out):
        out = [m for m in out if m[1] != ('station',)]
    return out

def tower_default(lat):
    """A tower on its own: a pele or tower house in the north, Scotland and
       Northern Ireland; further south, more often a folly."""
    return 'castle' if lat is not None and lat >= 54.0 else 'folly'

def head_of(words, lat):
    """Kind from English word order: the last keyword is the head noun, and
       a part (wall, tower, chimney) takes the kind of the word before it."""
    ms = matches(words)
    useful = [m for m in ms if m[3] != 'P' or m[2]]   # "Tower Gate": the tower
    if not useful:
        return None, None, ms
    last = useful[-1]
    if last[3] == 'P':
        before = [m for m in useful[:-1] if m[3] in ('T', 'O')]
        if before and before[-1][3] == 'T':
            return before[-1][2], 'type', ms
        k = last[2]
        if last[1] == ('tower',):
            k = 'religious' if any(w in SAINT for w in words) else tower_default(lat)
        return k, 'weak', ms
    if last[3] == 'O':
        return 'ruins', 'weak', ms
    return last[2], 'type', ms

def name_kind(name, lat=None):
    """(kind, strength). 'type' is a definite statement ("X Chapel"); 'weak'
       ("St Mary's", "Tower") only counts when the tags say nothing."""
    words = fold(name).split()
    if not words:
        return None, None
    tail = fold(name.rsplit(',', 1)[1]).split() if ',' in name else []
    if len(tail) == 1:                  # Canmore style: "Gallanach, dun"
        hit = lookup(tail[0])
        if tail[0] == 'dun':
            return 'castle', 'type'
        if hit and hit[1] == 'T':
            return hit[0], 'type'
    j = 0
    while j < len(words) - 1 and words[j] in LEAD_SKIP:
        j += 1
    lw = LEAD.get(words[j])
    if j + 1 < len(words) and (words[j], words[j + 1]) in WELSH_COMBO:
        lw = (WELSH_COMBO[(words[j], words[j + 1])], 'T')
    # "Chapel of St Anne's Hospital": the noun before "of" is the head
    k, st, ms = None, None, []
    if 'of' in words[1:-1]:
        i = words.index('of', 1)
        kl, sl, ml = head_of(words[:i], lat)
        kr, sr, mr = head_of(words[i + 1:], lat)
        ms = ml + mr
        if sr == 'type':                       # Mains of Lesmoir Mill
            k, st = kr, sr
        elif sl == 'type' or (ml and ml[-1][3] == 'O'):
            k, st = kl, sl
        elif kr:
            k, st = kr, sr
        else:
            k, st = kl, sl
    else:
        k, st, ms = head_of(words, lat)
    mine_word = any(m[2] == 'mine' and m[3] == 'T' for m in ms)
    if words[-1] == 'dun' and len(words) > 1 and not ms:   # "Largiemore Dun"
        k, st = 'castle', 'type'
    if words[j] == 'fort' and len(words) > j + 1 and k not in ('railway', 'religious', 'castle'):
        k, st = 'military', 'type'
    if mine_word and (k in ('industrial', 'ruins') or (k == 'military' and
            any(m[1][0] in ('barrack', 'barracks', 'magazine') for m in ms))):
        return 'mine', 'type'
    if st == 'type' or (k == 'ruins' and ms and ms[-1][3] == 'O'):
        return k, st
    if lw and lw[1] == 'T':
        return lw[0], 'type'
    if lw and lw[1] == 'P' and not k:            # Twr
        saint = any(w in SAINT for w in words)
        return ('religious' if saint else tower_default(lat)), 'weak'
    if k:
        return k, 'weak'
    if any(w in SAINT for w in words) or ' our lady' in ' ' + ' '.join(words):
        return 'religious', 'weak'
    if lw:
        return 'ruins', 'weak'
    if re.match(r'(llan|kil)(?!n)', words[j]):    # Llan-, Kil-: a church once
        return 'religious', 'weak'
    return None, None

def decide(name, tagkinds, lat=None):
    """tagkinds: what the OSM tags imply. Mine and railway tags are definite
       (a "Colliery Branch" is a railway). A castle in the name beats a fort
       tag, as many hillforts are called "X Castle"; military tags beat any
       other name, so "Rhydymwyn Valley Works" stays military."""
    if 'mine' in tagkinds:
        return 'mine'
    if 'railway' in tagkinds:
        return 'railway'
    if 'castle' in tagkinds:
        return 'castle'
    k, st = name_kind(name, lat)
    if st == 'type' and k == 'castle':
        return k
    if 'military' in tagkinds:
        return 'military'
    return k or 'ruins'


def tag_kinds(t):
    h = t.get('historic')
    out = set()
    if h in ('mine', 'mine_shaft') or t.get('man_made') in ('mineshaft', 'adit'):
        out.add('mine')
    if 'abandoned:railway' in t or 'disused:railway' in t:
        out.add('railway')
    if h == 'castle':
        out.add('castle')
    if 'military' in t or h in ('fort', 'aircraft_wreck'):
        out.add('military')
    return out

def categorise(name, t, lat=None):
    return decide(name, tag_kinds(t), lat)


def clean(s):
    return re.sub(r'[\t\r\n]+', ' ', s or '').strip()

# ---------------------------------------------------------------- pass one

class ScanPass:
    """Nodes are written straight out. Matching ways are held back until
       pass two has found where their member nodes are."""
    def __init__(self, sites_out, places_out):
        self.seen = set()        # an object can come through both filtered reads
        self.sites = sites_out
        self.places = places_out
        self.pending = []        # (way_id, tags, [node ids])
        self.wanted = set()      # node ids those ways need
        self.n_sites = 0
        self.n_named = 0
        self.n_places = 0
        self.n_rel = 0

    def emit(self, oid, tags, lat, lon):
        name = clean(tags.get('name'))
        self.sites.write("%s\t%s\t%.6f\t%.6f\t%s\t%s\n" % (
            oid, name, lat, lon, categorise(name, tags, lat),
            clean(tags.get('description') or tags.get('inscription'))))
        self.n_sites += 1
        if name:
            self.n_named += 1

    def feed(self, o):
        key = (o.type_str(), o.id)
        if key in self.seen:
            return
        self.seen.add(key)
        if o.is_node():
            self.node(o)
        elif o.is_way():
            self.way(o)
        elif o.is_relation():
            self.relation(o)

    def node(self, n):
        t = dict(n.tags)
        if not t:
            return
        if t.get('place') in PLACE_TYPES and t.get('name'):
            self.places.write("%s\t%.5f\t%.5f\t%s\n"
                              % (clean(t['name']), n.location.lat, n.location.lon, t['place']))
            self.n_places += 1
        if is_site(t):
            self.emit("n%d" % n.id, t, n.location.lat, n.location.lon)

    def way(self, w):
        t = dict(w.tags)
        if not is_site(t):
            return
        refs = [nd.ref for nd in w.nodes]
        if len(refs) > 2 and refs[0] == refs[-1]:
            refs = refs[:-1]     # closed ring: drop the repeated corner
        if not refs:
            return
        self.pending.append((w.id, t, refs))
        self.wanted.update(refs)

    def relation(self, r):
        # Relations are a rounding error here (87 of 26,814 historic=ruins in
        # the UK) and resolving them means another level of indirection.
        if is_site(dict(r.tags)):
            self.n_rel += 1

# ---------------------------------------------------------------- pass two

def locate(pbf, wanted):
    """Positions of just the wanted nodes. IdFilter does the picking in C++."""
    loc = {}
    fp = osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(wanted))
    for n in fp:
        loc[n.id] = (n.location.lat, n.location.lon)
    return loc

# ---------------------------------------------------------------- driver

def main():
    if len(sys.argv) < 3:
        sys.exit("usage: python extract-osm.py <file.osm.pbf> <out_dir>")
    pbf, out_dir = sys.argv[1], sys.argv[2]
    if not os.path.exists(pbf):
        sys.exit("no such file: " + pbf)
    os.makedirs(out_dir, exist_ok=True)

    sp = os.path.join(out_dir, "sites.tsv")
    pp = os.path.join(out_dir, "places.tsv")
    size_mb = os.path.getsize(pbf) / (1024 * 1024)
    print("reading %s (%.0f MB)" % (os.path.basename(pbf), size_mb), flush=True)

    nogeom = 0
    with open(sp, "a", encoding="utf-8", newline="") as sf, \
         open(pp, "a", encoding="utf-8", newline="") as pf:

        print("  pass 1 of 2: finding sites", flush=True)
        scan = ScanPass(sf, pf)
        for o in osmium.FileProcessor(pbf).with_filter(osmium.filter.TagFilter(*SITE_TAGS)):
            scan.feed(o)
        for o in osmium.FileProcessor(pbf).with_filter(osmium.filter.KeyFilter(*ANY_VALUE_KEYS)):
            scan.feed(o)

        if scan.pending:
            print("  pass 2 of 2: locating %d areas (%d node positions needed)"
                  % (len(scan.pending), len(scan.wanted)), flush=True)
            coords = locate(pbf, scan.wanted)

            for wid, tags, refs in scan.pending:
                la = lo = 0.0
                k = 0
                for r in refs:
                    c = coords.get(r)
                    if c:
                        la += c[0]
                        lo += c[1]
                        k += 1
                if k == 0:
                    nogeom += 1
                    continue
                scan.emit("w%d" % wid, tags, la / k, lo / k)

    print("  sites   %d (%d named)" % (scan.n_sites, scan.n_named))
    print("  places  %d" % scan.n_places)
    if nogeom:
        print("  %d areas skipped, their nodes lie outside this extract" % nogeom)
    if scan.n_rel:
        print("  %d matching relations skipped (multipolygons, not supported)" % scan.n_rel)

if __name__ == "__main__":
    main()
