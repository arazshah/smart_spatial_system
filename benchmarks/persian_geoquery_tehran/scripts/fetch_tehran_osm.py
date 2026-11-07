#!/usr/bin/env python3
"""Download the five Tehran OSM layers used by the benchmark into ../data/.

Pure standard library. Uses a fixed bounding box (not an area-name lookup),
so it does not depend on how Overpass spells "تهران".

    python scripts/fetch_tehran_osm.py
    python scripts/fetch_tehran_osm.py --force            # re-download everything
    python scripts/fetch_tehran_osm.py --endpoint https://overpass.kumi.systems/api/interpreter

Output: data/<layer>.geojson (EPSG:4326). Every feature has an `osm_id`
property ("node/123", "way/456") - the benchmark compares answers by it.
Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import http.client
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from geoutil import DEDUPE_M, dedupe_points, record_provenance  # noqa: E402

BBOX = "35.58,51.20,35.82,51.62"  # south,west,north,east - Tehran urban area
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

QUERIES = {
    "metro_stations": f'nwr["railway"="station"]["station"="subway"]({BBOX});out center;',
    "hospitals": f'nwr["amenity"="hospital"]({BBOX});out center;',
    "malls": f'nwr["shop"="mall"]({BBOX});out center;',
    "parks": f'nwr["leisure"="park"]["name"]({BBOX});out center;',
    "main_roads": f'way["highway"~"^(motorway|trunk|primary)$"]({BBOX});out geom;',
}


RETRY_CODES = (429, 502, 503, 504)  # Overpass busy / gateway errors
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def _fetch_once_with_retries(endpoint, data, retries, backoff):
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            endpoint, data=data, headers={"User-Agent": "s3geo-persian-geoquery-bench/0.1 (+https://s3geo.com)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode("utf-8"))
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            http.client.IncompleteRead,  # response cut off mid-transfer (busy server)
            http.client.RemoteDisconnected,
            ConnectionError,
            TimeoutError,
            json.JSONDecodeError,  # truncated/garbled body
        ) as e:
            code = getattr(e, "code", None)
            retryable = not isinstance(e, urllib.error.HTTPError) or code in RETRY_CODES
            if attempt < retries and retryable:
                wait = backoff * attempt
                print(f"  {type(e).__name__}: {e}; retry {attempt}/{retries - 1} in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def fetch(endpoints, body, retries=3, backoff=30):
    """Try each endpoint in turn (with retries). Returns (parsed_json, endpoint_used)."""
    data = urllib.parse.urlencode({"data": f"[out:json][timeout:180];{body}"}).encode()
    last = None
    for endpoint in endpoints:
        try:
            return _fetch_once_with_retries(endpoint, data, retries, backoff), endpoint
        except Exception as e:  # noqa: BLE001 - fall through to the next mirror
            last = e
            print(f"  {endpoint} failed ({type(e).__name__}); trying next endpoint" if endpoint != endpoints[-1] else f"  {endpoint} failed", file=sys.stderr)
    raise last


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
    # deterministic order (nodes, ways, relations; then id) so de-duplication keeps the same survivor every run
    rank = {"node": 0, "way": 1, "relation": 2}
    feats.sort(key=lambda f: (rank[f["properties"]["osm_id"].split("/")[0]], int(f["properties"]["osm_id"].split("/")[1])))
    return {"type": "FeatureCollection", "features": feats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", help="use only this Overpass endpoint (default: overpass-api.de, then a mirror)")
    ap.add_argument("--force", action="store_true", help="re-download layers that already exist")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    for name, body in QUERIES.items():
        target = DATA / f"{name}.geojson"
        if target.exists() and not args.force:
            print(f"{name}: already downloaded, skipping (use --force to refresh)")
            continue
        print(f"fetching {name} ...")
        raw, used = fetch([args.endpoint] if args.endpoint else MIRRORS, body)
        fc = to_geojson(raw["elements"])
        removed = 0
        if name in DEDUPE_M:
            before = len(fc["features"])
            fc["features"] = dedupe_points(fc["features"], DEDUPE_M[name])
            removed = before - len(fc["features"])
        target.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        record_provenance(DATA, name, f"overpass:{used}", len(fc["features"]), removed)
        print(f"  {len(fc['features'])} features (removed {removed} near-duplicates)")
        time.sleep(5)  # be polite to the public Overpass server


if __name__ == "__main__":
    main()
