"""Fit an arrival model to real outcomes from tools/eval_eta.py, test it on held-out data.

Target: actual seconds until arrival (lead). Features available at prediction time:
  schedRem = timetabled arrival - now      delay = how late the bus is now (s)
  traffic  = live traffic factor ahead     stopped / k / tps / age
The first 60% of predictions (in time order) fit the model; the last 40% test it.
"""
import json
import math
import os
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = [r for r in json.load(open(os.path.join(ROOT, "data", "eval_raw.json")))
     if r.get("schedRem") is not None and r.get("delay") is not None and abs(r["delay"]) < 1800]
print(len(R), "usable")
cut = int(len(R) * 0.6)
train, test = R[:cut], R[cut:]
mae = lambda e: float(np.mean(np.abs(e)))


def feats(r, kind):
    S, d = r["schedRem"], r["delay"]
    stop = 1.0 if (r.get("kmh") is not None and r["kmh"] < 3) else 0.0
    lt = math.log(r["traffic"]) if r.get("traffic") else 0.0
    base = [1.0, S, d, min(d, 0.0), stop, r["age"]]
    if kind == "traffic":
        base += [lt * max(S + d, 0)]
    return base


def fit(kind, rows):
    X = np.array([feats(r, kind) for r in rows]); y = np.array([r["lead"] for r in rows])
    # least absolute deviations via iteratively reweighted least squares (MAE is what users feel)
    w = np.ones(len(y))
    for _ in range(30):
        W = np.sqrt(w)[:, None]
        beta, *_ = np.linalg.lstsq(X * W, y * W[:, 0], rcond=None)
        res = np.abs(y - X @ beta); w = 1.0 / np.maximum(res, 5.0)
    return beta


def evaluate(name, pred, rows):
    e = np.array([pred(r) - r["lead"] for r in rows])
    within = 100 * np.mean(np.abs(e) <= 60)
    out = f"{name:34s} MAE {mae(e):5.0f}s  bias {np.mean(e):5.0f}s  within 1 min {within:4.0f}%"
    for lo, hi in [(0, 180), (180, 420), (420, 900)]:
        sub = [x for x, r in zip(e, rows) if lo <= r["lead"] < hi]
        out += f" | {lo//60}-{hi//60}m {mae(sub):4.0f}s" if sub else ""
    print(out)


print("\nHELD-OUT TEST SET (last 40%)")
evaluate("current app: timetable+holds", lambda r: r["lead"] + r["eS"], test)
evaluate("current app: traffic model", lambda r: r["lead"] + r["eT"], test)
evaluate("delay carries on (sched + delay)", lambda r: max(0, r["schedRem"] + r["delay"]), test)
for kind in ["plain", "traffic"]:
    b = fit(kind, train)
    evaluate(f"fitted ({kind})", lambda r, b=b, k=kind: max(0, float(np.dot(feats(r, k), b))), test)
    print("   coefficients:", [round(x, 3) for x in b])
