"""Collect real stop-to-stop running times, by hour of the week, for THE ROUTE.

Each run watches every Cambridge bus through the live relay (official BODS GTFS-RT, exact
trip ids) for a few minutes. Whenever a bus passes two consecutive stops of its timetabled
trip, the actual time between them is recorded next to the timetable's time, tagged with the
hour of the week. Over days this builds "how long does this stretch really take on a
Friday at 5pm" for every stop pair in the city.

    py tools/collect_speeds.py --store ../speeds-data --minutes 4 --interval 20

Writes (in --store):
    crossings/YYYY-MM-DD.csv   raw rows (append): one per stop-to-stop stretch observed
    speeds.json                running totals per stop pair per hour-of-week
"""
import argparse
import bisect
import csv
import datetime as dt
import json
import math
import os
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONDON = ZoneInfo("Europe/London")
RELAY = "https://the-route-live.maxwilliamfoster.workers.dev/live"
MPP = 3.0                 # metres per map pixel (docs/data/meta.json)
MAX_MS = 25.0             # 90 km/h: anything faster between two reports is a position glitch


def fnv(s):
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return format(h, "08x")


class Pattern:
    def __init__(self, p, scale):
        pts = p["shape"]
        self.X = [pts[i] / scale for i in range(0, len(pts), 2)]
        self.Y = [pts[i + 1] / scale for i in range(0, len(pts), 2)]
        self.C = [0.0]
        for i in range(1, len(self.X)):
            self.C.append(self.C[-1] + math.hypot(self.X[i] - self.X[i - 1], self.Y[i] - self.Y[i - 1]))
        self.line, self.stops, self.d, self.profiles = p["line"], p["stops"], p["d"], p["profiles"]

    def project(self, x, y, prev=None, max_d=60.0):
        best = None
        for i in range(len(self.X) - 1):
            x0, y0 = self.X[i], self.Y[i]
            if abs(x - x0) > 400 or abs(y - y0) > 400:
                continue
            vx, vy = self.X[i + 1] - x0, self.Y[i + 1] - y0
            L2 = vx * vx + vy * vy
            if not L2:
                continue
            t = max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / L2))
            d = math.hypot(x - (x0 + vx * t), y - (y0 + vy * t))
            if d > max_d:
                continue
            a = self.C[i] + math.sqrt(L2) * t
            pen = d + (30 if prev is not None and a < prev - 40 else 0)
            if best is None or pen < best[0]:
                best = (pen, a)
        return None if best is None else best[1]


def load():
    meta = json.load(open(os.path.join(ROOT, "docs", "data", "meta.json")))
    pj = json.load(open(os.path.join(ROOT, "docs", "data", "patterns.json")))
    pats = [Pattern(p, pj["scale"]) for p in pj["patterns"]]
    trips = {}
    for P, p in zip(pats, pj["patterns"]):
        for t0, sid, pi, h in p["trips"]:
            trips[h] = (P, pi)
    proj = lambda lat, lon: ((lon - meta["lon0"]) * meta["mPerDegLon"] / meta["mpp"],
                             (meta["lat1"] - lat) * meta["mPerDegLat"] / meta["mpp"])
    return trips, proj


def fetch():
    req = urllib.request.Request(RELAY, headers={"User-Agent": "TheRoute-collector/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--minutes", type=float, default=8)
    ap.add_argument("--interval", type=float, default=15)
    a = ap.parse_args()
    trips, proj = load()

    tracks = {}          # (vehicle, trip) -> list of (ts, along)
    end = time.time() + a.minutes * 60
    polls = 0
    while True:
        try:
            feed = fetch()
            polls += 1
            for v in feed.get("v", []):
                if not v.get("trip") or v.get("lat") is None or not v.get("ts"):
                    continue
                hit = trips.get(fnv(v["trip"]))
                if not hit:
                    continue
                P, pi = hit
                key = (v.get("id", "?"), v["trip"])
                tr = tracks.setdefault(key, [])
                if tr and v["ts"] <= tr[-1][0]:
                    continue                                   # no new report from this bus
                x, y = proj(v["lat"], v["lon"])
                al = P.project(x, y, tr[-1][1] if tr else None)
                if al is not None:
                    tr.append((v["ts"], al))
        except Exception as e:                                 # a missed poll just leaves a gap
            print("poll failed:", e)
        if time.time() + a.interval > end:
            break
        time.sleep(a.interval)

    # stop crossings (interpolated between reports), then consecutive stop-to-stop times
    rows = []
    for (veh, trip), tr in tracks.items():
        P, pi = trips[fnv(trip)]
        prof = P.profiles[pi]
        cross = {}
        for (t1, a1), (t2, a2) in zip(tr, tr[1:]):
            if a2 <= a1 or t2 <= t1 or t2 - t1 > 180:
                continue
            if (a2 - a1) * MPP / (t2 - t1) > MAX_MS:           # faster than any bus: a GPS / route-matching jump
                continue
            for j, d in enumerate(P.d):
                if d >= 0 and a1 < d <= a2 and j not in cross:
                    cross[j] = t1 + (d - a1) / (a2 - a1) * (t2 - t1)
        for j in sorted(cross):
            if j + 1 in cross:
                actual, sched = cross[j + 1] - cross[j], prof[j + 1] - prof[j]
                if sched <= 0 or not (0 < actual < 1800):
                    continue
                if (P.d[j + 1] - P.d[j]) * MPP / actual > MAX_MS:
                    continue
                when = dt.datetime.fromtimestamp(cross[j], LONDON)
                rows.append({"date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S"),
                             "how": when.weekday() * 24 + when.hour, "line": P.line,
                             "from": P.stops[j], "to": P.stops[j + 1],
                             "actual": round(actual, 1), "sched": sched, "trip": fnv(trip)})

    os.makedirs(os.path.join(a.store, "crossings"), exist_ok=True)
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    for day, rs in by_day.items():
        path = os.path.join(a.store, "crossings", f"{day}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rs[0].keys()))
            if new:
                w.writeheader()
            w.writerows(rs)

    agg_path = os.path.join(a.store, "speeds.json")
    agg = json.load(open(agg_path)) if os.path.exists(agg_path) else {"v": 1, "runs": 0, "rows": 0, "seg": {}}
    for r in rows:
        cell = agg["seg"].setdefault(f'{r["from"]}>{r["to"]}', {}).setdefault(str(r["how"]), [0.0, 0, 0])
        cell[0] = round(cell[0] + r["actual"], 1); cell[1] += r["sched"]; cell[2] += 1
    agg["runs"] += 1; agg["rows"] += len(rows)
    agg["updated"] = dt.datetime.now(LONDON).isoformat(timespec="seconds")
    with open(agg_path, "w") as f:
        json.dump(agg, f, separators=(",", ":"))
    print(f"polls={polls} buses={len(tracks)} stretches={len(rows)} total_rows={agg['rows']} segments={len(agg['seg'])}")


if __name__ == "__main__":
    main()
