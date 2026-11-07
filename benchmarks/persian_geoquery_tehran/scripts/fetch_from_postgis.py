#!/usr/bin/env python3
"""Export the five benchmark layers from a PostGIS database (e.g. postgis.ir).

Alternative to fetch_tehran_osm.py when the public Overpass API is unreachable.
Writes the same files (data/<layer>.geojson, EPSG:4326, `osm_id` property), so
reference.py and run_benchmark.py work unchanged.

Connection settings come from the environment or a `.env` file - never from
the command line, so they do not land in your shell history:

    POSTGIS_HOST  POSTGIS_PORT(5432)  POSTGIS_DB  POSTGIS_USER  POSTGIS_PASSWORD
    POSTGIS_SSLMODE (optional, e.g. "require")

Step 1 - look at what the database has (read-only, prints table names/columns):

    python scripts/fetch_from_postgis.py --inspect

Step 2 - export. The defaults assume the standard osm2pgsql layout
(planet_osm_point / planet_osm_polygon / planet_osm_line, geometry column
`way`). If your tables differ, copy `postgis_layers.example.json`, edit it and
pass it with --layers-json:

    python scripts/fetch_from_postgis.py
    python scripts/fetch_from_postgis.py --layers-json my_layers.json

Only SELECT statements are issued, inside a read-only transaction.
Data (c) OpenStreetMap contributors, ODbL 1.0 (if the database is OSM-derived).
"""
import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_benchmark import load_env  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
BBOX = (51.20, 35.58, 51.62, 35.82)  # west, south, east, north (lon/lat) - same as the Overpass script

# Each layer: one or more sources. `where` is raw SQL over that table's columns;
# polygons are reduced to a representative point (like Overpass `out center`),
# lines stay lines. `{col}` marks the geometry column.
DEFAULT_LAYERS = {
    "metro_stations": [
        {"table": "planet_osm_point", "geom": "way", "mode": "point",
         "where": "railway = 'station' AND (tags -> 'station' = 'subway' OR tags -> 'subway' = 'yes')"}
    ],
    "hospitals": [
        {"table": "planet_osm_point", "geom": "way", "mode": "point", "where": "amenity = 'hospital'"},
        {"table": "planet_osm_polygon", "geom": "way", "mode": "point", "where": "amenity = 'hospital'"},
    ],
    "malls": [
        {"table": "planet_osm_point", "geom": "way", "mode": "point", "where": "shop = 'mall'"},
        {"table": "planet_osm_polygon", "geom": "way", "mode": "point", "where": "shop = 'mall'"},
    ],
    "parks": [
        {"table": "planet_osm_polygon", "geom": "way", "mode": "point",
         "where": "leisure = 'park' AND name IS NOT NULL"},
    ],
    "main_roads": [
        {"table": "planet_osm_line", "geom": "way", "mode": "line",
         "where": "highway IN ('motorway', 'trunk', 'primary')"},
    ],
}


def connect():
    import psycopg  # installed with requirements.lock

    missing = [k for k in ("POSTGIS_HOST", "POSTGIS_DB", "POSTGIS_USER", "POSTGIS_PASSWORD") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing settings: {', '.join(missing)} (set them in the environment or in .env)")
    kwargs = dict(
        host=os.environ["POSTGIS_HOST"], port=int(os.environ.get("POSTGIS_PORT", "5432")),
        dbname=os.environ["POSTGIS_DB"], user=os.environ["POSTGIS_USER"],
        password=os.environ["POSTGIS_PASSWORD"], connect_timeout=15,
    )
    if os.environ.get("POSTGIS_SSLMODE"):
        kwargs["sslmode"] = os.environ["POSTGIS_SSLMODE"]
    conn = psycopg.connect(**kwargs)
    conn.read_only = True
    return conn


def inspect(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT f_table_schema, f_table_name, f_geometry_column, srid, type FROM geometry_columns "
        "WHERE f_table_schema NOT IN ('pg_catalog','information_schema','tiger','topology') "
        "ORDER BY 1, 2"
    )
    geoms = cur.fetchall()
    print(f"{len(geoms)} geometry columns:\n")
    for schema, table, col, srid, gtype in geoms:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
            "ORDER BY ordinal_position", (schema, table))
        cols = [r[0] for r in cur.fetchall()]
        print(f"{schema}.{table}  [{col} | SRID {srid} | {gtype}]")
        print(f"    columns: {', '.join(cols)}")
    print("\nCopy this output and send it if the default export fails.")


def table_srid(cur, table, geom):
    cur.execute("SELECT srid FROM geometry_columns WHERE f_table_name=%s AND f_geometry_column=%s", (table, geom))
    row = cur.fetchone()
    if not row or not row[0]:
        sys.exit(f"cannot determine SRID of {table}.{geom} - run --inspect")
    return int(row[0])


def build_sql(src, srid):
    """SQL returning (osm_id, name, geojson) for one source, clipped to BBOX. Identifiers come from a trusted config."""
    g = f'"{src["geom"]}"'
    geom_expr = f"ST_PointOnSurface({g})" if src["mode"] == "point" else g
    w, s, e, n = BBOX
    return (
        f'SELECT osm_id, name, ST_AsGeoJSON(ST_Transform({geom_expr}, 4326)) '
        f'FROM "{src["table"]}" '
        f'WHERE ({src["where"]}) AND {g} && ST_Transform(ST_MakeEnvelope({w}, {s}, {e}, {n}, 4326), {srid})'
    )


def export_layer(cur, name, sources):
    feats = []
    for src in sources:
        srid = table_srid(cur, src["table"], src["geom"])
        cur.execute(build_sql(src, srid))
        for osm_id, nm, gj in cur.fetchall():
            geom = json.loads(gj)
            if src["mode"] == "point" and geom["type"] != "Point":
                continue
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"osm_id": f'{src["table"]}/{osm_id}', "name": nm or ""}})
    return {"type": "FeatureCollection", "features": feats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true", help="list geometry tables and columns, then exit")
    ap.add_argument("--layers-json", help="JSON file overriding DEFAULT_LAYERS (see postgis_layers.example.json)")
    ap.add_argument("--force", action="store_true", help="re-export layers that already exist")
    args = ap.parse_args()

    load_env()

    conn = connect()
    try:
        if args.inspect:
            return inspect(conn)
        layers = json.loads(pathlib.Path(args.layers_json).read_text(encoding="utf-8")) if args.layers_json else DEFAULT_LAYERS
        DATA.mkdir(exist_ok=True)
        cur = conn.cursor()
        for name, sources in layers.items():
            target = DATA / f"{name}.geojson"
            if target.exists() and not args.force:
                print(f"{name}: already exists, skipping (use --force to refresh)")
                continue
            fc = export_layer(cur, name, sources)
            target.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
            print(f"{name}: {len(fc['features'])} features")
            if not fc["features"]:
                print(f"  WARNING: {name} is empty - check table names/filters with --inspect", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
