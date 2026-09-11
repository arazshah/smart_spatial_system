# Smart Spatial System

**Ask a geospatial question in plain language and get map layers, tables, reports and files back.**

Smart Spatial System is a plugin-based GeoAI backend with a React workbench. A question such as *"rank these candidate properties by distance to metro stations, malls and main roads"* is turned into a structured `QuerySpec`, planned as a DAG of spatial operations, executed by plugins against uploaded files or PostGIS, and returned as map-ready outputs with a full execution trace.

It is the application built on top of [geochat-platform](https://github.com/arazshah/geochat-platform): plugins are written with `geochat_sdk` and executed through `geochat_kernel`.

> **Status:** active development, mid-refactor. Phase 6 moves logic out of `orchestrator/` into the layered `smart_spatial_system/` package (see [docs/ARCHITECTURE_TARGET.md](docs/ARCHITECTURE_TARGET.md)). The `orchestrator/*_service.py` modules are compatibility shims during that move.

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

Design decisions are recorded as ADRs in [`docs/`](docs): single kernel pipeline, artifact-based responses, a multilingual semantic layer (Persian queries today, language-neutral concepts inside), and a service-oriented modular backend.

## What is in the box

- **36 plugins**, including buffer, spatial join, intersection, predicates, dissolve, nearest neighbour, distance, area and perimeter, centroids, CRS transform, geometry validation, attribute statistics, zonal statistics, band math, NDVI and spectral indices, slope/aspect, raster clip/reclassify/threshold/statistics, raster-to-vector, geocoding, WMS/WFS fetcher, PostGIS connector, feature scoring and enrichment, local raster/vector loaders, report builder, PDF renderer and data export.
- **Data sources:** raster and vector uploads, CSV tables, WMS, WFS, PostGIS and remote URLs, grouped into projects.
- **Workflows:** real-estate site ranking with a generated PDF report, and NDVI analysis.
- **Learning router:** capability weights adjust from user feedback, with reviewable weight proposals.
- **Workbench:** React + Leaflet UI for queries, step-by-step progress, map layers, inspection, plugin settings and outputs.

## Repository layout

```text
api/                     FastAPI app and routers
orchestrator/            query parsing, planning (QuerySpec, OP_CATALOG, DAG), routing, services
smart_spatial_system/    new layered package (application services; other layers being filled in)
plugins/                 geochat_sdk capability plugins
config/plugins/          per-plugin YAML config (*.example.yaml are the templates)
templates/reports/       report templates (real-estate report)
scripts/sql/             PostGIS views for the Tehran OSM demo
examples/                small fake datasets for the real-estate workflow
frontend/                React + Vite workbench
tests/                   pytest suite (~150 modules)
docs/                    architecture, ADRs, API contracts, phase reports
```

Runtime data (outputs, uploads, projects) is written to `var/` by default, or to `SMART_SPATIAL_RUNTIME_DIR`, and is not committed.

## Getting started

Requires Python 3.10+, Node.js 20+, and optionally PostgreSQL with PostGIS.

```bash
git clone https://github.com/arazshah/smart_spatial_system.git
cd smart_spatial_system

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # add your LLM key
uvicorn api.main:app --reload         # http://127.0.0.1:8000/docs
```

Frontend:

```bash
cd frontend
cp .env.example .env
npm install && npm run dev            # http://localhost:5173
```

PostGIS demo data (Tehran OpenStreetMap): see [data/README.md](data/README.md).

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

## Tests

```bash
pytest
```

## Author

[Araz Shahkarami](https://github.com/arazshah) · [araz.me](https://araz.me)
