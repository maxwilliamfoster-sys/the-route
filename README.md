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

## Rebuild the map
```
bash tools/fetch_data.sh   # OSM (Overpass) + NaPTAN into data/raw/
py tools/bake.py           # -> docs/tiles + docs/data
```

## Run locally
```
py -m http.server 8660 --directory docs
```
