#!/usr/bin/env python3
"""
Rank candidate properties in Urmia against real vector layers (public
transit hubs, shopping centers, main roads, allowed construction zones),
and pull back every output the system produces for one request: a map
layer, a ranking table, and a downloadable PDF report.

This drives the same real-estate ranking workflow as
`examples/real_estate_ranking_payload.fake.json`, but with a full set of
named vector inputs instead of one pre-mixed payload, and over HTTP like
`query_via_http.py` - no client SDK, no LLM key needed (the query is
matched by keyword, not planned by an LLM; see
`smart_spatial_system/application/services/query_execution/real_estate_classifier.py`).

The five GeoJSON layers in `examples/urmia_real_estate/` are hand-written
sample data anchored on real Urmia landmarks and boulevards (Enghelab
Square, Daneshgah Blvd, Shahid Beheshti Blvd, the university, the Nazlu
riverside), the same convention as `examples/accessibility/`: real places,
illustrative/approximate coordinates, not surveyed GIS data, so the
example runs fully offline. Urmia has no metro/subway, so the
`metro_stations` layer stands in for the city's real public-transit hubs
(the interurban bus terminal and BRT/bus stops) - the ranking formula's
"metro" role is just the nearest major transit hub.

Start a server first:

    smart-spatial-api serve --port 8000
    # or, from a checkout: uvicorn api.main:app --reload

Then:

    python examples/urmia_real_estate_ranking.py
    SMART_SPATIAL_API_KEY=... python examples/urmia_real_estate_ranking.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("SMART_SPATIAL_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("SMART_SPATIAL_API_KEY")

DATA_DIR = Path(__file__).parent / "urmia_real_estate"
OUTPUT_DIR = Path(__file__).parent / "urmia_real_estate" / "output"

QUERY = (
    "ملک‌هایی را پیدا کن که کمتر از ۵۰۰ متر به مترو یا مرکز خرید نزدیک "
    "باشند، نزدیک خیابان اصلی باشند، ریسک سیل و زلزله و آتش‌سوزی پایینی "
    "داشته باشند، داخل محدوده مجاز ساخت‌وساز باشند، به هر ملک امتیاز بده "
    "و گزارش PDF شامل نقشه و جدول رتبه‌بندی تولید کن"
)


def load(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.geojson").read_text(encoding="utf-8"))


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if API_KEY:
        req.add_header("X-API-Key", API_KEY)

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach {BASE_URL}: {exc.reason}. Is the server running?"
        ) from exc


def download_file(url_path: str, dest: Path) -> None:
    req = urllib.request.Request(f"{BASE_URL}{url_path}", method="GET")
    if API_KEY:
        req.add_header("X-API-Key", API_KEY)
    with urllib.request.urlopen(req, timeout=120) as response:
        dest.write_bytes(response.read())


def main() -> None:
    health = request("GET", "/health")
    print(f"Server: {health['status']} · {len(health['plugin_modules'])} plugin modules")

    inputs = {
        "properties": load("properties"),
        "metro_stations": load("metro_stations"),
        "shopping_centers": load("shopping_centers"),
        "main_roads": load("main_roads"),
        "construction_zones": load("construction_zones"),
    }

    print(f"\nQuery: {QUERY}")
    response = request("POST", "/query", {"query": QUERY, "inputs": inputs})

    request_id = response.get("request_id")
    print(f"\nRequest id: {request_id}")
    print(f"Status:     {response.get('status')}")
    print(f"Answer:     {response.get('answer') or response.get('message')}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Map: the ranked layer, ready for a map client -------------------
    for layer in response.get("layers") or []:
        summary = layer.get("summary") or {}
        print(
            f"\nMap layer '{layer.get('name')}': {summary.get('feature_count', '?')}"
            " feature(s)"
        )
        geojson = layer.get("geojson")
        if geojson:
            map_path = OUTPUT_DIR / "ranked_properties.geojson"
            map_path.write_text(
                json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  saved map layer -> {map_path}")

    # --- Table: the ranking, with score and rejection reasons ------------
    outputs = response.get("outputs") or {}
    for table in outputs.get("tables") or []:
        if table.get("id") != "property_ranking":
            continue
        print("\nRanking:")
        for row in table.get("rows") or []:
            print(
                f"  #{row.get('rank')} {row.get('name'):<28}"
                f" score={row.get('score')}"
                f" road={row.get('distance_to_main_road_m')}m"
                f" poi={row.get('best_poi_distance_m')}m"
                f" zone_ok={row.get('in_allowed_zone')}"
            )

    for table in outputs.get("tables") or []:
        if table.get("id") != "rejected_properties":
            continue
        rows = table.get("rows") or []
        if rows:
            print("\nRejected:")
            for row in rows:
                print(f"  {row.get('name')}: {', '.join(row.get('reasons') or [])}")

    # --- File: the PDF report ---------------------------------------------
    for document in outputs.get("documents") or []:
        download_url = document.get("download_url")
        filename = document.get("filename") or document.get("name")
        if not download_url or not filename:
            continue
        dest = OUTPUT_DIR / filename
        download_file(download_url, dest)
        print(f"\n{document.get('format', 'document').upper()} report -> {dest}")

    for warning in response.get("warnings") or []:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
