"""Where do arrival predictions go wrong? Reads data/eval_raw.json from tools/eval_eta.py."""
import json
import os
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = json.load(open(os.path.join(ROOT, "data", "eval_raw.json")))
print(len(R), "scored predictions")


def summ(rows, label):
    if not rows:
        return
    eT = [r["eT"] for r in rows]; eS = [r["eS"] for r in rows]
    mae = lambda e: round(st.mean(abs(x) for x in e))
    print(f"{label:38s} n={len(rows):5d}  traffic MAE {mae(eT):4d}s bias {round(st.mean(eT)):5d}s   "
          f"timetable MAE {mae(eS):4d}s bias {round(st.mean(eS)):5d}s")


def by(name, fn, keys):
    print(f"\n--- by {name}")
    for k in keys:
        summ([r for r in R if fn(r) == k], f"{name}={k}")


lead = lambda r: "0-3" if r["lead"] < 180 else "3-7" if r["lead"] < 420 else "7-15" if r["lead"] < 900 else "15+"
summ(R, "ALL")
by("lead", lead, ["0-3", "3-7", "7-15", "15+"])
dl = lambda r: "none" if r.get("delay") is None else "early>1m" if r["delay"] < -60 else "late>3m" if r["delay"] > 180 else "late1-3m" if r["delay"] > 60 else "on time"
by("delay now", dl, ["early>1m", "on time", "late1-3m", "late>3m", "none"])
by("stopped now", lambda r: r.get("kmh") is not None and r["kmh"] < 3, [True, False])
by("timing points between", lambda r: min(r.get("tps", 0), 2), [0, 1, 2])
by("target is timing point", lambda r: r.get("isTp"), [True, False])
by("how", lambda r: r.get("how"), ["exact", "matched"])
fa = lambda r: "<20s" if r["age"] < 20 else "20-45s" if r["age"] < 45 else ">45s"
by("feed age", fa, ["<20s", "20-45s", ">45s"])
tr = lambda r: "none" if not r.get("traffic") else "slow>1.2" if r["traffic"] > 1.2 else "fast<0.85" if r["traffic"] < 0.85 else "normal"
by("traffic ahead", tr, ["slow>1.2", "normal", "fast<0.85", "none"])

# does today's delay carry on to later stops? actual delay at the stop vs delay when predicted
print("\n--- delay persistence (actual delay at stop vs delay when predicted), by lead")
for L in ["0-3", "3-7", "7-15"]:
    rows = [r for r in R if lead(r) == L and r.get("delay") is not None and r.get("actSched") is not None and abs(r["delay"]) < 1500]
    if len(rows) < 30:
        continue
    xs = [r["delay"] for r in rows]; ys = [r["actSched"] for r in rows]
    mx, my = st.mean(xs), st.mean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sum((x - mx) ** 2 for x in xs) or 1)
    print(f"lead {L}: actual_delay ≈ {b:.2f} × current_delay + {my - b * mx:.0f}s   (n={len(rows)})")

# remaining-time scaling: actual remaining vs timetable-model remaining
print("\n--- actual remaining time ÷ timetable-model remaining time, by lead (median)")
for L in ["0-3", "3-7", "7-15", "15+"]:
    rows = [r for r in R if lead(r) == L]
    rat = [r["lead"] / max(30, r["lead"] + r["eS"]) for r in rows]
    if rat:
        print(f"lead {L}: median {st.median(rat):.2f}  (n={len(rows)})")
