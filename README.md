# THE ROUTE

A live map of every Cambridge bus. It shows where each bus is, where it's going, and when it will reach your stop.

- **Tap a bus** to track it. The map shows the road ahead, and the card lists every remaining stop with an arrival time and the timetabled time. It also says whether the bus is early or late, and you can set an alert for 2 minutes before it reaches your stop.
- **Tap a stop** for a departure board. It mixes live buses with timetabled ones and covers the next 90 minutes.
- **NEARBY** lists the stops nearest you (with GPS) or nearest the map centre, each with its next departures.
- **ROUTES** lists what's running now and how punctual each route is.
- **SAVED** holds the stops you've starred.
- **Search** finds any stop, area or street, or a route number.

## How accurate is it?

Arrival times come from where the bus is now, read against the scheduled running times of **the exact timetabled trip it is on**. There are two ways of knowing which trip that is.

| Mode | How the trip is known | Header shows |
|---|---|---|
| **Exact** | The official Bus Open Data Service GTFS-RT feed names the trip ID for each bus. In tests, 91% of Cambridge buses had a trip ID that matched the timetable. | `EXACT TRIPS n/m` (green) |
| **Estimated** | Inferred from position along each candidate route, heading, destination and today's timetable. Running early is penalised 3×, because buses rarely run early. | `ESTIMATED TRIPS` |

When both were run on the same 9 live buses, the estimate picked the same trip as the official feed for all 7 buses that were in service.

Several things make the arrival times more accurate:
- **Each trip's own run times** are used, not an average, because peak and off-peak timetables differ.
- **Timing points:** an early bus is held at the next timing point, which is what drivers actually do.
- **Report age:** the time since the position was reported is accounted for. A bus that is sitting still doesn't count down.
- **Shapeless trips:** trips with no GTFS shape are routed stop to stop over the real road network, so every timetabled trip in the area is covered (273 patterns).

### Turning on exact mode (free, about 5 minutes)

1. Make a free account at the [Bus Open Data Service](https://data.bus-data.dft.gov.uk/) and copy your **API key** from your account page. Ask a parent if the sign-up asks for an adult.
2. In the [Cloudflare dashboard](https://dash.cloudflare.com), go to **Workers & Pages → Create → Worker**. Name it `the-route-live`, paste in [`worker/src/index.js`](worker/src/index.js), then press **Deploy**.
3. In that worker, open **Settings → Variables and Secrets → Add**. Choose the type **Secret**, name it `BODS_API_KEY`, and paste the key as its value.
4. Open `https://the-route-live.<your-subdomain>.workers.dev/live`. You should see JSON listing vehicles.
5. Put that worker URL in `RELAY_DEFAULT` in `docs/index.html`. Or, to try it first, open the site with `?relay=<worker url>`.

The worker calls the feed for the Cambridge box at most once every 10 seconds, however many people are viewing, and decodes it in well under 10 ms. That keeps it inside the free plan.

## Data
- Live positions: [bustimes.org](https://bustimes.org) / Bus Open Data Service (Open Government Licence)
- Timetables, route shapes and trip IDs: BODS GTFS for East Anglia, which needs no key
- Map: © OpenStreetMap contributors
- Stops: NaPTAN

## Design
- The UI uses [NES.css](https://github.com/nostalgic-css/NES.css) (MIT).
- Fonts are [Departure Mono](https://github.com/rektdeckard/departure-mono) (OFL, self-hosted) and Press Start 2P (OFL).
- There are two map styles, baked from the same OSM geometry: **clear** (the default) and **detailed**.

## Rebuild the data
```
bash tools/fetch_data.sh   # OSM (Overpass) + NaPTAN into data/raw/
curl -L -o data/raw/ea_gtfs.zip https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/east_anglia/
py tools/bake.py           # tiles + stops/graph/labels   (overwrites stops.json…)
py tools/bake_routes.py    # …so always run this after it: route lines + routes per stop
py tools/bake_patterns.py  # journey patterns, per-trip run times, timing points, trip-id hashes, calendars
```
The GTFS timetable changes every few weeks. Re-run the last three steps to stay current.

## Run locally
```
py -m http.server 8660 --directory docs
```
