# Downloading the İstanbul source data

Three ways to populate `data/raw/`, in order of preference. They all end at
the same converter, so nothing downstream cares which was used. The exact
queries, the AOI and the caveats live in
[`../data/README.md`](../data/README.md); `scripts/fetch_overpass.py` holds
them as code and is the single source of truth.

## 1. Scripted (needs a route to `overpass-api.de`)

```bash
python scripts/fetch_overpass.py
python scripts/overpass_to_geojson.py centers   data/raw/hospitals.json          data/raw/hospitals.geojson
python scripts/overpass_to_geojson.py relations data/raw/mahalle_boundaries.json data/raw/mahalle_boundaries.geojson
```

Check first, and believe the answer:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://overpass-api.de/api/status
```

A proxy `403`/`407` means the session's egress policy blocks Overpass. Do
not route around it — use option 2 or 3.

## 2. Overpass Turbo in a real browser

```bash
python scripts/fetch_overpass.py --print-queries
```

Paste each into [overpass-turbo.eu](https://overpass-turbo.eu/), Run ▶, then
**Export ▸ raw data / JSON** (not "GeoJSON" — keep the raw Overpass response;
the converter expects it, and a surprising response is much easier to
diagnose raw). Save as `data/raw/hospitals.json` and
`data/raw/mahalle_boundaries.json`, then run the two converter commands
above.

This is also the route that works from a sandboxed session with a desktop
browser attached: executing the query with `fetch()` inside the user's real
browser uses their network, not the sandbox's. It is how the Vienna repo's
data was obtained.

## 3. Hand the raw JSON to the session

Attach or paste the two response bodies, write them to `data/raw/` under the
same names, and convert. Fine for a one-off; note in the commit which route
was used and on what date, since Overpass is a live database.

## After any route

- `data/raw/` is gitignored — don't commit it. `data/processed/` is
  committed and is produced by `notebooks/01_data_and_problem.ipynb`.
- Record the real feature counts and the fetch date in that notebook. Every
  count in `data/README.md` is an expectation, not a measurement.
- Zero elements almost always means the `İstanbul` area didn't resolve
  because the dotted **İ** (U+0130) was mangled somewhere. See
  `data/README.md` caveat 1 before blaming the query.
