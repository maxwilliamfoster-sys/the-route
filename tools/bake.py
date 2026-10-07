"""Bake the Cambridge pixel-art base map + static data for THE ROUTE.

Reads OpenStreetMap extracts (data/raw/*.json, from Overpass) and the NaPTAN
stop list (data/raw/naptan050.csv) and writes everything the front end needs
into docs/:

    docs/tiles/t_{col}_{row}.png   pixel-art map tiles (TILE px square)
    docs/tiles/overview.png        1/4 scale image for zoomed-out views
    docs/data/meta.json            projection + tile layout
    docs/data/stops.json           active bus stops in the box
    docs/data/graph.json           bus-drivable road graph (for path snapping)
    docs/data/labels.json          place / college / landmark names

Run:  py tools/bake.py
"""
import csv
import json
import math
import re
import os
import sys
import zlib

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
DOCS = os.path.join(ROOT, "docs")

# ---- projection ---------------------------------------------------------
LAT0, LAT1 = 52.150, 52.250
LON0, LON1 = 0.040, 0.220
MPP = 3.0                                   # metres per map pixel
COSLAT = math.cos(math.radians((LAT0 + LAT1) / 2))
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * COSLAT
W = int(round((LON1 - LON0) * M_PER_DEG_LON / MPP))
H = int(round((LAT1 - LAT0) * M_PER_DEG_LAT / MPP))
TILE = 512


def proj(lat, lon):
    return ((lon - LON0) * M_PER_DEG_LON / MPP, (LAT1 - lat) * M_PER_DEG_LAT / MPP)


def geom_xy(geom):
    return [proj(p["lat"], p["lon"]) for p in geom]


