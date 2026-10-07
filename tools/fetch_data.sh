#!/usr/bin/env bash
# Download the raw OSM + NaPTAN data that tools/bake.py needs into data/raw/.
# Overpass often 504s when busy; this retries across two mirrors.
set -u
cd "$(dirname "$0")/../data/raw" 2>/dev/null || { mkdir -p "$(dirname "$0")/../data/raw"; cd "$(dirname "$0")/../data/raw"; }
B="52.150,0.040,52.250,0.220"
get() {
  for i in 1 2 3 4 5 6; do
    for m in https://overpass-api.de/api/interpreter https://overpass.private.coffee/api/interpreter; do
      code=$(curl -s -m 300 -A "TheRoute/0.1 (personal project)" --data-urlencode "data=[out:json][timeout:240];$1" "$m" -o "$2" -w "%{http_code}")
      if [ "$code" = 200 ] && head -c 20 "$2" | grep -q '{'; then echo "$2 ok"; return; fi
      echo "$2 $code, retrying"; sleep 8
    done
  done
  echo "$2 FAILED"
}
get "(way[highway]($B);way[railway~'rail|light_rail|disused|abandoned']($B););out geom;" roads.json
get "(way[building]($B);relation[building]($B););out geom;" buildings.json
get "(way[waterway]($B);way[natural=water]($B);relation[natural=water]($B););out geom;" water.json
get "(way[landuse]($B);relation[landuse]($B););out geom;" landuse.json
get "(way[leisure]($B);relation[leisure]($B);way[natural~'wood|scrub|grassland|wetland']($B);relation[natural~'wood|scrub']($B);way[amenity~'parking|university|college|school|hospital']($B);relation[amenity~'university|college|hospital']($B););out geom;" leisure.json
get "(node[place~'suburb|neighbourhood|village|quarter|town|city']($B);node[railway=station]($B);way[amenity~'college|university'][name]($B);relation[amenity~'college|university'][name]($B);way[tourism~'attraction|museum'][name]($B);way[shop=mall][name]($B););out center tags;" places.json
curl -s -m 120 "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=050&dataFormat=csv" -o naptan050.csv && echo "naptan050.csv ok"
