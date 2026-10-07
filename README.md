# THE ROUTE

A live, gamified pixel-art map of Cambridge. Every bus on it is a real bus, shown where it is right now and driving along the real roads. All 728 real bus stops are on the map.

THE ROUTE is a sister project to THE CUT: same look, but here the city is real and the buses are the characters.

## How it works
- **Map**: `tools/bake.py` turns OpenStreetMap data into 64 pixel-art tiles at 3 m per pixel. The data comes from Overpass and is saved to `data/raw/`, which is not committed. The bake also writes the stops, labels and a bus-drivable road graph into `docs/data/`.
- **Buses**: the page polls `bustimes.org/vehicles.json` for the Cambridge box every 12 s. That feed is Bus Open Data Service data under the Open Government Licence. It needs no key and allows any origin (CORS `*`).
- **Movement**: each new position is joined to the last one by A* over the OSM road graph, so buses turn real corners. If no sensible road route is found, the bus moves in a straight line instead.
- **Stops**: NaPTAN (Cambridgeshire, ATCO 050). Its easting/northing values are converted from OSGB36 to WGS84 inside the bake script.
- **Game** (saved in localStorage):
  - Spot buses to fill your Busdex.
  - GPS mode: catch a real bus within 80 m, or check in at a real stop within 45 m.
  - Daily missions, XP and levels.
  - Every bus gets a stable persona (name and trait) generated from its fleet number.

## Design
- The UI is built with [NES.css](https://github.com/nostalgic-css/NES.css) (MIT): panels, buttons, progress bars, speech balloons and icons.
- Fonts:
  - [Departure Mono](https://github.com/rektdeckard/departure-mono) (SIL OFL), self-hosted in `docs/fonts/`, for body text and map labels.
  - [Press Start 2P](https://fonts.google.com/specimen/Press+Start+2P) (OFL) for headings.
- Two map styles are baked from the same geometry:
  - **clear** (default): flat, calm colours with white roads, so buses and route lines read first.
  - **detailed**: the textured pixel-art city.
- The simulation layer favours clarity over realism:
  - Each bus is a bold route badge with a direction pointer.
  - Routes are transit-map lines taken from the official timetable shapes.
  - Stops are white dots with a black ring.
  - Tapping a bus or stop highlights its route(s) and fades everything else.

## Rebuild the map
```
bash tools/fetch_data.sh   # OSM (Overpass) + NaPTAN into data/raw/
curl -L -o data/raw/ea_gtfs.zip https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/east_anglia/
py tools/bake.py           # -> docs/tiles/{clear,detail} + docs/data
py tools/bake_routes.py    # -> docs/data/routes.json + routes-per-stop in stops.json
py tools/bake_patterns.py  # -> docs/data/patterns.json (journey patterns, run times, trips, calendars for ETAs)
```

## Tracking and arrival times

Tapping a bus starts **tracking** it.

It is matched to a timetabled journey pattern from the official BODS GTFS. The page projects the bus onto each candidate shape for its line, then scores each candidate on:
- distance from the shape
- heading
- how well its destination matches
- how close the nearest timetabled trip is in time

From the matched pattern it shows:
- the stops still to come, with ETAs from the scheduled running time between stops
- whether the bus is early or late, compared against today's trips (using the calendars)
- where the bus is (at a stop, or between two stops), plus its street and area
- its progress along the whole journey
- an arrival time at the terminus
- a reminder 2 min before the bus reaches a chosen stop

Stops show a departure board for the next hour, mixing LIVE buses with timetabled ones. The search box finds stops by name, area or street, and routes by number.

## Run locally
```
py -m http.server 8660 --directory docs
```
