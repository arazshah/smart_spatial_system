# Data

Nothing under `data/raw/` is committed (see `.gitignore`). This file is the
record of where the data comes from, the exact queries that produce it, and
how to regenerate it. `data/processed/` — the small derived GeoJSON actually
fed into `smart_spatial_system` — *is* committed once the data-prep notebook
produces it.

## Area of interest

**İstanbul Province (il), Turkey** — the whole administrative province on
both the European and Asian sides, not the smaller historic-peninsula or
metropolitan-core definitions.

| | |
|---|---|
| AOI | İstanbul Province, `admin_level=4` in OSM |
| OSM name tag | `İstanbul` (Turkish dotted capital **İ**, U+0130 — not ASCII `I`) |
| Approx. bbox (WGS 84) | 27.95 – 29.96 E, 40.80 – 41.68 N |
| Units of analysis | *mahalle* (neighbourhood), `admin_level=8` — **confirmed against the live database 2026-09-19, see caveat 6** (964 relations fetched) |
| Source CRS | EPSG:4326 |
| **Analysis CRS** | **EPSG:32635** — WGS 84 / UTM zone 35N |

EPSG:32635 is the metric CRS used for every distance, threshold and area
computation. Zone 35N (24°–30° E) covers all of İstanbul Province; the
province's eastern edge (~29.96° E) stays inside it, so no zone split is
needed. Distances are in metres, which is what the 2000 m threshold assumes.
Anything reported in metres that was computed in EPSG:4326 is wrong by
construction — see `STUDY_LOG.md`'s CRS note.

## Fetched

Two separate real fetches on 2026-09-19, from two different environments —
kept both because OSM is a live, continuously-edited database and the
difference between them is itself informative, not noise:

- **First fetch**, from a network-restricted cloud sandbox, via the
  `overpass.kumi.systems` mirror (the primary `overpass-api.de` endpoint was
  unreachable there — connection reset at the TLS layer; the mirror worked
  but is a shared public instance and returned `HTTP 504`/timeouts under
  load several times before succeeding): **972** hospitals/clinics (406
  `amenity=hospital`, 566 `amenity=clinic`), **964** mahalle (955 `Polygon`,
  9 `MultiPolygon`), 0 skipped by the converter. **A `200 OK` with a
  suspiciously small `elements` array is not proof of a real empty
  result** — one attempt this fetch returned valid-looking JSON with zero
  elements for a query that returns 100+ when the server isn't congested
  (confirmed by re-running with `out count` once less busy: 135 in a bbox
  probe, 126 with the exact area filter). Overpass can return a well-formed
  but truncated/empty response under load rather than a clear error.
  `scripts/fetch_overpass.py` retries on `429`/`504`, but an unexpectedly
  low count on a `200` should still be treated as suspect, not trusted.
- **Second fetch**, re-run directly against `overpass-api.de` from the
  paper author's own machine (normal internet, no proxy involved): **1020**
  hospitals/clinics (421 `amenity=hospital`, 599 `amenity=clinic`), **964**
  mahalle (955 `Polygon`, 9 `MultiPolygon` — identical breakdown to the
  first fetch). **This is the fetch `data/raw/` and `data/processed/`
  currently hold**, and the one `notebooks/01_data_and_problem.ipynb`'s
  executed output reflects.

The **mahalle count is identical between the two fetches** (administrative
boundaries change rarely) while the **hospital/clinic count grew by 48**
(individual facility points get added/edited far more often) — consistent
with genuine OSM activity between the two fetch times, not a bug in either
run. Report `data/raw/`'s actual current count in the paper rather than
either number above; re-run `notebooks/01_data_and_problem.ipynb`'s summary
cell and copy its live output if regenerating this file.

## Sources

