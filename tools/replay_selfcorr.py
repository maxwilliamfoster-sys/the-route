"""Replay recorded live runs to compare ETA strategies fairly, on the same buses and moments.

Each record (from tools/eval_eta.py) has: when the prediction was made, when the bus really
arrived, and what each method predicted (timetable eS, live-traffic eX, app-final eT).
Self-correction is simulated online in strict time order: a prediction made at time t may only
use outcomes that had already happened before t.

    py tools/replay_selfcorr.py data/eval_raw_selfcorr1.json data/eval_raw.json
"""
import json
import math
import sys
import statistics as st

TAU = 15 * 60e3
PRIOR = 3.0


def bucket(L):
    return 0 if L < 180 else 1 if L < 420 else 2 if L < 900 else 3


class Calib:
    def __init__(self, per_line=True, max_bucket=1):
        self.b, self.l, self.per_line, self.maxb = {}, {}, per_line, max_bucket

    def _get(self, m, k, now):
        c = m.get(k)
        if not c:
            return 0.0, 0.0
        return c[0], c[1] * math.exp(-(now - c[2]) / TAU)

    def _add(self, m, k, lr, now):
        lr0, w0 = self._get(m, k, now)
        m[k] = ((lr0 * w0 + lr) / (w0 + 1), w0 + 1, now)

    def factor(self, line, L, now):
        bk = bucket(L)
        if bk > self.maxb:
            return 1.0
        lb, wb = self._get(self.b, bk, now)
        lr = lb * wb / (wb + PRIOR)
        if self.per_line:
            ll, wl = self._get(self.l, (line, bk), now)
            if wl > 2:
                lr = (lr * PRIOR + ll * wl) / (PRIOR + wl)
        return math.exp(max(math.log(0.6), min(math.log(2.2), lr)))

    def learn(self, line, pred, act, now):
        bk = bucket(pred)
        if pred < 30 or bk > self.maxb or act < 0:
            return
        lr = math.log(max(0.25, min(4.0, act / pred)))
        self._add(self.b, bk, lr, now)
        if self.per_line:
            self._add(self.l, (line, bk), lr, now)


def run(R, base_key, calib=None, fair_cut=None):
    """Return list of (predicted-lead, error) for one strategy.
    fair_cut: only score predictions made before this time (so nearly all of them had time to resolve);
    results are grouped by the timetable method's predicted lead, the same for every strategy."""
    events = []
    for i, r in enumerate(R):
        events.append((r["made"], 0, i))   # prediction
        events.append((r["tAct"], 1, i))   # outcome
    events.sort()
    pred_at = {}
    out = []
    for t, kind, i in events:
        r = R[i]
        base = max(0.0, r["lead"] + r[base_key])          # seconds from 'made' predicted by the base method
        if kind == 0:
            f = calib.factor(r["line"], base, t) if calib else 1.0
            pred_at[i] = base * f
        else:
            if calib:
                calib.learn(r["line"], base, r["lead"], t)
            if fair_cut is None or r["made"] <= fair_cut:
                out.append((max(0.0, r["lead"] + r["eS"]), pred_at[i] - r["lead"]))
    return out


def report(name, res):
    def mae(rows):
        return round(st.mean(abs(e) for _, e in rows)) if rows else None
    cells = []
    for lo, hi in [(0, 180), (180, 420), (420, 900), (900, 1e9)]:
        cells.append(mae([x for x in res if lo <= x[0] < hi]))
    w1 = round(100 * sum(1 for _, e in res if abs(e) <= 60) / len(res))
    print(f"{name:44s} all {mae(res):4d}s | 0-3m {cells[0]:4d} | 3-7m {cells[1]:4d} | 7-15m {cells[2]:4d} | 15m+ {cells[3]:4d} | ≤1min {w1}%")


for path in sys.argv[1:]:
    R = [r for r in json.load(open(path)) if "made" in r and "tAct" in r and "eX" in r and r["tAct"] > r["made"]]
    print(f"\n=== {path}: {len(R)} arrivals")
    report("timetable from position", run(R, "eS"))
    report("live-traffic run times", run(R, "eX"))
    report("app as recorded", run(R, "eT"))
    for base, nm in [("eS", "timetable"), ("eX", "traffic")]:
        report(f"{nm} + self-correct (<7m, per line)", run(R, base, Calib(True, 1)))
        report(f"{nm} + self-correct (<7m, network only)", run(R, base, Calib(False, 1)))
        report(f"{nm} + self-correct (all ranges)", run(R, base, Calib(True, 3)))
