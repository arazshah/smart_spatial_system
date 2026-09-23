#!/usr/bin/env python3
"""Fetch this case study's two Overpass queries into ``data/raw/``.

Pure standard library (``urllib``) so it runs before ``requirements.txt`` is
installed. The two query strings below are the single source of truth and
are reproduced verbatim in ``data/README.md`` - if you change one, change
both, and say why in the commit message.

    python scripts/fetch_overpass.py                 # both layers
    python scripts/fetch_overpass.py --only hospitals
    python scripts/fetch_overpass.py --endpoint https://overpass.kumi.systems/api/interpreter

Writes the raw response bodies to ``data/raw/<layer>.json``. Converting them
to GeoJSON is a separate step, on purpose - the raw response is what you
want to keep when a query returns something surprising:

    python scripts/overpass_to_geojson.py centers   data/raw/hospitals.json          data/raw/hospitals.geojson
    python scripts/overpass_to_geojson.py relations data/raw/mahalle_boundaries.json data/raw/mahalle_boundaries.geojson

If this fails with a proxy 403/407, the session's egress policy blocks
overpass-api.de. Do not route around it - see STUDY_LOG.md, "Network and
secrets", for the three supported ways to get the data in.
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"

# NOTE: "İstanbul" below uses the Turkish dotted capital I (U+0130). Overpass
# matches ["name"=...] as an exact string; ASCII "Istanbul" resolves to no
# area and both queries come back with zero elements. See data/README.md
# caveat 1.
MAHALLE_QUERY_TEMPLATE = """
[out:json][timeout:120];
area["name"="İstanbul"]["admin_level"="4"]->.searchArea;
relation["admin_level"="{admin_level}"](area.searchArea);
out geom;
""".strip()

QUERIES = {
    "hospitals": """
[out:json][timeout:60];
area["name"="İstanbul"]["admin_level"="4"]->.searchArea;
(
  node["amenity"~"hospital|clinic"](area.searchArea);
  way["amenity"~"hospital|clinic"](area.searchArea);
);
out center;
""".strip(),
    # NOTE: admin_level=10 is the level named in this study's brief and is
    # kept as the default so the documented query and the code agree. But
    # OSM-derived Turkish datasets map mahalle at admin_level=**8**, not 10
    # - see data/README.md caveat 6, and run `--probe`, which settles it
    # against the live database instead of against either assumption.
    # Override with --mahalle-admin-level once probed.
    "mahalle_boundaries": MAHALLE_QUERY_TEMPLATE.format(admin_level="10"),
}

PROBE_QUERY = """
[out:json][timeout:120];
area["name"="İstanbul"]["admin_level"="4"]->.searchArea;
(
  relation["admin_level"="6"](area.searchArea);
  relation["admin_level"="8"](area.searchArea);
  relation["admin_level"="9"](area.searchArea);
  relation["admin_level"="10"](area.searchArea);
);
out tags;
""".strip()

RAW_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"


def fetch(endpoint, query, retries=3, backoff=30):
    """POST one query, retrying on the rate-limit/overload codes Overpass uses."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "User-Agent": (
                    "smart-spatial-istanbul-health-access/0.1 "
                    "(+https://github.com/arazshah/smart-spatial-istanbul-health-access)"
                )
            },
        )
        try:
            # Generous: query 2 asks for ~1000 relations with full geometry.
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 504) and attempt < retries:
                wait = backoff * attempt
                print(
                    f"  HTTP {e.code} (Overpass busy), retry {attempt}/{retries - 1} in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt < retries:
                wait = backoff * attempt
                print(f"  {e.reason}, retry {attempt}/{retries - 1} in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise last


def probe(endpoint):
    """Which admin_level actually holds Istanbul's mahalle? Ask the database.

    The study brief specifies admin_level=10; OSM-derived Turkish datasets
    use admin_level=8 for mahalle polygons. Rather than argue, count what is
    there. ``out tags;`` keeps this cheap - no geometry is transferred.
    """
    try:
        text = fetch(endpoint, PROBE_QUERY)
        elements = json.loads(text)["elements"]
    except Exception as e:  # noqa: BLE001
        print(f"probe FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    counts, samples = {}, {}
    for el in elements:
        lvl = el.get("tags", {}).get("admin_level")
        counts[lvl] = counts.get(lvl, 0) + 1
        samples.setdefault(lvl, []).append(el.get("tags", {}).get("name", "?"))

    if not counts:
        print(
            "probe returned zero relations - the AOI itself did not resolve.\n"
            "Check the dotted I (U+0130) survived; see data/README.md caveat 1.",
            file=sys.stderr,
        )
        return 1

    print("admin_level  count  examples")
    for lvl in sorted(counts, key=lambda x: (x is None, x)):
        print(f"{str(lvl):>11}  {counts[lvl]:>5}  {', '.join(samples[lvl][:3])}")
    print(
        "\nThe mahalle level is the one with ~950-1000 relations carrying "
        "neighbourhood-sized\nnames. Pass it as --mahalle-admin-level, and "
        "record this output in\nnotebooks/01_data_and_problem.ipynb.",
        file=sys.stderr,
    )
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--only", choices=sorted(QUERIES), help="fetch just one layer")
    ap.add_argument(
        "--mahalle-admin-level",
        default="8",
        help=(
            "OSM admin_level for mahalle (default 8 - confirmed against the "
            "live database 2026-09-19, see data/README.md caveat 6; the "
            "study brief assumed 10, which returns nothing)"
        ),
    )
    ap.add_argument(
        "--probe",
        action="store_true",
        help="count boundary relations at admin_level 6/8/9/10 in the AOI, then exit",
    )
    ap.add_argument(
        "--print-queries",
        action="store_true",
        help="print the queries and exit (paste into Overpass Turbo)",
    )
    args = ap.parse_args()

    QUERIES["mahalle_boundaries"] = MAHALLE_QUERY_TEMPLATE.format(
        admin_level=args.mahalle_admin_level
    )

    if args.probe:
        return probe(args.endpoint)

    if args.print_queries:
        for name, q in QUERIES.items():
            print(f"### {name} -> data/raw/{name}.json\n{q}\n")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else list(QUERIES)
    failed = False

    for name in names:
        out = RAW_DIR / f"{name}.json"
        print(f"fetching {name} -> {out}", file=sys.stderr)
        try:
            text = fetch(args.endpoint, QUERIES[name])
        except Exception as e:  # noqa: BLE001 - report, don't mask
            print(f"  FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            print(
                "  If this is a proxy 403/407, the session's egress policy blocks\n"
                "  the endpoint. Do not route around it - see STUDY_LOG.md.",
                file=sys.stderr,
            )
            failed = True
            continue

        try:
            n = len(json.loads(text).get("elements", []))
        except json.JSONDecodeError:
            # Overpass reports some errors as an HTML page with HTTP 200.
            print("  FAILED: response was not JSON. First 300 chars:", file=sys.stderr)
            print("  " + text[:300].replace("\n", "\n  "), file=sys.stderr)
            failed = True
            continue

        out.write_text(text, encoding="utf-8")
        print(f"  wrote {out} ({len(text):,} bytes, {n} elements)", file=sys.stderr)
        if n == 0:
            print(
                "  WARNING: zero elements. Most likely the area did not resolve -\n"
                "  check the dotted İ survived. See data/README.md caveat 1.",
                file=sys.stderr,
            )
        # Be a good Overpass citizen between the two heavy queries.
        if name != names[-1]:
            time.sleep(5)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