Both layers come from OpenStreetMap via the
[Overpass API](https://overpass-api.de/api/interpreter).

| Layer | File | OSM selection |
|---|---|---|
| Hospitals and clinics | `data/raw/hospitals.geojson` | `amenity=hospital` or `amenity=clinic`, nodes and ways, within the AOI |
| Mahalle boundaries | `data/raw/mahalle_boundaries.geojson` | `admin_level=8` relations within the AOI — see caveat 6 |

## The exact queries

Run against `https://overpass-api.de/api/interpreter` (POST the query as the
`data` parameter, or paste it into [Overpass Turbo](https://overpass-turbo.eu/)
and Run ▶). `scripts/fetch_overpass.py` sends exactly these two strings;
`scripts/download_istanbul_data.md` is the manual/browser fallback.

### 1. Hospitals and clinics → `data/raw/hospitals.geojson`

```
[out:json][timeout:60];
area["name"="İstanbul"]["admin_level"="4"]->.searchArea;
(
  node["amenity"~"hospital|clinic"](area.searchArea);
  way["amenity"~"hospital|clinic"](area.searchArea);
);
out center;
```

`out center` returns each **way** as a single representative point in a
`center` field (not a full `geometry`), and each **node** as its own
`lat`/`lon`. Both become `Point` features — which is what
`nearest_neighbor` wants. Convert with:

```
python scripts/overpass_to_geojson.py centers data/raw/hospitals.json data/raw/hospitals.geojson
```

### 2. Mahalle boundaries → `data/raw/mahalle_boundaries.geojson`

```
[out:json][timeout:120];
area["name"="İstanbul"]["admin_level"="4"]->.searchArea;
relation["admin_level"="8"](area.searchArea);
out geom;
```

`out geom` returns every member way's full coordinate list, so the relations
can be assembled into `Polygon`/`MultiPolygon` without a second lookup.
Convert with:

```
python scripts/overpass_to_geojson.py relations data/raw/mahalle_boundaries.json data/raw/mahalle_boundaries.geojson
```

## Known caveats — check these against the real response, don't assume

These are the things most likely to make the queries above return something
other than what this file claims. Record what actually came back in
`notebooks/01_data_and_problem.ipynb` rather than trusting the numbers here.

1. **`area["name"="İstanbul"]` is an exact string match on the dotted `İ`.**
   If a shell, editor or copy-paste step normalises it to ASCII `I` or
   lowercase `i`, the area resolves to nothing and both queries return zero
   elements. A zero-element response is this bug until proven otherwise.
   `["name:tr"="İstanbul"]` or the province's OSM relation id
   (`area(3600223474)`-style, id to be confirmed at fetch time, not assumed
   here) are the fallbacks.
2. **`admin_level=4` without `boundary=administrative`** can in principle
   match a non-boundary object carrying the same tags. Add
   `["boundary"="administrative"]` if the area looks wrong.
3. **Hospital *relations* are not fetched.** The query selects `node` and
   `way` only, per the study design. A large hospital campus mapped as a
   multipolygon relation is therefore missing. Count how many
   `type=multipolygon` + `amenity=hospital` relations exist in the AOI
   before deciding this is negligible, and name it in the paper's
   limitations either way.
4. **`amenity~"hospital|clinic"` is an unanchored regex**, so it also matches
   any future/rare value *containing* those substrings. Report the actual
   distinct `amenity` values found; if it is exactly
   `{hospital, clinic}`, say so.
5. **Hospitals vs. clinics are very different facilities.** Keep the
   `amenity` value as a property through the whole pipeline so the analysis
   can be re-run hospital-only. "Nearest hospital *or* clinic" is the
   headline definition, and that choice has to be visible.
6. **✅ Resolved 2026-09-19, against the live database:
   mahalle is `admin_level=8`, not the brief's 10.** Probed with
   `python scripts/fetch_overpass.py --probe` (counts boundary relations at
   levels 6/8/9/10 in the AOI via `out tags`, no geometry):

   ```
   admin_level  count  examples
             6     39  Adalar, Şile, Silivri
             8    964  Kadıköy Mahallesi, Çelebi Mahallesi, Paşaköy Mahallesi
   ```

   Level 6 (39 relations) is *ilçe* — matches İstanbul's well-known 39
   districts exactly. Level 8 (964) is mahalle. Levels 9 and 10 returned
   nothing. This confirms the two OSM-derived Turkish administrative
   datasets that disagreed with the brief before this was checked
   (`osadikoglu/turkey-admin-units-osm`: il=4, ilçe=6, mahalle=8; Geolocet's
   Turkey neighbourhoods product: also 8) rather than the brief's assumed
   10. The query above and `scripts/fetch_overpass.py`'s default were
   corrected to 8 in the same commit as this update.

   **The full fetch (2026-09-19) came back clean:** 964 mahalle relations,
   955 `Polygon` + 9 `MultiPolygon`, **zero skipped by the converter** — OSM
   mahalle coverage for İstanbul is complete enough that the "unit of
   analysis needs revisiting" fallback below did not end up triggering.
   Kept for the record: if a future re-fetch shows skipped relations or a
   count that drifts far from 964, treat that as a live problem, not this
   resolved one, and revisit the unit of analysis in `paper/PLAN.md`'s open
   decisions before treating a partial set of neighbourhoods as a map.
7. **Overpass rate-limits and times out.** The 120 s timeout on query 2 is
   not generous for ~1000 relations with full geometry; a 429 or a partial
   response is normal and should be retried, not worked around with a
   smaller AOI.

## Regenerating

1. `python scripts/fetch_overpass.py` (needs a route to `overpass-api.de`)
   writes the two raw JSON responses into `data/raw/`; or fetch them any
   other way and drop them there under the same names.
2. `python scripts/overpass_to_geojson.py ...` for each, as above.
3. Run `notebooks/01_data_and_problem.ipynb` end to end. It reads
   `data/raw/`, does the minimal cleaning (drop null/invalid geometry, keep
   only the needed fields, reproject to EPSG:32635, assert the CRS), records
   the real feature counts, and writes `data/processed/`.

## License

OpenStreetMap data is © OpenStreetMap contributors, available under the
[Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
Anything derived from it and redistributed here (`data/processed/`, the
figures, the underserved-mahalle table) carries the same attribution
requirement — retained in each processed file's metadata and repeated in the
paper's Data Availability section. The repository's own code is MIT
(`LICENSE`); the data is not.
