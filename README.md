# Smart Spatial System

[![PyPI](https://img.shields.io/pypi/v/smart-spatial-system)](https://pypi.org/project/smart-spatial-system/)
[![CI](https://github.com/arazshah/smart_spatial_system/actions/workflows/ci.yml/badge.svg)](https://github.com/arazshah/smart_spatial_system/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

**Ask a geospatial question in plain language and get map layers, tables, reports and files back.**

**[s3geo.com](https://s3geo.com)** — project site, plugin catalog and case-study index.

**Used in research:** four case studies have been built on s3geo so far (Vienna, Tehran, İstanbul, Urmia). Each compares an LLM-planned analysis against one written by hand on real OpenStreetMap data. The İstanbul study is included in full in [`examples/istanbul_health_access/`](examples/istanbul_health_access/README.md). See [Case studies and papers](#case-studies-and-papers-written-with-s3geo).

Smart Spatial System is a plugin-based GeoAI backend with a React workbench. A question such as *"rank these candidate properties by distance to metro stations, malls and main roads"* is turned into a structured `QuerySpec`, planned as a DAG of spatial operations, executed by plugins against uploaded files or PostGIS, and returned as map-ready outputs with a full execution trace.

![Running the Vienna accessibility ranking end to end through s3geo: the planned DAG, then the ranked output](docs/assets/demo.gif)

Real, unedited terminal output from [`examples/s3geo_accessibility_demo.py`](examples/s3geo_accessibility_demo.py) — the same deterministic ranking as `accessibility_analysis.py`, called entirely through `import s3geo` (no server, no LLM key needed for this rule-based path). Run it yourself after installing below.

It is the application built on top of [geochat-platform](https://github.com/arazshah/geochat-platform): plugins are written with `geochat_sdk` and executed through `geochat_kernel`.

> **Status:** published and usable, still refactoring internally. Logic is moving out of `orchestrator/` into the layered `smart_spatial_system/` package (see [docs/ARCHITECTURE_TARGET.md](docs/ARCHITECTURE_TARGET.md)); the `orchestrator/*_service.py` modules are compatibility shims during that move. The public surface - the CLI, the HTTP API and the documented entry points below - is stable.

## Why this, not a general-purpose LLM agent with a GIS tool belt

The natural alternative is: give an LLM function-calling access to some GIS
functions and let it decide what to call. This project deliberately doesn't
stop there, for reasons that turned out to matter in practice, not just in
theory - see [`smart-spatial-vienna-accessibility`](https://github.com/arazshah/smart-spatial-vienna-accessibility)'s
case study and [`CHANGELOG.md`](CHANGELOG.md)'s `0.2.1`-`0.2.4` entries for
five real bugs this surfaced and fixed:

- **A deterministic path exists alongside the LLM path**, built from the same
  operation catalog (`OP_CATALOG`). The same analysis can be run by rule and
  by LLM, so "is the LLM's plan reliable" is a measurable question (Plan
  Agreement Rate, Rank Stability - see [docs/CASE_STUDIES.md](docs/CASE_STUDIES.md)), not
  a leap of faith.
- **Every LLM-generated plan is validated before execution**, not just before
  syntax errors: missing input roles, an unchained multi-factor scoring step,
  an asymmetric CRS reprojection, an untyped scoring factor - each is a
  distinct, previously-silent failure mode this system now catches and raises
  a specific error for, before spending a single second executing.
- **Every plugin carries a full execution trace** - which operations ran, in
  what order, with what parameters - so a wrong answer is debuggable, not a
  black box.

---

## How a query runs

```text
natural-language question
  → QuerySpec            LLM (OpenAI-compatible) or rule-based, with PostGIS semantic context
  → DeterministicPlanner + OP_CATALOG
  → DagPlan → DagExecutor
  → CapabilityRegistry   weighted router, learns from user feedback
  → geochat_sdk plugins  vector, raster, PostGIS, reporting, export
  → outputs              map layers · tables · documents (PDF/HTML) · files · trace
```

Design decisions are recorded as ADRs in [`docs/`](docs): single kernel pipeline, artifact-based responses, a multilingual semantic layer (English and Persian questions, language-neutral concepts inside), and a service-oriented modular backend.

## What is in the box

- **37 plugins registered by default** (`orchestrator/plugin_modules.py::DEFAULT_SAFE_PLUGIN_MODULES`): buffer, ring buffer, spatial join, intersection, predicates, dissolve, nearest neighbour, distance, area and perimeter, centroids, CRS transform, geometry validation, attribute statistics, zonal statistics, band math, NDVI and spectral indices, slope/aspect, raster clip/reclassify/threshold/statistics, raster-to-vector, WMS/WFS fetcher, PostGIS connector, feature scoring/enrichment/risk enrichment, real-estate scoring and spatial enrichment, query filter, vector loader, report builder, PDF renderer and data export. Raster uploads load through `local_raster_loader`, and `geocoding_resolver` ships but is not registered by default.
- **Data sources:** raster and vector uploads, CSV tables, WMS, WFS, PostGIS and remote URLs, grouped into projects.
- **Workflows:** multi-amenity accessibility scoring (rule-based, reproducible), real-estate site ranking with a generated PDF report, and NDVI analysis.
- **Learning router:** capability weights adjust from user feedback, with reviewable weight proposals.
- **Workbench:** React + Leaflet UI for queries, step-by-step progress, map layers, inspection, plugin settings and outputs.

## Repository layout

```text
api/                     FastAPI app and routers
orchestrator/            query parsing, planning (QuerySpec, OP_CATALOG, DAG), routing, services
smart_spatial_system/    new layered package (application services; other layers being filled in)
s3geo/                   public entry point: s3geo.query() one-call path, plus the planning
                         pipeline classes it wires up, re-exported for finer-grained control
plugins/                 geochat_sdk capability plugins
config/plugins/          per-plugin YAML config (*.example.yaml are the templates)
templates/reports/       report templates (real-estate report)
scripts/sql/             PostGIS views for the Tehran OSM demo
examples/                runnable examples and their sample data, plus a complete case study
                         (examples/istanbul_health_access/)
frontend/                React + Vite workbench
tests/                   pytest suite (~150 modules)
docs/                    architecture, ADRs, API contracts, phase reports
```

Runtime data (outputs, uploads, projects) is written to `var/` by default, or to `SMART_SPATIAL_RUNTIME_DIR`, and is not committed.

## Install

Requires Python 3.11+.

```bash
pip install "smart-spatial-system[raster,pdf]"
```

Extras are optional and independent: `raster` (`rasterio` - NDVI, spectral
indices, zonal statistics), `pdf` (`weasyprint` - PDF reports), `postgis`
(`psycopg`), `dev` (`pytest`, `ruff`). Leaving one out does not break the
install: the affected plugins are simply not registered, and the rest of
the system runs normally.

Run the API:

```bash
smart-spatial-api serve --port 8000     # http://127.0.0.1:8000/docs
```

> **Set `SMART_SPATIAL_API_KEY` before exposing this beyond localhost.**
> With it unset every endpoint except `/` and `/health` is open, and the
> server logs a warning saying so at startup. See
> [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Use it

Two ways in, both first-class: import the library, or call the HTTP API.
Complete runnable versions of all of these are in [`examples/`](examples).

### As a library

Score candidate sites by how close they are to the things that matter, with
no server and no LLM involved:

```python
from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.planner import DeterministicPlanner
from smart_spatial_system.application.services.query_execution.accessibility_query_spec import (
    AmenitySpec, build_accessibility_initial_inputs, build_accessibility_query_spec,
)

amenities = [
    AmenitySpec(ref="metro", distance_field="distance_to_metro_m",
                max_distance_m=800.0, weight=3.0),
    AmenitySpec(ref="schools", distance_field="distance_to_school_m",
                max_distance_m=1200.0, weight=2.0),
]

query_spec = build_accessibility_query_spec(
    "Rank sites by access to metro and schools", amenities,
    target_crs="EPSG:31256",          # a projected CRS - see the note below
)

plan = DeterministicPlanner().build(query_spec)
registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
result = DagExecutor(lambda name: registry.resolve(name).callable).execute(
    plan,
    initial_inputs=build_accessibility_initial_inputs(
        sites=sites_geojson,
        amenity_layers={"metro": metro_geojson, "schools": schools_geojson},
    ),
)
```

`python examples/accessibility_analysis.py` runs exactly this over five
candidate sites in Vienna and prints the plan and the ranking:

```text
Plan: 10 operations
  sites_metric      transform_vector_crs
  metro_metric      transform_vector_crs
  sites_with_metro  find_nearest_neighbors
  ...

Site accessibility ranking
  Rank  Name                  Accessibility score  Metro (m)  School (m)  Park (m)
  1     Site 3 - Praterstern  75.4                 23.0       407.0       711.0
  2     Site 4 - Ottakring    60.8                 56.0       684.0       4371.0
  3     Site 2 - Karlsplatz   59.8                 128.0      782.0       630.0
```

This path is **rule-based, not LLM-backed**: the operation chain follows
mechanically from the amenity list, so identical inputs always produce an
identical plan and identical numbers.

> **Distances need a projected CRS.** The spatial operations measure planar
> distance in whatever units the input CRS uses and never reproject on your
> behalf, so EPSG:4326 input yields *degrees*. The generator reprojects
> every layer first; pass a local projected CRS for your study area
> (`EPSG:31256` for Vienna, the relevant UTM zone elsewhere). EPSG:3857 is a
> safe global fallback but its metres are inflated by 1/cos(latitude) -
> about 1.5x at Vienna's latitude.

### As a library, LLM-planned in one call

The `accessibility_analysis.py` path above is rule-based and wires the
planning pipeline manually - the right level of control for a reusable
workflow, but more ceremony than a one-off question needs. `s3geo.query()`
collapses that same pipeline (LLM client, `LLMQuerySpecGenerator`,
`DeterministicPlanner`, `CapabilityRegistry`, `DagExecutor`) into a single
call that plans the operation chain from the question itself:

```python
import s3geo

result = s3geo.query(
    "For every station, find amenity points within 300 meters, "
    "reproject to a metric CRS first.",
    layers={"stations": stations_geojson, "amenity": amenity_geojson},
)

result.goal          # str - the LLM-identified analysis goal
result.operations    # list[str] - operation names, in order
result.output        # the final DAG output
```

`layers` accepts GeoJSON `FeatureCollection` dicts or
`geopandas.GeoDataFrame` objects interchangeably. This needs an LLM key
configured (`LLM_API_KEY` / `AVALAI_API_KEY` / `OPENAI_API_KEY`) - unlike
the rule-based path above, the operation chain is planned by the model,
not built mechanically from a fixed amenity list. `LLMSpecGenerationError`
and `PlanningError` propagate unchanged if the model's plan doesn't pass
validation or can't be built into a DAG; a plan that builds but fails
during execution raises `RuntimeError` with the executor's own message.
It is a thin wrapper only - every class it wires up stays directly usable
for more control (custom `context`, a different LLM client, inspecting
the DAG plan before executing it). `python examples/s3geo_quickstart.py`
runs this over the same Vienna sample data as `accessibility_analysis.py`.

**`import s3geo` alone reaches that finer-grained control, too.** Every
class `query()` wires up internally is re-exported as `s3geo.<Name>` -
the exact same object defined in `orchestrator.planning` /
`orchestrator.capability_registry`, not a copy (`tests/test_s3geo.py`
asserts this with `is` identity checks). No need to know the pipeline
lives in `orchestrator.*` to reach it:

```python
import s3geo

registry = s3geo.registry()                      # every plugin loaded, tolerant=True by default
binding = registry.resolve("buffer_vector_features")
binding.callable(...)                             # call a specific plugin capability directly

spec = s3geo.LLMQuerySpecGenerator(s3geo.OpenAICompatibleLLMClient()).generate(
    "buffer the sites by 100 meters",
)
plan = s3geo.DeterministicPlanner().build(spec)   # inspect the plan before executing it
result = s3geo.DagExecutor(s3geo.RegistryCapabilityResolver(registry)).execute(
    plan, initial_inputs={"sites": sites_geojson},
)
```

Also re-exported: `StaticLLMClient` (for tests/local runs without a real
LLM key), and the errors above - `s3geo.LLMSpecGenerationError`,
`s3geo.PlanningError`, `s3geo.DagExecutionError`,
`s3geo.CapabilityResolutionError`.

### Over HTTP

```python
import json, urllib.request

body = {"query": "Display the sites on the map", "inputs": {"vector": sites_geojson}}
req = urllib.request.Request("http://127.0.0.1:8000/query",
                             data=json.dumps(body).encode(), method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("X-API-Key", "...")          # when the server requires a key
response = json.loads(urllib.request.urlopen(req).read())

for layer in response["layers"]:
    print(layer["name"], layer["summary"]["feature_count"])
```

`inputs` is required and must be an object even when empty - it is where
the data the question refers to is passed in, keyed by role (`vector`,
`raster`, or a named layer). `python examples/query_via_http.py` runs this
against a live server.

Questions are understood in English and Persian. A question is answered by
the LLM-backed planner when an LLM key is configured, and by the rule-based
paths otherwise.

## Running from a checkout

For development on the system itself, or to use the React workbench:

```bash
git clone https://github.com/arazshah/smart_spatial_system.git
cd smart_spatial_system

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock      # pinned; requirements.txt for latest upstream

cp .env.example .env                  # add your LLM key
uvicorn api.main:app --reload         # http://127.0.0.1:8000/docs
```

Frontend:

```bash
cd frontend
cp .env.example .env
npm install && npm run dev            # http://localhost:5173
```

Docker Compose (backend + frontend, PostGIS optional):

```bash
docker compose up --build
```

PostGIS demo data (Tehran OpenStreetMap): see [data/README.md](data/README.md).
Full deployment guide - environment variables, authentication, CORS, PostGIS:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## API at a glance

| Area | Endpoints |
|---|---|
| Query | `POST /query` · `POST /planner/intent` · `POST /feedback` |
| Requests & outputs | `GET /requests` · `GET /requests/{id}` · `…/map-layers` · `…/outputs` · `…/outputs/files/{name}` · `…/documents/{name}` |
| Projects & data | `/projects` · `/uploads/raster` · `/uploads/vector` · `/data-sources/{csv-table,wms,wfs,postgis,url}` |
| Plugins & settings | `/plugins` · `/plugins/{id}/config` · `/settings/runtime` · `/settings/llm/smoke-test` |
| Router weights | `/weights` · `/weights/save` · `/weights/reload` · `/weights/proposals/apply` |
| System | `GET /health` |

Full request and response contracts are in [docs/phase5_query_api_contract.md](docs/phase5_query_api_contract.md) and the other `docs/phase5_*` files.

## Research and case studies

Looking for a study to build on this system, or to see one already done?
[docs/CASE_STUDIES.md](docs/CASE_STUDIES.md) has a catalog of suggested
studies (accessibility, risk-aware siting, vegetation change, zoning
compliance, and a reproducibility-methodology replication) plus what's
*not* a good fit yet. If you use this software in published work, see
[CITATION.cff](CITATION.cff).

### Case studies and papers written with s3geo

Four research studies so far have used s3geo on real OpenStreetMap data. In
each one, an LLM planned the analysis from a plain-language question and was
checked against the same analysis written by hand. Each study has its own
paper or plan. The three that have run each found real defects in this
package, and all of those defects are now fixed and released.

| # | Study | City | Question | Status | What it changed in s3geo |
|---|---|---|---|---|---|
| 1 | [Vienna accessibility](https://github.com/arazshah/smart-spatial-vienna-accessibility) | Vienna | District accessibility to metro, schools and parks | Reference study: established the method | 5 bugs fixed (`0.2.1`–`0.2.4`) |
| 2 | [Tehran TOD gradient](https://github.com/arazshah/smart-spatial-tehran-tod-gradient) | Tehran | Does land-use diversity fall with distance from 122 metro stations? | Paper draft | 3 defects fixed (`0.2.5`–`0.2.9`); `ring_buffer_analysis` plugin |
| 3 | [Istanbul health access](examples/istanbul_health_access/README.md) | İstanbul | Which mahalle are underserved by hospitals and clinics? | **Complete**: full paper, results and figures, included in this repo | 6 bug reports and 3 enhancements (`0.4.1`–`0.5.6`) |
| 4 | [Urmia real estate](https://github.com/arazshah/smart-spatial-urmia-real-estate) | Urmia | Real-estate suitability ranking using transit, malls, roads, risk and zoning | Scaffolded, not yet run | Reuses the shipped real-estate workflow |

All four share one design. **Arm 1** is a deterministic plan written by
hand. **Arm 2** is the same question in plain language, planned by
`LLMQuerySpecGenerator` / `s3geo.query()` and run N times. The arms are
compared with Plan Agreement Rate, variance in the chosen parameters, Set
and Rank Stability, and agreement with Arm 1. Vienna's
`paper/comparison_metric.md` defines the metric, and the İstanbul study
reuses it unchanged.

#### 1. Vienna: district accessibility to metro, schools and parks

**Repository:** [`smart-spatial-vienna-accessibility`](https://github.com/arazshah/smart-spatial-vienna-accessibility)

This is the reference reproducibility study. It runs the same analysis two
ways, as a deterministic rule-based `QuerySpec` and as one planned entirely
by `LLMQuerySpecGenerator`. It measures Plan Agreement Rate, parametric
variance and Rank Stability across N repeated LLM runs. It focused on LLM
planning *reliability*.

**What it changed here:** it found and fixed five correctness bugs
(`0.2.1`–`0.2.4`):

- a silent domain-wrong default
- an undocumented required input role
- multi-factor scoring that was not chained
- an asymmetric CRS mismatch
- a scoring factor that silently defaulted to the wrong type

#### 2. Tehran: land-use diversity gradient around metro stations

**Repository:** [`smart-spatial-tehran-tod-gradient`](https://github.com/arazshah/smart-spatial-tehran-tod-gradient).
The paper draft is `paper/paper.md` in that repository.

This study tests whether land-use diversity around Tehran's 122 metro
stations changes systematically with distance, a transit-oriented
development (TOD) gradient. It ran two ways:

- a deterministic pipeline with plugin calls chosen by hand;
- a pipeline planned entirely from one plain-language question.

Both ran through the same `DeterministicPlanner`/`DagExecutor` engine. This
study focused on *correctness*.

**What it changed here:** running the LLM arm with a real model
(gpt-4o-mini) on real data surfaced three defects, fixed in `0.2.5`–`0.2.9`:

- a missing multi-ring buffer primitive, which is now the
  `ring_buffer_analysis` plugin;
- the planner choosing a boolean membership filter
  (`filter_points_in_polygon`) where a join that keeps zone identity
  (`spatial_join`) was needed;
- a `spatial_join` cardinality default (`"first"`) that silently dropped
  matches.

The STRtree-indexed `spatial_join` engine also came from this study. After
the fixes, the LLM-planned output is bit-identical to the hand-written
pipeline on every metric.

#### 3. İstanbul: mahalle underserved by hospitals and clinics

**Folder in this repository:** [`examples/istanbul_health_access/`](examples/istanbul_health_access/README.md).
It was developed in [`smart-spatial-istanbul-health-access`](https://github.com/arazshah/smart-spatial-istanbul-health-access).
The paper is [`paper/paper.md`](examples/istanbul_health_access/paper/paper.md).

This is the most complete study so far. It is the only one included in full
here, with notebooks, paper, results and figures. The data is 964 mahalle
and 1,020 hospitals/clinics from OSM:

- **Arm 1** is written by hand: EPSG:32635, nearest facility, and underserved
  mahalle at 1,000, 1,500 and 2,000 m.
- **Arm 2** is `s3geo.query()` run N=20 times. It is given no threshold,
  CRS or operation list.

**Result:** at `0.5.6`, **20/20 unhinted LLM-planned runs return exactly
Arm 1's set of underserved mahalle**. Jaccard vs. Arm 1 is 1.0000. All 20
runs use EPSG:32635, which the framework derived from the data's own
extent.

**What it changed here:** every earlier pin, from `0.3.0` to `0.5.5`,
failed for a specific reason. Each reason was reported in the study's
`bugs/` or `enhancements/` and fixed in `0.4.1`–`0.5.6`:

- raster extras required even for vector queries
- unknown op params passed through silently
- the shape of the `where` value never shown to the LLM
- explicit `None` overriding plugin defaults
- a CRS code leaked from a prompt example
- `nearest_neighbor` sped up about 150× with an STRtree
- a projected CRS derived from the input data
- a `max_distance`/`where` validator
- one repair retry

#### 4. Urmia: real-estate suitability ranking

**Repository:** [`smart-spatial-urmia-real-estate`](https://github.com/arazshah/smart-spatial-urmia-real-estate).
The plan is `paper/PLAN.md` in that repository.

This study reuses this package's shipped real-estate ranking workflow end
to end: `real_estate_spatial_enrich` → `real_estate_score` →
`filter_attribute` → `rank_features` → `build_report` (see
`orchestrator/planning/op_catalog.py`). It runs on real OSM data for
Urmia's roads, transit hubs and shopping centers, combined with
flood/earthquake/fire risk and allowed-construction zoning. Urmia has no
metro, so `transit_hubs` stands in for its public-transit hubs, the same
convention as
[`urmia_real_estate_ranking.py`](examples/urmia_real_estate_ranking.py).

**Status:** scaffolded, not yet run. Both arms are written, and their logic
that needs no network or LLM has been verified offline. That repository's
`paper/PLAN.md` lists what is left.

Want to write the next one? [docs/CASE_STUDIES.md](docs/CASE_STUDIES.md)
lists suggested studies, and the
[case-study issue template](.github/ISSUE_TEMPLATE/case_study.md) is where
to propose one.

## Development

```bash
pytest                # full suite
ruff check .          # lint
```

Contributions are welcome - see [CONTRIBUTING.md](CONTRIBUTING.md). Found a
security issue? See [SECURITY.md](SECURITY.md) rather than opening a public
issue.

## Author

[Araz Shahkarami](https://github.com/arazshah) · [araz.me](https://araz.me)
