#!/usr/bin/env python3
"""
End-to-end demo: OpenStreetMap data in, natural-language questions
answered by s3geo, map/table/PDF out - three steps, plain Python.

    1. DOWNLOAD  - pull real OSM vector data for Urmia (main roads, public
                   transit stops, shopping centers) via the Overpass API
                   and save it to examples/urmia_real_estate/osm_download/.
                   Skipped on later runs if the files are already there.
    2. CONFIGURE - load LLM_* / OPENAI_* settings from .env (python-dotenv,
                   the same call api/main.py makes) so s3geo.query()'s
                   OpenAICompatibleLLMClient can find them.
    3. ASK       - call s3geo.query() (smart_spatial_system 0.3.0's one-call
                   library path: LLM planning -> deterministic DAG -> plugin
                   execution) with plain-language Persian questions against
                   the downloaded layers plus a small hand-written set of
                   candidate properties, and save whatever each question's
                   plan produces: a map layer, a ranked table, a PDF report.

The point of the example is that step 3 is the only part that needs to
understand anything: three different questions over the same data produce
three different kinds of output, entirely from the LLM reading the query
and picking the right operations out of orchestrator/planning/op_catalog.py
(display_vector, real_estate_spatial_enrich + real_estate_score +
rank_features, build_report + render_pdf) - no branching in this script
decides that, the plan does.

Requires a real LLM key (see .env.example: LLM_API_KEY / AVALAI_API_KEY /
OPENAI_API_KEY + OPENAI_BASE_URL) and network access to both the LLM
endpoint and https://overpass-api.de. No server needed - this only uses
the s3geo library, like s3geo_quickstart.py.

    cp .env.example .env   # fill in your LLM key
    python examples/urmia_real_estate_ranking.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

import s3geo

# --------------------------------------------------------------------- #
# Data config: where layers come from.
# --------------------------------------------------------------------- #

EXAMPLE_DIR = Path(__file__).parent
DATA_DIR = EXAMPLE_DIR / "urmia_real_estate"
OSM_DIR = DATA_DIR / "osm_download"
OUTPUT_DIR = DATA_DIR / "output"

# Urmia city, approximate bounding box (south, west, north, east).
URMIA_BBOX = (37.47, 44.98, 37.60, 45.15)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# One Overpass QL query body per downloaded layer. `{bbox}` is filled in
# with "south,west,north,east". Roads come back as ways with inline
# geometry (`out geom`); POIs are nodes, so a plain `out` is enough.
OSM_QUERIES: dict[str, str] = {
    "main_roads": (
        '(way["highway"~"^(motorway|trunk|primary|secondary)$"]({bbox}););'
        "out geom;"
    ),
    "transit_hubs": (
        '(node["amenity"="bus_station"]({bbox});'
        'node["highway"="bus_stop"]({bbox}););'
        "out;"
    ),
    "shopping_centers": (
        '(node["shop"="mall"]({bbox});'
        'node["shop"="supermarket"]({bbox}););'
        "out;"
    ),
}

# --------------------------------------------------------------------- #
# Query config: one natural-language question per desired output kind.
# The LLM plans each one independently against the same input layers -
# nothing here tells it which operations to run.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class Scenario:
    output_name: str
    query: str


SCENARIOS: list[Scenario] = [
    Scenario(
        output_name="map_candidate_properties",
        query="ملک‌های کاندید ارومیه را روی نقشه نمایش بده.",
    ),
    Scenario(
        output_name="ranking_table",
        query=(
            "برای هر ملک کاندید در ارومیه فاصله تا نزدیک‌ترین ایستگاه حمل‌ونقل "
            "عمومی، نزدیک‌ترین مرکز خرید و نزدیک‌ترین خیابان اصلی را محاسبه کن، "
            "سپس هر ملک را بر اساس این فاصله‌ها، ریسک سیل و زلزله و آتش‌سوزی، و "
            "قرارگیری داخل محدوده مجاز ساخت‌وساز امتیازدهی کن و رتبه‌بندی نهایی "
            "را بر اساس امتیاز نزولی برگردان."
        ),
    ),
    Scenario(
        output_name="pdf_report",
        query=(
            "ملک‌های کاندید ارومیه را بر اساس فاصله تا ایستگاه‌های حمل‌ونقل "
            "عمومی، مراکز خرید و خیابان‌های اصلی، ریسک سیل/زلزله/آتش‌سوزی و "
            "قرارگیری داخل محدوده مجاز ساخت‌وساز امتیازدهی و رتبه‌بندی کن، و "
            "یک گزارش PDF قابل چاپ از نتیجه تهیه کن."
        ),
    ),
]

# Told to the LLM alongside each query, so it does not have to guess which
# input layer plays which role in the real-estate scoring formula (Urmia
# has no metro/subway, so `transit_hubs` stands in for the "metro" role -
# see docs on real_estate_spatial_enrich in orchestrator/planning/op_catalog.py).
SYSTEM_HINTS = (
    "لایه‌های ورودی موجود:\n"
    "- properties: ملک‌های کاندید (نقطه‌ای)\n"
    "- main_roads: خیابان‌های اصلی ارومیه (خطی) - نقش 'main_roads'\n"
    "- transit_hubs: پایانه‌ها/ایستگاه‌های حمل‌ونقل عمومی ارومیه (نقطه‌ای) - "
    "چون ارومیه مترو ندارد، این لایه نقش 'metro' را در فرمول امتیازدهی ایفا می‌کند\n"
    "- shopping_centers: مراکز خرید ارومیه (نقطه‌ای) - نقش 'malls'\n"
    "- construction_zones: محدوده مجاز ساخت‌وساز (چندضلعی) - نقش 'allowed_zones'"
)


# --------------------------------------------------------------------- #
# Step 1: download OSM data.
# --------------------------------------------------------------------- #


def _overpass_element_to_feature(element: dict[str, Any]) -> dict[str, Any] | None:
    tags = element.get("tags") or {}
    element_type = element.get("type")

    if element_type == "node":
        geometry = {
            "type": "Point",
            "coordinates": [element["lon"], element["lat"]],
        }
    elif element_type == "way":
        points = element.get("geometry") or []
        if len(points) < 2:
            return None
        geometry = {
            "type": "LineString",
            "coordinates": [[pt["lon"], pt["lat"]] for pt in points],
        }
    else:
        return None

    return {
        "type": "Feature",
        "id": f"osm-{element_type}-{element['id']}",
        "properties": {"osm_id": element["id"], "osm_type": element_type, **tags},
        "geometry": geometry,
    }


def _fetch_overpass(query_body: str, *, attempts: int = 3) -> dict[str, Any]:
    south, west, north, east = URMIA_BBOX
    ql = f"[out:json][timeout:60];{query_body.format(bbox=f'{south},{west},{north},{east}')}"

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(OVERPASS_URL, data={"data": ql}, timeout=90)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2**attempt)
    raise SystemExit(
        f"Could not reach Overpass API after {attempts} attempts: {last_error}"
    )


def download_osm_layers(*, force: bool = False) -> dict[str, Path]:
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for name, query_body in OSM_QUERIES.items():
        dest = OSM_DIR / f"{name}.geojson"
        if dest.exists() and not force:
            print(f"  {name}: already downloaded -> {dest}")
            paths[name] = dest
            continue

        print(f"  {name}: downloading from Overpass API ...")
        raw = _fetch_overpass(query_body)
        features = [
            feature
            for element in raw.get("elements") or []
            if (feature := _overpass_element_to_feature(element)) is not None
        ]
        feature_collection = {"type": "FeatureCollection", "features": features}
        dest.write_text(
            json.dumps(feature_collection, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  {name}: {len(features)} feature(s) -> {dest}")
        paths[name] = dest

    return paths


# --------------------------------------------------------------------- #
# Step 3: save whatever a plan produced.
# --------------------------------------------------------------------- #


def save_output(output: Any, dest_stub: Path) -> Path:
    # PDF (PDFOut from plugins/pdf_renderer.py): bytes, or an HTML fallback
    # if WeasyPrint failed to render but the report itself built fine.
    if hasattr(output, "pdf_bytes"):
        pdf_bytes = getattr(output, "pdf_bytes", b"") or b""
        if pdf_bytes:
            dest = dest_stub.with_suffix(".pdf")
            dest.write_bytes(pdf_bytes)
            return dest
        html = getattr(output, "html", "") or ""
        if html:
            dest = dest_stub.with_suffix(".html")
            dest.write_text(html, encoding="utf-8")
            return dest
        raise RuntimeError(f"PDF render failed: {getattr(output, 'errors', None)}")

    # Vector output (VectorOut, or a display_vector_layer-style dict).
    geojson: dict[str, Any] | None = None
    if hasattr(output, "to_json"):
        geojson = json.loads(output.to_json(default=str))
    elif isinstance(output, dict):
        if output.get("type") == "FeatureCollection":
            geojson = output
        elif isinstance(output.get("layer"), dict):
            geojson = output["layer"].get("geojson")
        elif isinstance(output.get("geojson"), dict):
            geojson = output["geojson"]

    if geojson is not None:
        dest = dest_stub.with_suffix(".geojson")
        dest.write_text(
            json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return dest

    # Fallback: whatever JSON-serializable shape we can get out of it
    # (a report dict/ReportOut, a summary, ...).
    if hasattr(output, "to_dict"):
        payload = output.to_dict()
    elif isinstance(output, dict):
        payload = output
    else:
        payload = {"repr": repr(output)}
    dest = dest_stub.with_suffix(".json")
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return dest


def load_geojson(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    print("== Step 1/3: OSM data for Urmia ==")
    osm_paths = download_osm_layers()

    print("\n== Step 2/3: LLM settings from .env ==")
    load_dotenv()
    print("  loaded (LLM_API_KEY / OPENAI_BASE_URL / ... read by s3geo.query())")

    layers = {
        "properties": load_geojson(DATA_DIR / "properties.geojson"),
        "main_roads": load_geojson(osm_paths["main_roads"]),
        "transit_hubs": load_geojson(osm_paths["transit_hubs"]),
        "shopping_centers": load_geojson(osm_paths["shopping_centers"]),
        "construction_zones": load_geojson(DATA_DIR / "construction_zones.geojson"),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n== Step 3/3: ask s3geo ==")
    for scenario in SCENARIOS:
        print(f"\nQuery: {scenario.query}")
        try:
            result = s3geo.query(
                scenario.query, layers=layers, system_hints=SYSTEM_HINTS
            )
        except Exception as exc:
            print(f"  failed: {exc}")
            continue

        print(f"  goal:       {result.goal}")
        print(f"  operations: {', '.join(result.operations)}")

        dest = save_output(result.output, OUTPUT_DIR / scenario.output_name)
        print(f"  saved ->    {dest}")


if __name__ == "__main__":
    main()
