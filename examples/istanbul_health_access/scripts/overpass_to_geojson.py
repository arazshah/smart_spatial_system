#!/usr/bin/env python3
"""Convert raw Overpass API JSON into standard GeoJSON.

Pure standard library - no ``osm2geojson``/``shapely`` dependency. Carried
over from smart-spatial-vienna-accessibility (same author, same case-study
family) and extended here with a ``centers`` mode for ``out center;``
responses. Written because that repo's sandbox had no route to either
overpass-api.de or pypi.org, so both the "export GeoJSON from Overpass
Turbo" and the "pip install osm2geojson" paths were blocked; the sandbox
this repo was scaffolded in had the same restriction.

Handles the element shapes this case study's queries produce:
  - nodes      -> Point features               (``out geom;`` node results)
  - centers    -> Point features               (``out center;`` - nodes by
                  their own lat/lon, ways by their ``center`` field; this is
                  what data/README.md query 1, hospitals+clinics, returns)
  - ways       -> Polygon features              (closed ways, ``out geom;``)
  - relations  -> Polygon/MultiPolygon features (mahalle boundaries -
                  standard OSM multipolygon ring assembly from outer/inner
                  member ways, incl. hole-to-ring assignment)

Usage:
    python scripts/overpass_to_geojson.py centers   data/raw/hospitals.json           data/raw/hospitals.geojson
    python scripts/overpass_to_geojson.py relations data/raw/mahalle_boundaries.json  data/raw/mahalle_boundaries.geojson

Each <input.json> is the raw response body of an Overpass ``[out:json]``
query (see data/README.md for the exact queries used in this repo).
"""
import argparse
import json
import sys


def nodes_to_points_fc(elements):
    features = []
    for el in elements:
        if el.get("type") != "node":
            continue
        tags = el.get("tags", {})
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
                "properties": {"osm_id": el["id"], "osm_type": "node", **tags},
            }
        )
    return {"type": "FeatureCollection", "features": features}, []



def centers_to_points_fc(elements):
    """``out center;`` results -> Point features.

    A node carries its own ``lat``/``lon``; a way (or relation) carries a
    ``center`` dict instead of a full ``geometry``. Anything with neither is
    reported as skipped rather than silently dropped - a large hospital
    mapped as a relation would land here, and data/README.md caveat 3 says
    to count those, not lose them.
    """
    features = []
    skipped = []
    for el in elements:
        tags = el.get("tags", {})
        if "lat" in el and "lon" in el:
            lon, lat = el["lon"], el["lat"]
        elif isinstance(el.get("center"), dict):
            lon, lat = el["center"]["lon"], el["center"]["lat"]
        else:
            skipped.append(f"{el.get('type')}/{el.get('id')} has no lat/lon and no center")
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "osm_id": el.get("id"),
                    "osm_type": el.get("type"),
                    **tags,
                },
            }
        )
    notes = []
    if skipped:
        notes.append(f"{len(skipped)} element(s) skipped, no usable coordinate:")
        notes.extend(skipped[:20])
        if len(skipped) > 20:
            notes.append(f"... and {len(skipped) - 20} more")
    return {"type": "FeatureCollection", "features": features}, notes


def ways_to_polygon_fc(elements):
    features = []
    skipped = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry")
        tags = el.get("tags", {})
        if not geom or len(geom) < 4:
            skipped.append(el["id"])
            continue
        coords = [[n["lon"], n["lat"]] for n in geom]
        if coords[0] != coords[-1]:
            # not closed - shouldn't happen for e.g. leisure=park ways, but guard
            coords.append(coords[0])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"osm_id": el["id"], "osm_type": "way", **tags},
            }
        )
    notes = [f"way {i}: no/degenerate geometry - skipped" for i in skipped]
    return {"type": "FeatureCollection", "features": features}, notes


