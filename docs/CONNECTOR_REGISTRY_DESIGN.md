# Connector registry: design proposal

Status: **decisions taken 2026-10 (section 7); all five migration steps are implemented**. Implements ADR-004 phase 7
("Data Connector Service: connector registry, normalized connector results, source metadata
and preview") and unblocks the three P0 sources of `docs/PLUGIN_ROADMAP_100.md`
(`csv_xy_loader`, `geojson_url_loader`, `osm_overpass_fetcher`).

## 1. What exists today (verified in code, 2026-10)

- Seven source capabilities in five plugins (`postgis_connector`, `local_vector_loader`,
  `local_raster_loader`, `wms_wfs_fetcher`, `geocoding_resolver`), five different shapes and
  config styles (see `PHASE6_SOURCE_ABSTRACTION_PLAN.md`).
- A "data source" is an *upload record* with `source_type` metadata
  (`DataSourceService._normalize_data_source_metadata`). PostGIS/WFS sources are **fetched once and
  stored as a GeoJSON upload** (snapshot); CSV-table and WMS sources store **metadata only**.
- The per-type fetch-and-register logic lives in the API router
  (`api/routers/data_source_connectors.py`, ~450 lines), duplicated per source type.
- The planner reaches only `load_local_vector` and `query_database_postgis`
  (`op_catalog.py` docstring).

## 2. Problems the registry must fix

1. **No common contract**: every consumer (router, planner, MCP, UI) special-cases each source.
2. **Client-supplied connection details.** `POST /data-sources/postgis` forwards `dsn`, `host`,
   `port`, `user`, `password` from the request body to `psycopg` with no host restriction. Any
   caller (the API key is optional) can make the server open connections to arbitrary hosts
   (internal network scanning, credential relay). WFS already blocks private addresses unless the
   URL comes from deployer config (`allow_private_network`); PostGIS has no equivalent.
3. **Inconsistent error redaction** (only PostGIS redacts; WFS/geocoding can echo URLs with keys).
4. **Unreachable capabilities**: WMS fetch, SQL layer, geocoding have no caller.
5. **Three more sources are coming** and would each add another one-off shape.

## 3. Proposal: a contract layer over capabilities (Option A)

New sources stay ordinary capabilities (ADR-001: nothing bypasses the pipeline). A **connector**
is a capability that declares a `connector` block in its `@capability(metadata=...)`:

```python
metadata={
    "connector": {
        "source_type": "geojson_url",     # stable id, language-neutral
        "kind": "vector",                 # vector | raster | table
        "access": ["network"],            # risk classes, same vocabulary as mcp/policy.py
        "credentials": "none",            # none | profile  (never "client")
        "supports_preview": True,
    }
}
```

`smart_spatial_system/application/connectors/` (new, no plugin imports; resolves through
`CapabilityRegistry`):

- `ConnectorRegistry.from_capability_registry(registry)`: lists connectors, their parameter schema
  (derived from the capability signature, as the MCP catalog already does) and risk classes.
- `ConnectorResult` (normalized, JSON-safe):
  `kind`, `source_type`, `data` (VectorOut / RasterOut / table rows),
  `metadata = {crs, feature_count, bbox, fields: [{name,type}], geometry_types, truncated,
  warnings[], fetched_at, source_ref (redacted), elapsed_ms}`.
- `normalize(capability_result, connector) -> ConnectorResult` and
  `preview(connector, params, limit)` (first N features + schema, bounded).
- Shared **egress policy** `plugins/_shared/net_policy.py` (allow-list, public-address check,
  redirect handling, max bytes, timeout), extracted from `wms_wfs_fetcher._validate_url` and the
  MCP `check_url`. Used by WFS, `geojson_url`, `osm_overpass`.
- Shared **error mapping**: every connector failure goes through `provider_error_mapping`.

Consumers:

- API: one generic `POST /data-sources/connect {source_type, params, project_id}` implemented with
  the registry; `/data-sources/postgis|wfs` stay as thin compatibility wrappers (strangler
  migration, same response).
- Planner: one `OP_CATALOG` op per connector (`load_csv`, `load_url`, `load_osm`, ...), generated
  from the connector block where possible.
- MCP: unchanged mechanism; connector `access` feeds `policy.classify` instead of name heuristics.
- Tests: one parametrized contract suite run over **every** connector (manifest, schema, limits,
  redaction, normalized result, preview), replacing today's per-plugin copies.

Rejected alternative (Option B): a single generic `load_data_source(source_type, params)`
capability that dispatches internally. It gives the planner one op but hides risk from the policy
layer (the MCP/risk class would depend on a parameter value) and breaks "one capability = one
contract". Not recommended.

## 4. Credentials

Connections come from **server-side profiles only** (`config/plugins/*.yaml`, `*_env` secrets).
Request/tool payloads may name a profile but never carry `dsn`/`password`/`host`. The legacy
HTTP payload fields keep working only if `allow_client_connection: true` is set in the plugin
config (default **false**). This is a behavior change for any deployment that relies on posting a
DSN today; it needs a CHANGELOG entry.

## 5. The three P0 sources on this contract

| Connector | Input | Guard rails |
|---|---|---|
| `csv_xy_loader` | upload id / allowed local path / (small) inline text; `x`,`y` columns or WKT column, `crs` | row cap, encoding detection (utf-8, utf-8-sig, cp1256), delimiter sniffing, numeric validation with a report of rejected rows |
| `geojson_url_loader` | https URL | egress policy, max bytes, timeout, content-type check, feature cap, no redirects to other hosts |
| `osm_overpass_fetcher` | bbox (required) + tag filters (key/value allow-list syntax, not raw Overpass QL by default) | max bbox area, max features, deployer-configured endpoint, query timeout, attribution in metadata |

## 6. Migration steps (each independently shippable, tests green)

1. `ConnectorResult` + registry + parametrized contract suite; add `connector` blocks to the 7
   existing source capabilities (no behavior change).
2. Extract `net_policy` and apply it to WFS (no behavior change), then to PostGIS host handling.
3. Generic `/data-sources/connect` + wrappers; client-DSN gating (section 4).
4. Add planner ops for unreachable connectors where a sensible op exists (WMS stays metadata-only).
5. The three new connectors.

## 7. Decisions (taken)

1. Option A (contract layer over capabilities).
2. Client DSN: **server profiles only by default** (`allow_client_connection: false`), breaking and
   recorded in the CHANGELOG.
3. Snapshot (fetch once, store as upload) with `fetched_at` provenance.
4. Overpass: structured tag filters only.
5. Persian CSV: auto-detect windows-1256 in addition to utf-8.

### Implementation status

| Step | State |
|---|---|
| 1 contract: `application/connectors/` (registry, `ConnectorResult`, contract tests) | done |
| 2 `shared/net_policy.py` (WFS and MCP use it) | done; PostGIS host policy is the `allow_client_connection` gate |
| 3 `/connectors`, `/connectors/preview`, `/data-sources/connect`, legacy wrappers, credential gate | done |
| 4 planner ops (`load_url`, `load_wfs`, `geocode`, `load_csv`, `load_osm`) | done; WMS stays metadata-only |
| 5 `geojson_url_loader` (also replaced the unprotected `/data-sources/url` fetch), `csv_xy_loader`, `osm_overpass_fetcher` | done |

HTTP exposure: connectors reachable through the API are limited to `network` and `database`
access (`SMART_SPATIAL_CONNECTOR_ACCESS`); filesystem and raw-SQL connectors are never driven by
an HTTP caller (uploads are the file route).

## 7b. Original open questions (kept for the record)

1. **Option A (recommended) vs Option B** (section 3).
2. **Client DSN default**: flip to server-profiles-only now (secure, breaking), or keep accepting
   it behind `allow_client_connection: true` defaulting to true for one release, then flip?
3. **Snapshot vs live**: keep "fetch once, store as upload" (today's behavior, reproducible,
   works offline) for all connectors, or allow live re-fetch per request for URL/OSM sources?
   Recommendation: snapshot, with `fetched_at` and a "refresh" action.
4. **Overpass**: accept only structured tag filters (recommended) or also raw Overpass QL?
   Raw QL widens the attack and cost surface.
5. **Persian CSV encodings**: auto-detect cp1256/windows-1256 in addition to utf-8 (recommended)?

## 8. Open items found while implementing

- `POST /query` accepts `user_context.postgis_connection` (host/user/password) and the planner ops
  `query_database` / `load_postgis_layer` still list `dsn`/`host`/`port`/`database`/`user`/
  `password` in their `param_map`. That is the same caller-chosen-connection exposure that
  `/data-sources/postgis` had; it is intentionally **not** changed here because it is the main
  PostGIS workflow of `/query`. Recommended follow-up: gate it with the same
  `allow_client_connection` flag (server profile by default).
- Uploaded CSV files are not yet auto-loaded by `csv_xy_loader` in the planner path (the
  `input_reference_resolver` knows the vector and raster loaders only).
- WFS/WMS responses are not size-capped (`requests.get` without streaming); `net_policy.fetch_limited`
  does this for the new connectors and should replace `_http_get_json` / `_http_get_bytes`.
- `fetch_wms_map` writes to a caller-chosen `output_path`; it is excluded from HTTP and MCP
  exposure by its `filesystem` access class.
