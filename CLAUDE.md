# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Smart Spatial System is a plugin-based GeoAI backend (FastAPI) with a React/Leaflet workbench. A natural-language geospatial question ("rank these candidate properties by distance to metro stations, malls and main roads") is turned into a structured `QuerySpec`, planned as a DAG of spatial operations, executed by plugins (vector, raster, PostGIS, reporting, export) against uploads or PostGIS, and returned as map-ready outputs with a full execution trace.

It is built on top of `geochat-platform` (external repo): plugins are written with `geochat_sdk` and executed through `geochat_kernel`. Both are pulled via git in `requirements.txt`, not vendored here.

**Status: active mid-refactor (Phase 6/7 of `docs/SMART_SPATIAL_SYSTEM_BACKEND_ROADMAP.md`).** Read `docs/ARCHITECTURE_CURRENT.md` and `docs/ARCHITECTURE_TARGET.md` before making structural changes — they define what's being migrated away from and toward.

## Commands

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock      # pinned, reproducible (preferred for CI/onboarding)
# pip install -r requirements.txt     # unpinned direct deps, picks up upstream updates
cp .env.example .env                  # fill in LLM_API key etc.
uvicorn api.main:app --reload         # http://127.0.0.1:8000/docs

ruff check .                          # lint (config: pyproject.toml, scoped rule set — see below)

# Tests (pytest, ~150 modules in tests/)
pytest                                          # full suite
pytest tests/test_deterministic_planner.py      # one module
pytest tests/test_deterministic_planner.py -k test_name   # one test
pytest tests/test_planning_runner.py tests/test_planning_dag_executor.py  # planning path
pytest tests/test_orchestrator_service.py tests/test_orchestrator_service_integration.py  # service path

# Frontend (frontend/)
cd frontend
npm install            # regenerates package-lock.json from the standard npm registry —
                        # never let a lockfile with hardcoded mirror "resolved" URLs back in
                        # (see git history: it broke installs off one specific network)