def load(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return json.load(f)["elements"]


# ---- polygon assembly (ways + multipolygon relations) --------------------
def rings_from_members(members, role):
    """Stitch a relation's member ways (with geometry) into closed rings."""
    segs = [geom_xy(m["geometry"]) for m in members
            if m.get("type") == "way" and (m.get("role") or "outer") == role
            and m.get("geometry")]
    rings = []
    while segs:
        ring = segs.pop()
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def polygons(el):
    """-> list of (outer_rings, inner_rings) for a way or multipolygon relation."""
    if el["type"] == "way":
        g = el.get("geometry")
        if not g or len(g) < 3:
            return []
        return [([geom_xy(g)], [])]
    if el["type"] == "relation" and el.get("members"):
        return [(rings_from_members(el["members"], "outer"),
                 rings_from_members(el["members"], "inner"))]
    return []


# ---- ground classes -------------------------------------------------------
(FIELD, RES, COMM, IND, GRASS, WOOD, CEMETERY, CAMPUS, PARKING, WATER, PLAZA,
 PAVE, ROAD, BUSWAY, PATH, CYCLE, RAIL, SAND, ALLOT) = range(19)

BASE = {
    FIELD: (178, 192, 122), RES: (214, 209, 192), COMM: (224, 204, 194),
    IND: (204, 200, 210), GRASS: (128, 188, 98), WOOD: (74, 134, 76),
    CEMETERY: (152, 182, 132), CAMPUS: (228, 217, 182), PARKING: (180, 180, 184),
    WATER: (66, 142, 206), PLAZA: (228, 220, 202), PAVE: (236, 232, 224),
    ROAD: (98, 100, 112), BUSWAY: (196, 170, 120), PATH: (238, 224, 184),
    CYCLE: (218, 170, 152), RAIL: (132, 122, 112), SAND: (226, 210, 160),
    ALLOT: (160, 176, 104),
}

LANDUSE = {
    "farmland": FIELD, "farmyard": FIELD, "meadow": FIELD, "orchard": FIELD,
    "greenfield": FIELD, "brownfield": IND, "construction": IND,
    "residential": RES, "commercial": COMM, "retail": COMM, "industrial": IND,
    "railway": IND, "grass": GRASS, "recreation_ground": GRASS,
    "village_green": GRASS, "cemetery": CEMETERY, "religious": CAMPUS,
    "forest": WOOD, "education": CAMPUS, "allotments": ALLOT, "flowerbed": GRASS,
    "plant_nursery": ALLOT,
}
LEISURE = {"park": GRASS, "garden": GRASS, "common": GRASS, "pitch": GRASS,
           "golf_course": GRASS, "nature_reserve": GRASS, "playground": SAND,
           "sports_centre": COMM, "recreation_ground": GRASS, "track": SAND}
NATURAL = {"wood": WOOD, "scrub": WOOD, "grassland": GRASS, "wetland": GRASS,
           "water": WATER}
AMENITY = {"parking": PARKING, "university": CAMPUS, "college": CAMPUS,
           "school": CAMPUS, "hospital": CAMPUS}


def ground_class(tags):
    if tags.get("natural") == "water" or tags.get("waterway") == "riverbank":
        return WATER
    for key, table in (("amenity", AMENITY), ("leisure", LEISURE),
                       ("natural", NATURAL), ("landuse", LANDUSE)):
        if tags.get(key) in table:
            return table[tags[key]]
    if tags.get("highway") == "pedestrian" and tags.get("area") == "yes":
        return PLAZA
    return None


# draw order: broad land first, then detail on top
PRIORITY = [FIELD, ALLOT, RES, IND, COMM, CAMPUS, CEMETERY, GRASS, SAND,
            WOOD, PARKING, PLAZA, WATER]

# ---- roads ---------------------------------------------------------------
# class -> (pavement width, surface width, surface class) in map px (3 m)
ROAD_STYLE = {
    "motorway": (7, 6, ROAD), "trunk": (7, 5, ROAD), "primary": (6, 4, ROAD),
    "secondary": (5, 4, ROAD), "tertiary": (5, 3, ROAD),
    "motorway_link": (4, 3, ROAD), "trunk_link": (4, 3, ROAD),
    "primary_link": (4, 3, ROAD), "secondary_link": (4, 3, ROAD),
    "tertiary_link": (4, 3, ROAD), "unclassified": (4, 2, ROAD),
    "residential": (4, 2, ROAD), "living_street": (3, 2, ROAD),
    "busway": (4, 3, BUSWAY), "bus_guideway": (4, 3, BUSWAY),
    "service": (0, 1, ROAD), "pedestrian": (0, 2, PLAZA),
    "footway": (0, 1, PATH), "path": (0, 1, PATH), "bridleway": (0, 1, PATH),
    "steps": (0, 1, PATH), "track": (0, 1, SAND), "cycleway": (0, 1, CYCLE),
}
CENTRE_LINE = {"motorway", "trunk", "primary", "secondary", "tertiary"}

# highways a bus can use (for the path-snapping graph)
BUS_OK = {"motorway", "trunk", "primary", "secondary", "tertiary",
          "motorway_link", "trunk_link", "primary_link", "secondary_link",
          "tertiary_link", "unclassified", "residential", "living_street",
          "service", "busway", "bus_guideway"}


def hashi(*vals):
    return zlib.crc32(("|".join(map(str, vals))).encode())


# ---- OSGB36 easting/northing -> WGS84 (NaPTAN has no lat/lon for 050) -----
def osgb_to_wgs84(E, N):
    a, b = 6377563.396, 6356256.909
    F0, lat0, lon0 = 0.9996012717, math.radians(49), math.radians(-2)
    N0, E0 = -100000, 400000
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    lat, M = lat0, 0.0
    while True:
        lat = (N - N0 - M) / (a * F0) + lat
        Ma = (1 + n + 1.25 * n**2 + 1.25 * n**3) * (lat - lat0)
        Mb = (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (15 / 8 * n**2 + 15 / 8 * n**3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = 35 / 24 * n**3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
        if abs(N - N0 - M) < 0.00001:
            break
    s, c, t = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * F0 / math.sqrt(1 - e2 * s * s)
    rho = a * F0 * (1 - e2) / (1 - e2 * s * s) ** 1.5
    eta2 = nu / rho - 1
    VII = t / (2 * rho * nu)
    VIII = t / (24 * rho * nu**3) * (5 + 3 * t * t + eta2 - 9 * t * t * eta2)
    IX = t / (720 * rho * nu**5) * (61 + 90 * t * t + 45 * t**4)
    X = 1 / (c * nu)
    XI = 1 / (c * 6 * nu**3) * (nu / rho + 2 * t * t)
    XII = 1 / (c * 120 * nu**5) * (5 + 28 * t * t + 24 * t**4)
    XIIA = 1 / (c * 5040 * nu**7) * (61 + 662 * t * t + 1320 * t**4 + 720 * t**6)
    dE = E - E0
    lat = lat - VII * dE**2 + VIII * dE**4 - IX * dE**6
    lon = lon0 + X * dE - XI * dE**3 + XII * dE**5 - XIIA * dE**7
    # OSGB36 (Airy) -> cartesian -> Helmert -> WGS84
    s, c = math.sin(lat), math.cos(lat)
    nu = a / math.sqrt(1 - e2 * s * s)
    x = nu * c * math.cos(lon); y = nu * c * math.sin(lon); z = (1 - e2) * nu * s
    tx, ty, tz = 446.448, -125.157, 542.060
    rx, ry, rz = [math.radians(v / 3600) for v in (0.1502, 0.2470, 0.8421)]
    sc = 1 - 20.4894e-6
    x2 = tx + sc * x - rz * y + ry * z
    y2 = ty + rz * x + sc * y - rx * z
    z2 = tz - ry * x + rx * y + sc * z
    a2, b2 = 6378137.0, 6356752.3142
    e22 = 1 - (b2 * b2) / (a2 * a2)
    p = math.hypot(x2, y2)
    lat = math.atan2(z2, p * (1 - e22))
    for _ in range(10):
        nu = a2 / math.sqrt(1 - e22 * math.sin(lat) ** 2)
        lat = math.atan2(z2 + e22 * nu * math.sin(lat), p)
    return math.degrees(lat), math.degrees(math.atan2(y2, x2))


# ==========================================================================
def bake_ground():
    print(f"map {W}x{H} px at {MPP} m/px")
    cls = Image.new("L", (W, H), FIELD)
    d = ImageDraw.Draw(cls)

    areas = {k: [] for k in PRIORITY}
    for f in ("landuse.json", "leisure.json", "water.json"):
        for el in load(f):
            c = ground_class(el.get("tags", {}))
            if c is None:
                continue
            for outers, inners in polygons(el):
                areas[c].append((outers, inners))
    for c in PRIORITY:
        for outers, inners in areas[c]:
            for r in outers:
                d.polygon(r, fill=c)
            # inner rings (holes) fall back to whatever the town is around them
            for r in inners:
                d.polygon(r, fill=RES if c != FIELD else FIELD)
    print("areas:", {k: len(v) for k, v in areas.items()})

    # rivers / streams / drains as lines
    for el in load("water.json"):
        t = el.get("tags", {})
        ww = t.get("waterway")
        if el["type"] != "way" or ww not in ("river", "stream", "canal", "drain", "ditch"):
            continue
        if t.get("tunnel") in ("yes", "culvert"):
            continue
        w = {"river": 6, "canal": 4, "stream": 2}.get(ww, 1)
        d.line(geom_xy(el["geometry"]), fill=WATER, width=w, joint="curve")

    roads = [el for el in load("roads.json") if el["type"] == "way" and el.get("geometry")]
    rails, hw = [], []
    for el in roads:
        t = el["tags"]
        if "railway" in t and "highway" not in t:
            if t["railway"] in ("rail", "light_rail") and t.get("tunnel") != "yes":
                rails.append(el)
        elif t.get("highway") in ROAD_STYLE and t.get("tunnel") != "yes":
            hw.append(el)

    def rank(el):
        return list(ROAD_STYLE).index(el["tags"]["highway"]) * -1

    hw.sort(key=rank)          # minor first, major drawn on top
    for el in rails:
        d.line(geom_xy(el["geometry"]), fill=RAIL, width=3)
    for el in hw:
        pw, sw, sc = ROAD_STYLE[el["tags"]["highway"]]
        if el["tags"].get("area") == "yes":
            continue
        if pw:
            d.line(geom_xy(el["geometry"]), fill=PAVE, width=pw, joint="curve")
    for el in hw:
        pw, sw, sc = ROAD_STYLE[el["tags"]["highway"]]
        if el["tags"].get("area") == "yes":
            continue
        d.line(geom_xy(el["geometry"]), fill=sc, width=sw, joint="curve")
    return cls, hw, rails


def texture(cls):
    """Class map -> RGB with deterministic pixel-art texture."""
    a = np.asarray(cls)
    hgt, wid = a.shape
    yy, xx = np.mgrid[0:hgt, 0:wid].astype(np.uint32)
    noise = ((xx * 73856093) ^ (yy * 19349663) ^ ((xx * yy) * 83492791)) % 251
    rgb = np.zeros((hgt, wid, 3), np.uint8)
    for c, col in BASE.items():
        rgb[a == c] = col

    def shade(mask, col):
        rgb[mask] = col

    shade((a == FIELD) & (yy % 5 == 0), (164, 180, 110))                  # furrows
    shade((a == FIELD) & (noise < 12), (190, 202, 134))
    shade((a == GRASS) & (noise < 20), (110, 170, 84))
    shade((a == GRASS) & (noise > 238), (150, 206, 112))
    shade((a == ALLOT) & ((xx // 4 + yy // 4) % 2 == 0), (144, 162, 92))
    # woodland: little tree blobs on a grid with jitter
    tree = ((xx + (yy // 4) * 2) % 4 == 0) & (yy % 4 == 0)
    shade((a == WOOD) & (noise < 60), (60, 116, 64))
    shade((a == WOOD) & tree, (46, 96, 52))
    shade((a == WOOD) & np.roll(tree, 1, axis=0), (100, 160, 92))
    shade((a == RES) & (noise < 10), (204, 199, 182))
    shade((a == CAMPUS) & (noise < 10), (216, 205, 170))
    shade((a == CEMETERY) & (xx % 4 == 0) & (yy % 3 == 0), (210, 210, 205))
    shade((a == PARKING) & (xx % 4 == 0), (206, 206, 210))
    # water: horizontal ripple dashes
    rip = (yy % 4 == 0) & (((xx + (yy // 4) * 5) % 13) < 3)
    shade((a == WATER) & rip, (126, 186, 234))
    shade((a == ROAD) & (noise < 14), (90, 92, 104))
    shade((a == RAIL) & (xx % 3 == 0), (96, 84, 74))
    shade((a == BUSWAY) & (noise < 16), (182, 156, 108))
    return Image.fromarray(rgb, "RGB")


def draw_centre_lines(img, hw):
    d = ImageDraw.Draw(img)
    for el in hw:
        if el["tags"]["highway"] not in CENTRE_LINE or el["tags"].get("area") == "yes":
            continue
        pts = geom_xy(el["geometry"])
        dist = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg == 0:
                continue
            t = 0.0
            while t < seg:
                if (dist + t) % 8 < 3:
                    px, py = x0 + (x1 - x0) * t / seg, y0 + (y1 - y0) * t / seg
                    d.point((px, py), fill=(244, 240, 222))
                t += 1.0
            dist += seg


# ---- buildings -----------------------------------------------------------
ROOFS_HOUSE = [(176, 92, 72), (98, 106, 122), (150, 102, 82), (132, 132, 140),
               (186, 112, 84), (112, 92, 86)]
STONE = (214, 192, 134)
FLAT = [(190, 190, 194), (172, 178, 188), (198, 186, 170)]


def darker(c, f=0.72):
    return tuple(int(v * f) for v in c)


def building_style(tags, area_px, campus_hit):
    b = tags.get("building", "yes")
    name = (tags.get("name") or "").lower()
    lv = tags.get("building:levels")
    try:
        levels = float(lv.split(";")[0]) if lv else None
    except ValueError:
        levels = None
    if b in ("church", "cathedral", "chapel") or tags.get("amenity") == "place_of_worship":
        roof, h = (150, 150, 156), 5
    elif b in ("college", "university") or campus_hit or "college" in name:
        roof, h = STONE, 4
    elif b in ("house", "detached", "semidetached_house", "terrace", "residential",
               "bungalow", "apartments", "garage", "garages", "shed", "hut"):
        roof = ROOFS_HOUSE[hashi(tags.get("addr:street", ""), area_px // 40) % len(ROOFS_HOUSE)]
        h = 1 if b in ("garage", "garages", "shed", "hut", "bungalow") else 2
        if b == "apartments":
            h = 3
    elif b in ("hospital",):
        roof, h = (222, 224, 232), 4
    elif b in ("industrial", "warehouse", "commercial", "retail", "office",
               "supermarket", "train_station", "transportation"):
        roof, h = FLAT[hashi(b) % len(FLAT)], 3
    else:
        roof = ROOFS_HOUSE[hashi(area_px // 25) % len(ROOFS_HOUSE)] if area_px < 220 else FLAT[hashi(area_px) % len(FLAT)]
        h = 2 if area_px < 60 else 3
    if levels:
        h = levels
    h = int(max(1, min(7, round(h * 0.8)))) if levels else h
    return roof, h


def poly_area(r):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))) / 2


def draw_buildings(img, cls):
    clsa = np.asarray(cls)
    blds = []
    for el in load("buildings.json"):
        t = el.get("tags", {})
        if t.get("building") in (None, "no", "construction"):
            continue
        for outers, inners in polygons(el):
            for r in outers:
                if len(r) < 3:
                    continue
                ys = [p[1] for p in r]
                xs = [p[0] for p in r]
                cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
                if not (0 <= cx < W and 0 <= cy < H):
                    continue
                area = poly_area(r)
                campus = clsa[cy, cx] == CAMPUS
                roof, h = building_style(t, area, campus)
                if t.get("building") == "roof":
                    h = 1
                if area < 6:
                    h = min(h, 1)
                blds.append((max(ys), r, roof, h))
    blds.sort(key=lambda b: b[0])   # north first, so southern walls overlap
    print("buildings:", len(blds))

    shadow = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shadow)
    for _, r, roof, h in blds:
        sd.polygon([(x + 1 + h * 0.5, y + 1) for x, y in r], fill=1)
    sh = np.asarray(shadow).astype(bool)
    a = np.asarray(img).copy()
    a[sh] = (a[sh] * 0.78).astype(np.uint8)
    img.paste(Image.fromarray(a, "RGB"))

    d = ImageDraw.Draw(img)
    for _, r, roof, h in blds:
        wall = darker(roof, 0.62)
        for k in range(h):
            d.polygon([(x, y - k) for x, y in r], fill=wall)
        top = [(x, y - h) for x, y in r]
        d.polygon(top, fill=roof, outline=darker(roof, 0.82) if poly_area(r) > 30 else None)
    return blds


# ---- data exports ----------------------------------------------------------
def export_stops():
    out = []
    with open(os.path.join(RAW, "naptan050.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["Status"] != "active" or row["StopType"] not in ("BCT", "BCS"):
                continue
            if row["Latitude"]:
                lat, lon = float(row["Latitude"]), float(row["Longitude"])
            else:
                lat, lon = osgb_to_wgs84(float(row["Easting"]), float(row["Northing"]))
            x, y = proj(lat, lon)
            if not (0 <= x < W and 0 <= y < H):
                continue
            out.append({
                "id": row["ATCOCode"], "name": row["CommonName"],
                "ind": row["Indicator"], "b": row["Bearing"],
                "street": row["Street"], "loc": row["LocalityName"],
                "x": round(x, 1), "y": round(y, 1),
                "lat": round(lat, 6), "lon": round(lon, 6),
            })
    print("stops:", len(out))
    return out


def export_graph(roads):
    """Directed graph of bus-usable roads. Nodes = OSM node ids, deduped."""
    node_xy, edges = {}, []
    for el in roads:
        t = el["tags"]
        hwy = t.get("highway")
        if hwy not in BUS_OK or t.get("area") == "yes":
            continue
        if hwy == "service" and t.get("service") in ("parking_aisle", "driveway", "drive-through"):
            continue
        if t.get("access") in ("private", "no") and t.get("bus") not in ("yes", "designated") \
                and t.get("psv") not in ("yes", "designated") and hwy not in ("busway", "bus_guideway"):
            continue
        ids, geo = el.get("nodes"), el["geometry"]
        if not ids or len(ids) != len(geo):
            continue
        ow = t.get("oneway")
        if t.get("junction") in ("roundabout", "circular") and ow is None:
            ow = "yes"
        if t.get("oneway:bus") == "no" or t.get("oneway:psv") == "no" or hwy in ("busway", "bus_guideway"):
            if ow != "-1":
                ow = "no"
        for nid, p in zip(ids, geo):
            node_xy[nid] = proj(p["lat"], p["lon"])
        for a_, b_ in zip(ids, ids[1:]):
            if ow == "-1":
                edges.append((b_, a_, 1))
            else:
                edges.append((a_, b_, 1 if ow in ("yes", "true", "1") else 0))
    # keep only nodes inside a margin of the map
    idx, xs = {}, []
    for nid, (x, y) in node_xy.items():
        if -200 < x < W + 200 and -200 < y < H + 200:
            idx[nid] = len(idx)
            xs += [round(x * 4), round(y * 4)]
    e = []
    for a_, b_, ow in edges:
        if a_ in idx and b_ in idx:
            e += [idx[a_], idx[b_], ow]
    print("graph:", len(idx), "nodes", len(e) // 3, "edges")
    return {"scale": 4, "n": xs, "e": e}


def export_labels():
    labels = []
    seen = set()
    for el in load("places.json"):
        t = el.get("tags", {})
        name = t.get("name")
        if not name or name in seen:
            continue
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        x, y = proj(lat, lon)
        if not (0 <= x < W and 0 <= y < H):
            continue
        if t.get("place") in ("city", "town"):
            kind = "town"
        elif t.get("place") in ("suburb", "village", "quarter"):
            kind = "suburb"
        elif t.get("place") == "neighbourhood":
            kind = "hood"
        elif t.get("railway") == "station":
            kind = "station"
        elif t.get("amenity") in ("college", "university"):
            kind = "college"
            # keep the colleges themselves, not every court / site / annexe
            name = re.sub(r"\s*\(University of Cambridge\)", "", name)
            if "(" in name or "College" not in name or re.search(
                    r"Court|Site|Building|Centre|Library|Lab|Department|Institute|House|School|Faculty|Museum|Garden", name):
                continue
        else:
            kind = "poi"
        seen.add(name)
        labels.append({"name": name, "kind": kind, "x": round(x), "y": round(y)})
    print("labels:", len(labels))
    return labels


def main():
    os.makedirs(os.path.join(DOCS, "tiles"), exist_ok=True)
    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    cls, hw, rails = bake_ground()
    img = texture(cls)
    draw_centre_lines(img, hw)
    draw_buildings(img, cls)

    cols, rows = math.ceil(W / TILE), math.ceil(H / TILE)
    for c in range(cols):
        for r in range(rows):
            tile = img.crop((c * TILE, r * TILE, (c + 1) * TILE, (r + 1) * TILE))
            tile = tile.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            tile.save(os.path.join(DOCS, "tiles", f"t_{c}_{r}.png"), optimize=True)
    ov = img.resize((W // 4, H // 4), Image.Resampling.BOX)
    ov.save(os.path.join(DOCS, "tiles", "overview.png"), optimize=True)

    roads = [el for el in load("roads.json") if el["type"] == "way" and el.get("geometry")]
    meta = {"W": W, "H": H, "mpp": MPP, "tile": TILE, "cols": cols, "rows": rows,
            "lat0": LAT0, "lat1": LAT1, "lon0": LON0, "lon1": LON1,
            "mPerDegLat": M_PER_DEG_LAT, "mPerDegLon": M_PER_DEG_LON}
    dumps = {
        "meta.json": meta,
        "stops.json": export_stops(),
        "graph.json": export_graph(roads),
        "labels.json": export_labels(),
    }
    for name, obj in dumps.items():
        with open(os.path.join(DOCS, "data", name), "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
    print("done:", cols * rows, "tiles")


if __name__ == "__main__":
    sys.exit(main())
