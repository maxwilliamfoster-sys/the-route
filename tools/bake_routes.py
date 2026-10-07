"""Bake real bus-route lines + which routes serve each stop, from the official
BODS GTFS timetable for East Anglia (keyless bulk download).

    data/raw/ea_gtfs.zip  ->  docs/data/routes.json   (route lines, map px)
                          ->  docs/data/stops.json    (adds "l": [lines] per stop)

Run after tools/bake.py:  py tools/bake_routes.py
Download:  curl -L -o data/raw/ea_gtfs.zip https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/east_anglia/
"""
import collections
import csv
import html
import io
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
DATA = os.path.join(ROOT, "docs", "data")

META = json.load(open(os.path.join(DATA, "meta.json")))
MARGIN = 0.01  # degrees of slack around the box when clipping lines


def proj(lat, lon):
    return ((lon - META["lon0"]) * META["mPerDegLon"] / META["mpp"],
            (META["lat1"] - lat) * META["mPerDegLat"] / META["mpp"])


def inside(lat, lon, m=0.0):
    return (META["lat0"] - m <= lat <= META["lat1"] + m and
            META["lon0"] - m <= lon <= META["lon1"] + m)


def rdp(pts, eps):
    """Ramer-Douglas-Peucker, iterative."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x0, y0), (x1, y1) = pts[a], pts[b]
        dx, dy = x1 - x0, y1 - y0
        L = (dx * dx + dy * dy) ** 0.5 or 1e-9
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * (pts[i][0] - x0) - dx * (pts[i][1] - y0)) / L
            if d > best:
                best, bi = d, i
        if best > eps:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def main():
    z = zipfile.ZipFile(os.path.join(RAW, "ea_gtfs.zip"))

    def rows(name):
        return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))

    agency = {r["agency_id"]: r["agency_name"] for r in rows("agency.txt")}
    routes = {r["route_id"]: (r["route_short_name"] or r["route_long_name"], agency.get(r["agency_id"], ""))
              for r in rows("routes.txt")}
    trips = {r["trip_id"]: (r["route_id"], r["shape_id"], r["trip_headsign"]) for r in rows("trips.txt")}
    stops_in = {r["stop_id"] for r in rows("stops.txt")
                if r["stop_lat"] and inside(float(r["stop_lat"]), float(r["stop_lon"]))}
    print(f"{len(routes)} routes, {len(trips)} trips, {len(stops_in)} GTFS stops in the box")

    stop_lines = collections.defaultdict(set)
    trips_in = set()
    for r in rows("stop_times.txt"):
        sid = r["stop_id"]
        if sid in stops_in:
            t = trips.get(r["trip_id"])
            if t:
                trips_in.add(r["trip_id"])
                stop_lines[sid].add(routes[t[0]][0])
    print(f"{len(trips_in)} trips call in the box")

    # pick the distinct shapes per line, busiest first
    shape_trips = collections.Counter()
    shape_line, line_info = {}, collections.defaultdict(lambda: {"agency": set(), "heads": collections.Counter(), "trips": 0})
    for tid in trips_in:
        rid, shp, head = trips[tid]
        line, ag = routes[rid]
        if shp:
            shape_trips[shp] += 1
            shape_line[shp] = line
        info = line_info[line]
        info["agency"].add(ag)
        info["heads"][html.unescape(head.split(",")[0].strip())] += 1
        info["trips"] += 1

    wanted = set(shape_trips)
    pts = collections.defaultdict(list)
    for r in rows("shapes.txt"):
        s = r["shape_id"]
        if s in wanted:
            pts[s].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    print(f"{len(pts)} shapes loaded")

    by_line = collections.defaultdict(list)
    for s, p in pts.items():
        p.sort()
        runs, cur = [], []
        for _, lat, lon in p:
            if inside(lat, lon, MARGIN):
                cur.append(proj(lat, lon))
            elif cur:
                runs.append(cur); cur = []
        if cur:
            runs.append(cur)
        runs = [rdp(r, 0.8) for r in runs if len(r) > 1]
        if runs:
            by_line[shape_line[s]].append((shape_trips[s], runs))

    out = []
    for line, shapes in by_line.items():
        shapes.sort(key=lambda t: -t[0])
        # drop shapes that are (nearly) duplicates of a busier one: compare sampled cells
        kept, cells_seen = [], []
        for n, runs in shapes:
            cells = {(int(x) // 12, int(y) // 12) for r in runs for x, y in r}
            if any(len(cells - c) <= 0.1 * len(cells) for c in cells_seen):
                continue
            cells_seen.append(cells)
            kept.append(runs)
            if len(kept) >= 6:
                break
        info = line_info[line]
        out.append({
            "line": line,
            "agency": sorted(info["agency"]),
            "heads": [h for h, _ in info["heads"].most_common(4)],
            "trips": info["trips"],
            "paths": [[round(v * 2) for xy in r for v in xy] for runs in kept for r in runs],
        })
    out.sort(key=lambda r: -r["trips"])
    with open(os.path.join(DATA, "routes.json"), "w", encoding="utf-8") as f:
        json.dump({"scale": 2, "routes": out}, f, separators=(",", ":"))
    print(f"routes.json: {len(out)} lines")

    sp = os.path.join(DATA, "stops.json")
    stops = json.load(open(sp, encoding="utf-8"))
    hit = 0
    for s in stops:
        ls = sorted(stop_lines.get(s["id"], ()), key=lambda l: (len(l), l))
        s["l"] = ls
        hit += bool(ls)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(stops, f, separators=(",", ":"))
    print(f"stops with routes: {hit}/{len(stops)}")


if __name__ == "__main__":
    main()
