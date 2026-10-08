"""Measure arrival-time accuracy on live buses: timetable-only vs live-traffic ETAs.

Runs the real app (docs/) in headless Chromium for N minutes. Every poll it records,
for each bus on a known trip, its predicted arrival at each of its next 12 stops
(both methods, at most one prediction per bus+stop per minute). When the bus later
crosses that stop, both predictions are scored against the actual crossing time.

    py tools/eval_eta.py 20        # minutes
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 20
PORT = 8661

HARNESS = r"""
window.__ev = { preds: new Map(), done: [], lastAlong: new Map(), started: Date.now(), polls: 0 };
window.__evStep = function () {
  const E = window.__ev, now = Date.now();
  for (const b of buses.values()) {
    if (b.gone || !b.m?.trip) continue;
    const key = b.id + '@' + b.m.trip.t0 + '@' + b.m.trip.dayStart, P = b.m.P, a = b.m.along;
    const prev = E.lastAlong.get(key);
    if (prev && a > prev.a && b.feedTime > prev.ms) {
      for (let j = 0; j < P.d.length; j++) {
        const d = P.d[j]; if (d < 0 || !(prev.a < d && a >= d)) continue;
        const tAct = prev.ms + (d - prev.a) / (a - prev.a) * (b.feedTime - prev.ms);
        const list = E.preds.get(key + '#' + j); if (!list) continue;
        for (const p of list) E.done.push(Object.assign({ lead: (tAct - p.made) / 1000, eT: (p.tr - tAct) / 1000, eS: (p.sc - tAct) / 1000, line: b.line, how: b.m.how, actSched: b.m.trip ? (tAct - (b.m.trip.dayStart + (b.m.trip.t0 + P.profiles[b.m.trip.pi][j]) * 1000)) / 1000 : null }, p.f));
        E.preds.delete(key + '#' + j);
      }
    }
    E.lastAlong.set(key, { a, ms: b.feedTime });
    const J1 = journey(b), J0 = journey(b, { traffic: false });
    if (!J1 || !J0 || J1.layover) continue;
    for (let k = 0; k < Math.min(12, J1.up.length); k++) {
      const u = J1.up[k], u0 = J0.up[k]; if (P.d[u.i] < 0) continue;
      const pk = key + '#' + u.i; let list = E.preds.get(pk); if (!list) E.preds.set(pk, list = []);
      if (list.length && now - list[list.length - 1].made < 55e3) continue;
      list.push({ made: now, tr: u.at, sc: u0.at, f: { k, delay: J1.delay, kmh: b.kmh, age: (now - b.feedTime) / 1000, traffic: J1.traffic, self: b.selfLr, schedRem: u.sched != null ? (u.sched - now) / 1000 : null, atStop: !!J1.atStop, tps: P.tp ? P.tp.slice(J1.o.prev + 1, u.i + 1).reduce((s, x) => s + x, 0) : 0, isTp: !!P.tp?.[u.i] } });
    }
  }
  E.polls++;
};
window.__evTimer = setInterval(async () => { lastPoll = 0; await poll(); window.__evStep(); }, 12000);
"""

STATS = r"""
(() => {
  const E = window.__ev, D = E.done;
  const mae = (a, k) => a.length ? Math.round(a.reduce((s, x) => s + Math.abs(x[k]), 0) / a.length) : null;
  const within = (a, k, s) => a.length ? Math.round(100 * a.filter((x) => Math.abs(x[k]) <= s).length / a.length) : null;
  const rows = [[0, 180], [180, 420], [420, 900], [900, 3600]].map(([lo, hi]) => {
    const a = D.filter((x) => x.lead >= lo && x.lead < hi);
    return { lead: `${lo / 60}-${hi / 60} min`, n: a.length, traffic_mae_s: mae(a, 'eT'), timetable_mae_s: mae(a, 'eS'),
             traffic_within_1min: within(a, 'eT', 60), timetable_within_1min: within(a, 'eS', 60) };
  });
  return { minutes: +((Date.now() - E.started) / 60000).toFixed(1), polls: E.polls, scored: D.length,
           traffic_mae_s: mae(D, 'eT'), timetable_mae_s: mae(D, 'eS'),
           traffic_within_1min: within(D, 'eT', 60), timetable_within_1min: within(D, 'eS', 60),
           traffic_bias_s: D.length ? Math.round(D.reduce((s, x) => s + x.eT, 0) / D.length) : null,
           timetable_bias_s: D.length ? Math.round(D.reduce((s, x) => s + x.eS, 0) / D.length) : null,
           rows, traffic_cells: TRAFFIC.size, traffic_obs: trafficObs, buses: buses.size };
})()
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    handler = lambda *a, **k: Quiet(*a, directory=ROOT, **k)  # noqa: E731
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": 1200, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://localhost:{PORT}/?relay=")
        page.wait_for_function("typeof PATTERNS !== 'undefined' && PATTERNS.length > 0", timeout=60000)
        page.wait_for_timeout(5000)
        page.evaluate(HARNESS)
        end = time.time() + MINUTES * 60
        while time.time() < end:
            time.sleep(60)
            s = page.evaluate(STATS)
            print(f"[{s['minutes']} min] scored={s['scored']} traffic_mae={s['traffic_mae_s']}s "
                  f"timetable_mae={s['timetable_mae_s']}s cells={s['traffic_cells']}", flush=True)
        print("FINAL " + json.dumps(page.evaluate(STATS)), flush=True)
        raw = page.evaluate("window.__ev.done")
        with open(os.path.join(os.path.dirname(ROOT), "data", "eval_raw.json"), "w") as f:
            json.dump(raw, f)
        print("raw records:", len(raw), flush=True)
        if errors:
            print("PAGE ERRORS:", errors[:5])
        br.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