npm run dev        # http://localhost:5173
npm run build
npm run lint
```

`requirements.lock` is generated from `requirements.txt` in a clean venv (`python -m venv /tmp/lockenv && /tmp/lockenv/bin/pip install -r requirements.txt && /tmp/lockenv/bin/pip freeze | grep -v '^pip==' > requirements.lock`) — regenerate it the same way after changing `requirements.txt`, and always from a clean venv, not `pip freeze` in an ad-hoc/system environment (which pulls in unrelated OS-level Python packages).

Python lint is `ruff`, configured in `pyproject.toml` with a deliberately narrow rule set (`E`, `F`, `W`, `I`, minus `E501` line-length and `E402` import-position — both have large pre-existing counts unrelated to correctness). `F` (pyflakes) already caught and fixed real bugs this way: an unimported-name `NameError` in the legacy `SimpleCapabilityRouter`, a fully dead 100-line duplicate of `normalize_llm_query_spec_for_planning` silently shadowed by a second definition, two undefined-name references in `query_execution_service.py`, and three test functions in `test_orchestrator_service.py` silently shadowed by later same-named definitions (pytest only ever ran the second one). `orchestrator/service.py` is exempted from `F401`/`I001` in `pyproject.toml` — the test suite monkeypatches many of its "unused" imports as module attributes (e.g. `service_module.OpenAICompatibleLLMClient = Fake`), so don't "clean up" imports there without checking every test for `service_module.<Name>` / `from orchestrator.service import <Name>` first. No Python type-checker (mypy/pyright) is configured yet. Runtime data (outputs, uploads, projects, cache) is written under `var/` (override with `SMART_SPATIAL_RUNTIME_DIR`) and is git-ignored; don't treat files there as source.

## Architecture

### The layering: three generations of code coexisting on purpose

This is the single most important thing to understand before editing anything:

1. **`smart_spatial_system/`** — the real, current implementation. Business logic lives in `smart_spatial_system/application/services/**`. This is where you make actual changes.
2. **`orchestrator/*_service.py`** (e.g. `data_source_service.py`, `query_execution_service.py`, `upload_service.py`, `project_service.py`, `output_service.py`, `map_layer_service.py`, `plugin_runtime_service.py`, `feedback_proposal_service.py`, `request_history_service.py`) — **thin compatibility re-export shims**, ~10 lines each, that just import the real class from `smart_spatial_system.application.services.*`. Don't add logic here; if a shim looks too small to contain what you're looking for, the real code is in `smart_spatial_system/`.
3. **`orchestrator/service.py`** (`OrchestratorService`, ~1500 lines) — the original God Service, still the operational boundary the API/frontend calls, and still not fully decomposed. New use-case-specific logic must NOT be added here (see ADR-001, ADR-004) — it should go through the QuerySpec/DAG/plugin pipeline or a dedicated internal service instead.

So: `api/` → `orchestrator/service.py` (facade) → delegates into `orchestrator/*_service.py` shims → real logic in `smart_spatial_system/application/services/`. Other `orchestrator/*.py` modules without a `smart_spatial_system` counterpart (planning, capability registry, weighted router, etc.) are still the live implementation directly in `orchestrator/`.

### The official execution pipeline (target and mostly-current for planning)

```
Natural-language query
  -> QuerySpec              LLM (OpenAI-compatible) or rule-based, PostGIS semantic context
  -> DeterministicPlanner + OP_CATALOG     (orchestrator/planning/planner.py, op_catalog.py)
  -> DagPlan -> DagExecutor  (orchestrator/planning/dag.py, dag_executor.py)
  -> CapabilityRegistry      weighted router, learns from feedback (orchestrator/capability_registry.py, weighted_router.py)
  -> geochat_sdk plugins     vector, raster, PostGIS, reporting, export (plugins/*.py)
  -> outputs                 map layers, tables, documents (PDF/HTML), files, execution trace
```

ADR-001 (`docs/ADR-001-single-kernel-pipeline.md`): **all new functionality must enter through this pipeline** unless explicitly transitional legacy. Do not add new direct/service-level handlers for a specific use case, data source, or output format — add a plugin capability, an `OP_CATALOG` entry, or a QuerySpec/DAG workflow instead.

Known legacy parallel paths still being migrated away (see `docs/ARCHITECTURE_CURRENT.md`): direct real-estate ranking path inside `OrchestratorService`, direct vector-display path, system-status handling inside `/query`, `SimpleCapabilityRouter` (legacy hardcoded router), multiple response builders. Real-estate-specific code currently lives under `smart_spatial_system/application/services/query_execution/real_estate_*.py` and is slated to become a plugin/workflow (`smart_spatial_system/workflows/real_estate/`), not stay as service logic.

### Response/output model

Responses are not yet a single unified schema (this is an open migration — ADR-002, `docs/ADR-002-artifact-based-response.md`). Canonical artifact types being converged on: `vector_layer`, `raster_layer`, `table`, `report`, `file`, `map_view`, `chart`, `text`, `json`. Target public response shape includes `schema_version`, `status`, `success`, `request_id`, `answer`, `artifacts`, `layers`, `outputs` (`files`/`vectors`/`tables`/`rasters`), `report`, `steps`, `warnings`, `next_actions`, `metadata`. `layers` and `outputs` are compatibility projections for the current frontend; `artifacts` is meant to become canonical. Contracts are documented in `docs/phase5_*.md`.

### Plugins (`plugins/*.py`, config in `config/plugins/*.yaml`)

~36 `geochat_sdk` capability plugins, one file per plugin, each with a matching `config/plugins/<name>.yaml` (and a `.example.yaml` template). Categories: source/loader (`local_vector_loader`, `local_raster_loader`, `postgis_connector`, `wms_wfs_fetcher`, `geocoding_resolver`), vector analysis (`buffer_analysis`, `spatial_join`, `spatial_intersection`, `spatial_predicate`, `nearest_neighbor`, `distance_calculator`, `dissolve_aggregator`, `centroid_extractor`, `area_perimeter_calc`, `crs_transformer`, `geometry_validator`, `attribute_statistics`, `feature_enrichment`, `feature_scoring`, `risk_enrichment`), raster analysis (`band_math`, `ndvi_analysis`/`ndvi_calculator`, `spectral_indices`, `slope_aspect`, `raster_clip_mask`, `raster_reclassify`, `raster_threshold`, `raster_statistics`, `raster_to_vector`, `zonal_statistics`), output (`report_builder`, `pdf_renderer`, `data_writer_exporter`). Plugin authoring standards: `docs/PLUGIN_FACTORY_AGENT_GUIDE.md`.

### PostGIS semantic layer

Persian-language natural queries are resolved against PostGIS schema using a semantic layer (`orchestrator/planning/postgis_semantic_resolver.py`, `semantic_planning_context.py`) so the LLM doesn't have to guess table/column/geometry-column names. Internal concept IDs are meant to be language-neutral/English (`metro_station`, `shopping_center`, etc.) with language aliases externalized (see `docs/ADR-003-multilingual-semantic-layer.md`) — current queries/data are mostly Persian but the target supports multiple languages.

### API layer (`api/`)

`api/main.py` is the FastAPI app factory (`create_app`); routers in `api/routers/` are grouped by concern: `system`, `projects`, `uploads`, `data_sources`, `data_source_connectors`, `plugins_settings`, `requests_outputs`, `weights`, `query_planner`. All routers go through `app.state.service` (`OrchestratorService`). Endpoint groups: Query (`/query`, `/planner/intent`, `/feedback`), Requests & outputs (`/requests*`), Projects & data (`/projects`, `/uploads/*`, `/data-sources/*`), Plugins & settings (`/plugins*`, `/settings/*`), Router weights (`/weights*`). Full contracts: `docs/phase5_query_api_contract.md` and sibling `phase5_*` files.

**Auth**: `api/auth.py` is a minimal shared-secret key check (`X-API-Key` header), sized for the "one deployer, one team, self-hosted" model — not multi-tenant/per-user. Set `SMART_SPATIAL_API_KEY` to require it on every router except `system_router` (`/` and `/health` stay open for liveness checks); unset (the default), the API stays fully open, matching pre-auth behavior. `create_app` wires it via `dependencies=[Depends(require_api_key)]` per router in `api/main.py`, not global middleware — adding a new router means deciding there too whether it should be protected. The frontend sends it via `VITE_API_KEY` → `X-API-Key` in `frontend/src/api/client.js` when set.

**Capability resolution from routers**: routers must not import plugin modules directly (see `_resolve_service_capability` pattern in `api/routers/data_source_connectors.py`) — they resolve callables through `svc.registry.resolve(name)`, which returns a `CapabilityBinding`, not a bare callable. Pull `.callable` off it before calling. Getting this wrong doesn't raise loudly — `_resolve_service_capability`'s `except Exception: continue` swallows the `TypeError` from calling a non-callable `CapabilityBinding`, and every request just gets a generic "Capability is not available" 400, indistinguishable from the plugin genuinely being unregistered. This exact bug silently broke `/data-sources/postgis` and `/data-sources/wfs` end to end (every request 400'd) until an actual HTTP-level test caught it — the API's existing test suite was otherwise all unit/router-mocked and never exercised these two routes for real. If a `/data-sources/*` or similar capability-resolving route "can't find" a capability you know is registered, suspect this before suspecting plugin registration.

### Runtime paths

`orchestrator/runtime_paths.py` (`RuntimePaths`) centralizes where generated state goes: `var/{outputs,uploads,projects,reports,cache}/` by default, overridable via `SMART_SPATIAL_RUNTIME_DIR`. Never hardcode `outputs/`, `uploads/`, etc. — use `RuntimePaths`.

### Frontend (`frontend/`)

React 19 + Vite + Leaflet/react-leaflet workbench, no router/state library — plain components in `frontend/src/components/`, API calls centralized in `frontend/src/api/client.js`. `frontend/src/utils/geojsonLayers.js` and `frontend/src/lib/dataSources.js` hold shared logic.

## Where to look for more context (don't re-derive, read these)

- `docs/ARCHITECTURE_CURRENT.md` / `docs/ARCHITECTURE_TARGET.md` — current vs. target state, read first for any structural change.
- `docs/ADR-001..004-*.md` — accepted architectural decisions (single kernel pipeline, artifact-based response, multilingual semantic layer, service-oriented modular backend before microservices).
- `docs/REFACTOR_PLAN.md` — the phased plan (artifact contract → response assembler → planning config → migrate vector/real-estate direct paths → source abstraction → legacy cleanup) with recommended test commands per phase.
- `docs/SMART_SPATIAL_SYSTEM_BACKEND_ROADMAP.md` — the full backend checklist/roadmap, phases 0–10.
- `docs/phase4_*.md`, `docs/phase5_*.md`, `docs/phase6_*.md` — closure reports and contracts for each completed/in-progress phase; check the latest phase doc for the actual current state before assuming the target architecture is already in place.
- `docs/PLUGIN_FACTORY_AGENT_GUIDE.md` — conventions for writing a new plugin.
- `docs/KERNEL_SDK_PRODUCT_ALIGNMENT.md` — how this product aligns with `geochat_sdk`/`geochat_kernel`.
- `data/README.md` — PostGIS demo data (Tehran OpenStreetMap) setup.

When picking up a task: check the most recent `docs/phase*_closure_report.md` first to know which parts of `ARCHITECTURE_TARGET.md` are actually done vs. still aspirational, since this file won't be updated every time the refactor progresses.
