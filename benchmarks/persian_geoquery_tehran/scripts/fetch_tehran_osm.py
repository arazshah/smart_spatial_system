#!/usr/bin/env python3
"""Download the five Tehran OSM layers used by the benchmark into ../data/.

Pure standard library. Uses a fixed bounding box (not an area-name lookup),
so it does not depend on how Overpass spells "تهران".

    python scripts/fetch_tehran_osm.py
    python scripts/fetch_tehran_osm.py --endpoint https://overpass.kumi.systems/api/interpreter

Output: data/<layer>.geojson (EPSG:4326). Every feature has an `osm_id`
property ("node/123", "way/456") - the benchmark compares answers by it.
Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BBOX = "35.58,51.20,35.82,51.62"  # south,west,north,east - Tehran urban area
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

QUERIES = {
    "metro_stations": f'nwr["railway"="station"]["station"="subway"]({BBOX});out center;',
    "hospitals": f'nwr["amenity"="hospital"]({BBOX});out center;',
    "malls": f'nwr["shop"="mall"]({BBOX});out center;',
    "parks": f'nwr["leisure"="park"]["name"]({BBOX});out center;',
    "main_roads": f'way["highway"~"^(motorway|trunk|primary)$"]({BBOX});out geom;',
}


def fetch(endpoint, body, retries=3, backoff=30):
    data = urllib.parse.urlencode({"data": f"[out:json][timeout:180];{body}"}).encode()
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            endpoint, data=data, headers={"User-Agent": "s3geo-persian-geoquery-bench/0.1 (+https://s3geo.com)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            code = getattr(e, "code", None)
            if attempt < retries and (code in (429, 504) or code is None):
                wait = backoff * attempt
                print(f"  {e}; retry {attempt}/{retries - 1} in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def to_geojson(elements):
    feats = []
    for el in elements:
        tags = el.get("tags", {})
        if "lat" in el:
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif "center" in el:
            geom = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
        elif "geometry" in el and len(el["geometry"]) >= 2:
            geom = {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in el["geometry"]]}
        else:
            continue
        props = {
            "osm_id": f'{el["type"]}/{el["id"]}',
            "name": tags.get("name:fa") or tags.get("name") or "",
            **{k: tags[k] for k in ("amenity", "shop", "leisure", "railway", "highway") if k in tags},
        }
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="https://overpass-api.de/api/interpreter")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    for name, body in QUERIES.items():
        print(f"fetching {name} ...")
        fc = to_geojson(fetch(args.endpoint, body)["elements"])
        (DATA / f"{name}.geojson").write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        print(f"  {len(fc['features'])} features")
        time.sleep(5)  # be polite to the public Overpass server


if __name__ == "__main__":
    main()
