// THE ROUTE live relay (Cloudflare Worker, free plan).
//
// Fetches the official Bus Open Data Service GTFS-RT feed for the Cambridge box
// (needs a free BODS API key, stored as the secret BODS_API_KEY), decodes the
// protobuf and returns a small JSON list. Each vehicle comes with its EXACT
// timetabled trip_id, which is what makes the page's ETAs and early/late exact
// instead of estimated.
//
//   GET /live  ->  {"ts":<feed time>,"v":[{"id","trip","start","date","route","lat","lon","bearing","ts"}]}

const BBOX = "0.04,52.15,0.22,52.25";              // minLon,minLat,maxLon,maxLat (matches the map)
const TTL_MS = 10_000;                              // one upstream fetch per 10 s, however many viewers
const ALLOWED = [/^https:\/\/maxwilliamfoster-sys\.github\.io$/, /^http:\/\/localhost(:\d+)?$/, /^http:\/\/127\.0\.0\.1(:\d+)?$/];

let memo = { at: 0, body: null };                   // per-isolate cache (the Cache API is a no-op on workers.dev)

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED.some((r) => r.test(origin)) ? origin : "https://maxwilliamfoster-sys.github.io",
      "Vary": "Origin",
    };
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: { ...cors, "Access-Control-Allow-Methods": "GET" } });
    if (url.pathname !== "/live") return new Response("THE ROUTE live relay: GET /live", { headers: cors });
    if (!env.BODS_API_KEY) return json({ error: "BODS_API_KEY secret not set" }, 500, cors);

    if (!memo.body || Date.now() - memo.at > TTL_MS) {
      const r = await fetch(`https://data.bus-data.dft.gov.uk/api/v1/gtfsrtdatafeed/?boundingBox=${BBOX}&api_key=${env.BODS_API_KEY}`,
        { headers: { "User-Agent": "TheRoute/1.0 (Cambridge bus map)" } });
      if (!r.ok) {
        if (memo.body) return json(memo.body, 200, cors);     // serve the last good copy rather than nothing
        return json({ error: `BODS ${r.status}` }, 502, cors);
      }
      memo = { at: Date.now(), body: decodeFeed(new Uint8Array(await r.arrayBuffer())) };
    }
    return json(memo.body, 200, cors);
  },
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), { status, headers: { ...cors, "content-type": "application/json", "cache-control": "no-store" } });
}

// ---- minimal protobuf reader for the GTFS-RT fields we need -------------
function fields(buf, start = 0, end = buf.length) {
  const out = [];
  let i = start;
  const varint = () => {
    let r = 0, s = 1, b;
    do { b = buf[i++]; r += (b & 0x7f) * s; s *= 128; } while (b & 0x80);
    return r;
  };
  while (i < end) {
    const key = varint(), f = Math.floor(key / 8), wt = key & 7;
    if (wt === 0) out.push([f, varint()]);
    else if (wt === 1) { out.push([f, new DataView(buf.buffer, buf.byteOffset + i, 8).getFloat64(0, true)]); i += 8; }
    else if (wt === 2) { const n = varint(); out.push([f, [i, i + n]]); i += n; }
    else if (wt === 5) { out.push([f, new DataView(buf.buffer, buf.byteOffset + i, 4).getFloat32(0, true)]); i += 4; }
    else break;                                               // groups: not used by GTFS-RT
  }
  return out;
}
const td = new TextDecoder();
const str = (buf, [a, b]) => td.decode(buf.subarray(a, b));

export function decodeFeed(buf) {
  let ts = 0;
  const v = [];
  for (const [f, val] of fields(buf)) {
    if (f === 1) for (const [hf, hv] of fields(buf, ...val)) { if (hf === 3) ts = hv; }   // header.timestamp
    if (f !== 2) continue;                                                              // entity
    for (const [ef, ev] of fields(buf, ...val)) {
      if (ef !== 4) continue;                                                           // entity.vehicle
      const o = {};
      for (const [vf, vv] of fields(buf, ...ev)) {
        if (vf === 1) for (const [tf, tv] of fields(buf, ...vv)) {                      // trip
          if (tf === 1) o.trip = str(buf, tv); else if (tf === 2) o.start = str(buf, tv);
          else if (tf === 3) o.date = str(buf, tv); else if (tf === 5) o.route = str(buf, tv);
        } else if (vf === 2) for (const [pf, pv] of fields(buf, ...vv)) {               // position
          if (pf === 1) o.lat = +pv.toFixed(6); else if (pf === 2) o.lon = +pv.toFixed(6); else if (pf === 3) o.bearing = Math.round(pv);
        } else if (vf === 5) o.ts = vv;                                                 // timestamp
        else if (vf === 8) for (const [df, dv] of fields(buf, ...vv)) {                 // vehicle descriptor
          if (df === 1) o.id = str(buf, dv); else if (df === 2 && !o.id) o.id = str(buf, dv);
        }
      }
      if (o.lat != null) v.push(o);
    }
  }
  return { ts, v };
}