def assemble_rings(segments):
    """segments: list of Overpass ``geometry`` arrays (each a list of
    ``{"lat":.., "lon":..}`` dicts - one OSM way's node chain). Returns a
    list of closed rings (each a list of ``(lon, lat)`` tuples) by
    chaining segments that share an endpoint, as OSM multipolygon
    assembly requires - a boundary relation typically splits its outer
    ring across many member ways that only close once concatenated in
    the right order/orientation."""
    remaining = [[(n["lon"], n["lat"]) for n in seg] for seg in segments if len(seg) >= 2]
    rings = []
    while remaining:
        ring = list(remaining.pop(0))
        progress = True
        while ring[0] != ring[-1] and progress:
            progress = False
            for i, cand in enumerate(remaining):
                if cand[0] == ring[-1]:
                    ring.extend(cand[1:])
                elif cand[-1] == ring[-1]:
                    ring.extend(list(reversed(cand))[1:])
                elif cand[-1] == ring[0]:
                    ring[0:0] = cand[:-1]
                elif cand[0] == ring[0]:
                    ring[0:0] = list(reversed(cand))[:-1]
                else:
                    continue
                remaining.pop(i)
                progress = True
                break
        rings.append(ring)
    return rings


def point_in_ring(point, ring):
    """Ray-casting point-in-polygon test. point=(lon,lat)."""
    x, y = point
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_int = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_int:
                inside = not inside
    return inside


def relations_to_polygon_fc(elements):
    features = []
    notes = []
    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        members = el.get("members", [])
        outer_segs = [m["geometry"] for m in members if m.get("role") == "outer" and m.get("geometry")]
        inner_segs = [m["geometry"] for m in members if m.get("role") == "inner" and m.get("geometry")]

        outer_rings = assemble_rings(outer_segs)
        inner_rings = assemble_rings(inner_segs)

        unclosed = [r for r in outer_rings if r[0] != r[-1]]
        if unclosed:
            notes.append(
                f"relation {el['id']} ({tags.get('name')}): {len(unclosed)} outer "
                f"ring(s) did not close (dangling boundary segments) - closed by "
                f"connecting the endpoint back to the start."
            )
            for r in outer_rings:
                if r[0] != r[-1]:
                    r.append(r[0])

        # assign each inner ring (hole) to the outer ring that contains it
        polygons = [[list(map(list, ring))] for ring in outer_rings]
        for inner in inner_rings:
            if inner[0] != inner[-1]:
                inner = inner + [inner[0]]
            placed = False
            for i, ring in enumerate(outer_rings):
                if point_in_ring(inner[0], ring):
                    polygons[i].append(list(map(list, inner)))
                    placed = True
                    break
            if not placed:
                notes.append(
                    f"relation {el['id']} ({tags.get('name')}): an inner ring "
                    f"couldn't be matched to any outer ring - dropped."
                )

        if not polygons:
            notes.append(f"relation {el['id']} ({tags.get('name')}): no outer geometry - skipped.")
            continue

        geometry = (
            {"type": "Polygon", "coordinates": polygons[0]}
            if len(polygons) == 1
            else {"type": "MultiPolygon", "coordinates": polygons}
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {"osm_id": el["id"], "osm_type": "relation", **tags},
            }
        )
    return {"type": "FeatureCollection", "features": features}, notes


CONVERTERS = {
    "nodes": nodes_to_points_fc,
    "centers": centers_to_points_fc,
    "ways": ways_to_polygon_fc,
    "relations": relations_to_polygon_fc,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=CONVERTERS)
    parser.add_argument("input_json", help="raw Overpass [out:json] response body ('out geom;' or 'out center;')")
    parser.add_argument("output_geojson")
    args = parser.parse_args()

    with open(args.input_json) as f:
        elements = json.load(f)["elements"]

    fc, notes = CONVERTERS[args.kind](elements)

    with open(args.output_geojson, "w") as f:
        json.dump(fc, f, ensure_ascii=False)

    print(f"wrote {args.output_geojson}: {len(fc['features'])} features", file=sys.stderr)
    for n in notes:
        print("NOTE:", n, file=sys.stderr)


if __name__ == "__main__":
    main()
