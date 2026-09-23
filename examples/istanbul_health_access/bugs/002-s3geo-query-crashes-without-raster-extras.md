# 002 — `s3geo.query()` crashes on a pure-vector query with no rasterio installed

- **Status:** fixed in 0.4.1
- **Found:** 2026-09-19, phase 4 (`notebooks/03_llm_arm.ipynb`, §4, the
  smoke test — the very first `s3geo.query()` call in the notebook)
- **Affects:** smart-spatial-system 0.3.0 (`s3geo/__init__.py::query`,
  `orchestrator/capability_registry.py::CapabilityRegistry.from_plugin_modules`,
  `plugins/ndvi_analysis.py`)
- **Severity:** hard failure (a `ModuleNotFoundError`, not a wrong answer —
  but it blocks *every* `s3geo.query()` call in an environment that hasn't
  separately installed the `raster` extra, regardless of what the query
  actually asks for)

## Symptom

The very first `s3geo.query()` call — Arm 2's smoke test, a 100% vector
query (`hospitals` points, `mahalle` polygons, nearest-facility distance;
no raster layer, no NDVI, nothing raster-related anywhere in the raw
query) — raises `ModuleNotFoundError: No module named 'rasterio'` before
the LLM's plan is ever executed. `rasterio` is not installed in this
repo's `.venv` (it's an optional extra, per `smart_spatial_system`'s own
`pyproject.toml`: `raster = ["rasterio"]`), and nothing about this study's
question needs it.

## Root cause

`s3geo/__init__.py::query()` (0.3.0), the exact new one-call entry point
this study's Arm 2 is built on:

```python
registry = CapabilityRegistry.from_plugin_modules()
```

called with **no arguments**. `CapabilityRegistry.from_plugin_modules`
(`orchestrator/capability_registry.py`) signature:

```python
@classmethod
def from_plugin_modules(
    cls,
    plugin_module_names: list[str] | None = None,
    *,
    tolerant: bool = False,
) -> "CapabilityRegistry":
```

With no args, `plugin_module_names` falls back to
`orchestrator/plugin_modules.py::DEFAULT_SAFE_PLUGIN_MODULES` (38 modules,
*every* registered plugin — raster, postgis, pdf, vector, all of it), and
`tolerant` defaults to `False`. `from_plugin_modules` then
`importlib.import_module()`s every one of those 38 modules **before**
looking at what the LLM's plan actually needs, and with `tolerant=False`
the first import failure aborts the whole call:

```python
for module_name in module_names:
    try:
        module = importlib.import_module(module_name)
        registry.register_plugin_module(module)
    except Exception as exc:
        if not tolerant:
            raise
        registry.skipped_plugins.append(...)
```

`plugins/ndvi_analysis.py` is in `DEFAULT_SAFE_PLUGIN_MODULES` and does an
unconditional top-level `import rasterio` (line 5). `rasterio` is gated
behind `smart_spatial_system`'s own `raster` extra
(`pyproject.toml`: `raster = ["rasterio"]`) — it is **not** a base
dependency — so any environment that installed the package without that
extra hits `ModuleNotFoundError` here, on the very first
`s3geo.query()` call, no matter what the query says.

**The framework already has the fix, just not here** (same shape as
`bugs/001`). `orchestrator/service.py`'s `OrchestratorService.__init__`
builds its registry the same way, deliberately with `tolerant=True`:

```python
self.registry = CapabilityRegistry.from_plugin_modules(
    self.config.plugin_modules,
    tolerant=True,
)
```

`pyproject.toml` even documents *why*, right above the extras it gates,
confirming this was a conscious design decision the new `s3geo.query()`
entry point simply didn't follow:

> postgis_connector, ndvi_analysis (rasterio) and pdf_renderer
> (weasyprint) are all in orchestrator/plugin_modules.py's
> DEFAULT_SAFE_PLUGIN_MODULES, so omitting these extras does NOT crash
> the app at startup: OrchestratorService builds its registry with
> tolerant=True (orchestrator/service.py), so a plugin whose import fails
> for a missing optional dependency is recorded in
> registry.skipped_plugins and simply unavailable, not a hard failure -
> confirmed by reading that call site, not assumed.

