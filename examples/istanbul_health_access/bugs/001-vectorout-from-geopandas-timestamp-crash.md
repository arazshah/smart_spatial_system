# 001 — `VectorOut.from_geopandas()` crashes on real OSM data with a date tag

- **Status:** fixed in geochat-sdk 1.0.1
- **Found:** 2026-09-19, phase 3 (`notebooks/02_rule_based_arm.ipynb`, §2,
  first cell — the very first plugin call in the notebook)
- **Affects:** smart-spatial-system 0.3.0 → geochat-sdk 1.0.0
  (`geochat-sdk @ git+https://github.com/arazshah/geochat-platform.git#subdirectory=geochat-sdk`,
  pinned `>=1.0.0` in `smart_spatial_system`'s own `pyproject.toml`)
- **Severity:** hard failure (not silent — a `TypeError`, not a wrong
  number — but it stops phase 3 cold on ordinary, unmodified OSM data)

## Symptom

`VectorOut.from_geopandas(gdf)` — the SDK's own documented conversion from a
GeoDataFrame to the input shape every plugin function expects (confirmed by
reading `plugins/nearest_neighbor.py` et al.: they accept `VectorOut` /
FeatureCollection dict / Feature dict / list, never a GeoDataFrame directly)
— raises `TypeError: Object of type Timestamp is not JSON serializable` the
first time it is called on `data/raw/hospitals.geojson`, loaded with plain
`geopandas.read_file()` and never otherwise modified.

This is not a corrupted or malformed file. It is 1020 real hospital/clinic
points fetched straight from Overpass (`data/README.md`), and it fails
before any of this repo's own analysis code runs — the very first plugin
call in the rule-based arm.

## Root cause

`geochat_sdk/types/vector.py::VectorOut.from_geopandas` (v1.0.0):

```python
@classmethod
def from_geopandas(cls, gdf: Any) -> VectorOut:
    """Create VectorOut from a GeoDataFrame."""
    geojson_str = gdf.to_json()
    geojson = json.loads(geojson_str)
    return cls(features=geojson.get("features", []))
```

`GeoDataFrame.to_json()` (geopandas) builds a plain Python dict of the
GeoJSON structure and passes it straight to the stdlib `json.dumps(geo,
**kwargs)` — it does **not** special-case non-JSON-native column dtypes.
`from_geopandas` calls it with no `default=` handler and exposes no way for
a caller to supply one (it hardcodes `gdf.to_json()`, no `**kwargs`
passthrough), so there is no way to work around this from outside the SDK.

The trigger is entirely ordinary for OSM data: several hospital features
carry a `check_date`, `start_date`, or `check_date:opening_hours` tag,
e.g.:

```
check_date: 12 of 972 features, values like "2024-11-07", "2026-04-30"
start_date: 3 of 972 features, values like "2019-04-13", "1998-12-10"
```

`geopandas.read_file()` (via pyogrio) infers a column as `datetime64[ns]`
when every non-null value in it parses as a date — which a sparse
(12-of-1020), all-date-shaped `check_date` column does. The resulting
GeoDataFrame column holds `pandas.Timestamp` objects (and `NaT` for the
~99% of rows missing the tag). `to_file(..., driver="GeoJSON")` (used
successfully in `01_data_and_problem.ipynb`) goes through OGR/pyogrio's own
writer, which serializes datetime fields correctly — a completely different
code path from `to_json()`'s hand-built-dict-then-`json.dumps()`, which is
why phase 2 never hit this and phase 3 did on its very first plugin call.

This is not a rare edge case: `check_date`, `survey:date`, and `start_date`
are common, encouraged OSM tags across many `amenity` types, so any
real-world OSM-derived GeoDataFrame fed through `VectorOut.from_geopandas`
is at risk, not just this dataset.

**The framework already has the fix, just not here.** `s3geo/__init__.py`'s
own `_to_geojson_dict()` — the equivalent GeoDataFrame→GeoJSON conversion
used by `s3geo.query()`'s `layers=` parameter — calls
`layer.to_json(default=str)`, i.e. it *does* pass a `default=` handler
through to `json.dumps`. Read directly from 0.3.0 source, not yet
empirically confirmed by a real run: `notebooks/03_llm_arm.ipynb`'s Arm 2
loads the exact same `data/raw/hospitals.geojson` (same `check_date`/
`start_date` columns) through this path and should *not* crash the way
Arm 1 did, on this reading of the source. If it does crash there too when
that notebook actually runs, that would mean this analysis is wrong and
needs revisiting. Either way, two conversion helpers in the same codebase
handling the identical situation differently — one with a `default=`
handler, one without — is worth citing as evidence the missing handler is
a one-line omission, not a harder problem.

## Reproduction

Minimal, from a clean 0.3.0 install:

```python
import geopandas as gpd
from geochat_sdk.types.vector import VectorOut

gdf = gpd.read_file("data/raw/hospitals.geojson")  # this repo's committed fetch
VectorOut.from_geopandas(gdf)  # TypeError: Object of type Timestamp is not JSON serializable
```

`data/raw/hospitals.geojson` is gitignored (regenerated, per
`data/README.md`), but any OSM `amenity` extract with a `check_date` tag on
even one feature reproduces it — this is a property of `check_date`'s
value shape, not of this specific fetch.

## Proposed fix

### Plugin layer (deterministic)

`VectorOut.from_geopandas` should sanitize non-JSON-native column dtypes
before serializing — at minimum, cast `datetime64`/`Timestamp` columns to
ISO-8601 strings (`gdf[col].dt.strftime("%Y-%m-%dT%H:%M:%S")` with `NaT` →
`None`), the same way OGR's GeoJSON writer already does for `to_file()`. A
cheap alternative that would also have prevented this: pass a
`default=str` (or a dedicated JSON encoder) into `json.dumps` — but that
needs `from_geopandas` to accept and forward `**kwargs` to `gdf.to_json()`,
which it currently does not.

### LLM-prompt layer (generative)

Not applicable in the usual sense — this is a plugin/SDK-layer conversion
helper, not something an LLM planner chooses to call incorrectly;
`s3geo.query()`'s `layers` parameter accepts GeoDataFrames directly
(`paper/PLAN.md` "Arm 2"), so Arm 2 would hit this exact crash too, through
the same `from_geopandas` path, with no planning decision able to avoid it.
If anything, `system_hints`/planner guidance should tell the model that a
layer-loading failure here is an upstream data-shape issue, not a query
formulation problem, so it doesn't waste retries rephrasing the query.

## Local mitigation in this repo, if any

Applied in `notebooks/02_rule_based_arm.ipynb`'s `gdf_to_vectorout()`
helper (§ imports/helpers cell): before calling `VectorOut.from_geopandas`,
any `datetime64`-dtype column on the input GeoDataFrame is cast to a plain
string column (ISO date, `NaT` → `None`) on a copy — never on the
GeoDataFrame the rest of the notebook works with. Named and commented in
place; removes automatically once the pin moves past a version that fixes
`from_geopandas` itself.

## Resolution

**Fixed upstream in `geochat-sdk` 1.0.1** (2026-09-19) — confirmed by
cloning `github.com/arazshah/geochat-platform` (subdirectory
`geochat-sdk`) at tag `v1.0.1` and reading `geochat_sdk/types/vector.py`
directly, not taken on the fixer's word alone:

```python
def from_geopandas(cls, gdf: Any, **to_json_kwargs: Any) -> VectorOut:
    ...
    to_json_kwargs.setdefault("default", str)
    geojson_str = gdf.to_json(**to_json_kwargs)
    geojson = json.loads(geojson_str)
    return cls(features=geojson.get("features", []))
```

Goes slightly beyond the fix proposed above: defaults `default=str` (so
`datetime64`/`Timestamp` columns serialize instead of crashing, matching
this organization's own `s3geo._to_geojson_dict()` precedent this report
cited) *and* forwards arbitrary `**to_json_kwargs`, so a caller can
override `default` or any other `GeoDataFrame.to_json()` keyword (e.g.
`drop_id`) if they need to.

This repo's pin moved `smart-spatial-system==0.3.0`'s implicit
`geochat-sdk>=1.0.0` to an explicit `geochat-sdk==1.0.1` in
`requirements.txt` (same commit as the `bugs/002` pin bump). The local
mitigation in `notebooks/02_rule_based_arm.ipynb`'s `gdf_to_vectorout()`
(casting `datetime64` columns to strings before calling
`VectorOut.from_geopandas()`) is no longer required once this pin is
installed — safe to leave in place as a no-op, or remove next time that
notebook is touched.
