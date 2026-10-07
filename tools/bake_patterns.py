"""Bake journey patterns for live ETAs, from the official BODS GTFS.

A "pattern" is one ordered list of stops a line runs (e.g. the 1 towards
Fulbourn). For each pattern we store:
    - its shape (map px) and each stop's distance along it
    - the median scheduled time from the first stop to every stop
    - every trip's start time + service id, so the page can tell which
      timetabled journey a live bus is on and how early/late it is
plus the service calendars (which days each service runs).

    data/raw/ea_gtfs.zip -> docs/data/patterns.json
Run after tools/bake.py:  py tools/bake_patterns.py
"""
import collections
import csv
import html
import io
import json
import math
import os
import statistics
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
DATA = os.path.join(ROOT, "docs", "data")
META = json.load(open(os.path.join(DATA, "meta.json")))
SNAP_PX = 30          # a stop further than this from the shape gets no position


def proj(lat, lon):
    return ((lon - META["lon0"]) * META["mPerDegLon"] / META["mpp"],
            (META["lat1"] - lat) * META["mPerDegLat"] / META["mpp"])


def inside(lat, lon, m=0.0):
    return (META["lat0"] - m <= lat <= META["lat1"] + m and
            META["lon0"] - m <= lon <= META["lon1"] + m)


def secs(t):
    h, m, s = (int(v) for v in t.split(":"))
    return h * 3600 + m * 60 + s


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x0, y0), (x1, y1) = pts[a], pts[b]
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * (pts[i][0] - x0) - dx * (pts[i][1] - y0)) / L
            if d > best:
                best, bi = d, i
        if best > eps:
            keep[bi] = True; stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def project(shape, cum, x, y, start_seg=0):
    """Nearest point on the polyline at or after start_seg -> (dist, along, seg)."""
    best = (1e18, 0.0, start_seg)
    for i in range(start_seg, len(shape) - 1):
        (x0, y0), (x1, y1) = shape[i], shape[i + 1]
        vx, vy = x1 - x0, y1 - y0
        L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / L2))
        px, py = x0 + vx * t, y0 + vy * t
        d = math.hypot(x - px, y - py)
        if d < best[0]:
            best = (d, cum[i] + math.sqrt(L2) * t, i)
    return best


def main():
    z = zipfile.ZipFile(os.path.join(RAW, "ea_gtfs.zip"))

    def rows(name):
        return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))

    routes = {r["route_id"]: r["route_short_name"] or r["route_long_name"] for r in rows("routes.txt")}
    trips = {r["trip_id"]: r for r in rows("trips.txt")}
    stops = {r["stop_id"]: r for r in rows("stops.txt")}
    in_box = {sid for sid, r in stops.items() if r["stop_lat"] and inside(float(r["stop_lat"]), float(r["stop_lon"]))}

    # all stop_times for trips that call in the box
    st = collections.defaultdict(list)
    for r in rows("stop_times.txt"):
        st[r["trip_id"]].append(r)
    keep = {t for t, lst in st.items() if any(x["stop_id"] in in_box for x in lst) and t in trips}
    print(f"{len(keep)} trips call in the box")

    pats = {}
    for tid in keep:
        lst = sorted(st[tid], key=lambda r: int(r["stop_sequence"]))
        tr = trips[tid]
        line = routes[tr["route_id"]]
        key = (line, tuple(r["stop_id"] for r in lst))
        t0 = secs(lst[0]["departure_time"] or lst[0]["arrival_time"])
        offs = [secs(r["arrival_time"] or r["departure_time"]) - t0 for r in lst]
        p = pats.setdefault(key, {"line": line, "offs": [], "trips": [], "heads": collections.Counter(),
                                  "shapes": collections.Counter()})
        p["offs"].append(offs)
        p["trips"].append((t0, tr["service_id"]))
        p["heads"][html.unescape(tr["trip_headsign"]).split(",")[0].strip()] += 1
        if tr["shape_id"]:
            p["shapes"][tr["shape_id"]] += 1
    print(f"{len(pats)} patterns")

    want_shapes = {p["shapes"].most_common(1)[0][0] for p in pats.values() if p["shapes"]}
    shp = collections.defaultdict(list)
    for r in rows("shapes.txt"):
        if r["shape_id"] in want_shapes:
            shp[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))

    services = set()
    out, ext = [], {}
    for (line, seq), p in pats.items():
        if not p["shapes"]:
            continue
        pts = [proj(lat, lon) for _, lat, lon in sorted(shp[p["shapes"].most_common(1)[0][0]])]
        pts = rdp(pts, 0.8)
        if len(pts) < 2:
            continue
        cum = [0.0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
        offs = [int(statistics.median(col)) for col in zip(*p["offs"])]
        ds, seg = [], 0
        for sid in seq:
            s = stops.get(sid)
            if not s or not s["stop_lat"]:
                ds.append(-1); continue
            x, y = proj(float(s["stop_lat"]), float(s["stop_lon"]))
            d, along, seg2 = project(pts, cum, x, y, seg)
            if d > SNAP_PX:                       # loop or detour: try the whole shape
                d, along, seg2 = project(pts, cum, x, y, 0)
            if d > SNAP_PX:
                ds.append(-1); continue
            seg = seg2
            ds.append(round(along, 1))
            if sid not in in_box:
                ext[sid] = [s["stop_name"], round(x), round(y)]
        for _, sv in p["trips"]:
            services.add(sv)
        out.append({
            "line": line,
            "head": p["heads"].most_common(1)[0][0],
            "stops": list(seq),
            "d": ds,
            "offs": offs,
            "trips": sorted([t, sv] for t, sv in p["trips"]),
            "shape": [round(v * 2) for xy in pts for v in xy],
        })
    out.sort(key=lambda p: (p["line"], -len(p["trips"])))

    cal = {}
    for r in rows("calendar.txt"):
        if r["service_id"] in services:
            cal[r["service_id"]] = {"days": "".join(r[d] for d in ("monday", "tuesday", "wednesday", "thursday",
                                                                 "friday", "saturday", "sunday")),
                                    "from": r["start_date"], "to": r["end_date"], "add": [], "drop": []}
    for r in rows("calendar_dates.txt"):
        c = cal.get(r["service_id"])
        if c is None and r["service_id"] in services:
            c = cal[r["service_id"]] = {"days": "0000000", "from": "", "to": "", "add": [], "drop": []}
        if c is not None:
            (c["add"] if r["exception_type"] == "1" else c["drop"]).append(r["date"])

    with open(os.path.join(DATA, "patterns.json"), "w", encoding="utf-8") as f:
        json.dump({"scale": 2, "patterns": out, "calendar": cal, "ext": ext}, f, separators=(",", ":"))
    print(f"patterns.json: {len(out)} patterns, {len(cal)} services, {len(ext)} out-of-map stops, "
          f"{os.path.getsize(os.path.join(DATA, 'patterns.json')) // 1024} KB")


if __name__ == "__main__":
    main()