`s3geo.query()` also gives the caller no way to opt into `tolerant=True`
itself — it isn't a parameter of `query()` at all — so there is no
workaround available through the documented one-call API; the only way
around it without installing `rasterio` is to bypass `s3geo.query()` and
wire the five underlying classes by hand (the "advanced use" path the
module's own docstring says stays available).

One more inconsistency worth naming: `ndvi_analysis.py` is the *only*
raster-related plugin among the ten in `DEFAULT_SAFE_PLUGIN_MODULES`
(`spectral_indices`, `raster_threshold`, `raster_to_vector`,
`raster_statistics`, `raster_reclassify`, `band_math`,
`raster_clip_mask`, `slope_aspect`, `zonal_statistics`,
`ndvi_calculator`) that imports `rasterio` unconditionally at module
top level — none of the other nine reference `rasterio` by name at all
in their own source, so they're importable without it. `ndvi_analysis.py`
alone breaks the pattern its siblings already follow.

## Reproduction

Minimal, from a clean 0.3.0 install **without** the `raster` extra
(`pip install smart_spatial_system`, not `pip install
"smart_spatial_system[raster]"`):

```python
import geopandas as gpd
import s3geo

# any two ordinary vector layers -- content is irrelevant, this fails
# before the query is even planned
points = gpd.read_file("some_points.geojson")
polygons = gpd.read_file("some_polygons.geojson")

s3geo.query(
    "For each polygon, find the nearest point and its distance.",
    layers={"points": points, "polygons": polygons},
)
# ModuleNotFoundError: No module named 'rasterio'
#   ... plugins/ndvi_analysis.py:5, in <module>
#         import rasterio
```

This repo's real traceback (from `data/raw/hospitals.geojson` +
`data/raw/mahalle_boundaries.geojson`, both committed/regeneratable per
`data/README.md`) matches this exactly — the failure is in
`CapabilityRegistry.from_plugin_modules()` → `importlib.import_module("plugins.ndvi_analysis")`
→ `import rasterio`, with the query's own content never inspected.

## Proposed fix

### Plugin layer (deterministic)

`s3geo.query()` should build its registry the same way
`OrchestratorService` already does:

```python
registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
```

One line, zero behavior risk — `tolerant=True` is already this
framework's own answer to exactly this scenario at the other call site.
Optionally, expose it as a `query(..., tolerant: bool = True)` kwarg so a
caller who *wants* strict "fail if any plugin is unavailable" behavior can
still opt back in, but the unconditional default here should not be
`False` when the only other call site in the codebase uses `True` for the
same registry, built from the same module list.

As defense in depth, `plugins/ndvi_analysis.py`'s top-level
`import rasterio` (line 5) should move to a lazy import inside
`process_ndvi()`, matching whatever pattern its sibling raster plugins
already use to stay importable without `rasterio` present — this would
mean `ndvi_analysis` only breaks queries that actually try to use NDVI,
not every query on every `s3geo.query()` call, independent of the
`tolerant=True` fix above.

### LLM-prompt layer (generative)

Not applicable, same reasoning as `bugs/001`: this fires inside
`CapabilityRegistry.from_plugin_modules()`, before the LLM's plan is
executed and regardless of what operations it contains — a purely vector
plan trips it exactly as hard as a plan that actually wants NDVI. No
prompt guidance can route around a registry-build failure that happens
before the plan runs.

## Local mitigation in this repo, if any

Applied here: installed the missing optional dependency into this
repo's own `.venv` rather than touching pinned source —
`pip install rasterio` (equivalently `pip install
"smart_spatial_system[raster]"` per its own extras group). This sidesteps
the bug entirely: `from_plugin_modules()`'s import loop succeeds because
`plugins/ndvi_analysis.py` now imports cleanly, even though
`tolerant` is still `False` and nothing about this repo's query touches
NDVI. No notebook or framework code changed. `postgis_connector.py` and
`pdf_renderer.py` (the other two plugins the `pyproject.toml` comment
names) import their own optional deps (`psycopg`, `weasyprint`) lazily,
not at module top level, so they don't need the same treatment — checked
directly, not assumed.

Named and commented here only; removes automatically once the pin moves
past a version where `s3geo.query()` builds its registry with
`tolerant=True`.

## Resolution

**Fixed upstream in `smart_spatial_system` 0.4.1** (2026-09-19,
`CHANGELOG.md` `[0.4.1]` "Fixed") — confirmed by cloning
`github.com/arazshah/smart_spatial_system` at tag `v0.4.1` and reading
the actual diff, not taken on the fixer's word alone:

- `s3geo/__init__.py::query()` now builds its registry with
  `tolerant=True` by default (`CapabilityRegistry.from_plugin_modules(tolerant=tolerant)`
  where `tolerant: bool = True` is now a real parameter of `query()`
  itself), exactly the fix proposed above, plus a way for a caller to
  opt back into strict mode that wasn't requested but is a reasonable
  addition. Also added in the same release: `s3geo.registry(tolerant=True)`,
  a standalone accessor to the same registry (from `0.4.0`, purely
  additive, released the same day — nothing existing changed between
  0.3.0 and 0.4.1 that would affect this repo).
- `plugins/ndvi_analysis.py`'s `import rasterio` moved from module top
  level into `process_ndvi()` itself, exactly the defense-in-depth fix
  proposed above.

This repo's pin moved `smart-spatial-system==0.3.0` → `==0.4.1` in
`requirements.txt` (commit that bumps it: see git log same day). The
local mitigation (`pip install rasterio`) is no longer required once
this pin is actually installed — the registry no longer needs to import
`ndvi_analysis` successfully at all when a query doesn't use it, and even
when it does, `rasterio` is now imported lazily.
