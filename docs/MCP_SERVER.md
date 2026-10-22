# MCP server

Exposes the capability registry (`CapabilityRegistry`, ~50 capabilities) as
[MCP](https://modelcontextprotocol.io) tools. It adds no analysis logic
(ADR-001): a new plugin becomes a tool by being registered; the tool schema is
derived from the capability's declared inputs and Python signature.

Code: `smart_spatial_system/interfaces/mcp/`. Tests: `tests/test_mcp_*.py`.

```bash
pip install "smart_spatial_system[mcp]"
smart-spatial-api mcp                                  # stdio (local clients, in-app testing)
SMART_SPATIAL_MCP_API_KEY=... smart-spatial-api mcp --transport http --port 8765
```

## Security model

The package runs on shared servers, so nothing relies on plugin config being
tight (the shipped loader configs have `allowed_roots: []`, i.e. unrestricted).
The MCP layer enforces its own policy and fails closed:

| Risk class | Capabilities (today) | Needs |
|---|---|---|
| (none) | pure computation on inline data or artifact handles (78 tools + 2 artifact tools) | nothing, enabled by default |
| `filesystem` | `load_local_vector/raster`, `load_csv_points`, `export_*` (`export_table`, `export_vector_file`, `export_raster_geotiff`), `render_pdf` | `SMART_SPATIAL_MCP_ALLOWED_ROOTS` |
| `network` | `fetch_wfs_features`, `fetch_wms_map`, geocoding, `load_geojson_url`, `fetch_osm_features`, `fetch_dem`, `search_stac` | `SMART_SPATIAL_MCP_ALLOWED_HOSTS` |
| `database` | `fetch_postgis_layer`, `query_database_postgis` | server-side `profile` only |
| `database` + `sql` | `write_postgis_layer` (the only DB writer; also needs `allow_writes` in its config) | enable both on purpose |
| `sql` | `fetch_postgis_sql_layer` | enable explicitly, with `database` |

- Enable classes with `SMART_SPATIAL_MCP_ENABLE=filesystem,network,...`. A class
  that needs roots/hosts refuses to start without them.
- Classification uses an explicit table **plus parameter names** (`path`,
  `output_dir`, `base_url`, `dsn`, `sql`...), so a future plugin that touches
  files/URLs/DBs is hidden by default until someone opts in.
- Paths are resolved (symlinks included) and must sit under an allowed root.
  URLs must be http(s), allow-listed and resolve to public addresses (SSRF
  guard); plugin-side redirects are not controlled, so allow-list only hosts you trust.
- Never exposed to clients: `dsn`, passwords/tokens, `strict_extensions`, any
  parameter that is not a declared input. Schemas are closed
  (`additionalProperties: false`).
- A disabled tool answers exactly like an unknown tool.
- Limits: input and output size, nesting depth, per-call timeout, concurrency
  (`SMART_SPATIAL_MCP_MAX_INPUT_BYTES`, `..._MAX_OUTPUT_BYTES`,
  `..._TIMEOUT_SECONDS`, `..._MAX_CONCURRENCY`). A timed-out call returns at
  once, but the plugin thread cannot be killed and finishes in the background.
- Error text sent to clients has absolute paths reduced to base names.
- Audit log (logger `smart_spatial_system.mcp`): tool, status, duration, size;
  never payloads.
- `SMART_SPATIAL_MCP_ALLOW_TOOLS` / `..._DENY_TOOLS` give an explicit allow/deny list.

HTTP transport: stateless streamable HTTP (works behind a load balancer).
It refuses to start without an API key (`SMART_SPATIAL_MCP_API_KEY`, falling
back to `SMART_SPATIAL_API_KEY`; `Authorization: Bearer` or `X-API-Key`), and on a
non-loopback bind also requires `SMART_SPATIAL_MCP_ALLOWED_HOSTS` (the `Host`
values clients use). Terminate TLS in front of it. A key is a shared secret,
not per-user auth (same model as `api/auth.py`); for multi-tenant use put an
OAuth-aware gateway in front or supply a `TokenVerifier`.

## Data model and artifacts

- Vector data is exchanged as GeoJSON (`FeatureCollection`), so one tool's output feeds the next.
- Rasters and large results are passed **by handle**. A raster result, or any result larger
  than `SMART_SPATIAL_MCP_INLINE_OUTPUT_BYTES` (256 KiB, never above `..._MAX_OUTPUT_BYTES`), is
  stored server-side and the tool returns `{"type": "artifact", "artifact_id": "art_...",
  "kind", "summary", ...}`. Pass `{"artifact_id": "art_..."}` as a `raster` or vector input of
  another tool, read it with `artifact_get` (vector results are paged), remove it with
  `artifact_delete`. Raster inputs can also be sent inline as `{"data": [[...]], "metadata": {...}}`.
- Store: JSON files in `<runtime>/cache/mcp_artifacts` (or `SMART_SPATIAL_MCP_ARTIFACTS_DIR`),
  mode 0700/0600, strict-regex ids (192-bit random), 1 h TTL, 50 MB per artifact, 1 GB total
  (`..._ARTIFACT_TTL_SECONDS`, `..._MAX_ARTIFACT_BYTES`, `..._MAX_ARTIFACT_TOTAL_BYTES`).
  Nothing is pickled. `SMART_SPATIAL_MCP_ARTIFACTS=off` disables it (results then come back
  inline and oversize ones are refused). Workers share artifacts only if they share the
  directory; a handle is a bearer capability within one deployment (one API key, no per-user
  isolation).
- `ndvi_processor` is never exposed (async, needs SDK `RasterIn` objects, writes to the server's
  working directory); use `calculate_ndvi`.
- Input names differ per capability (`features`, `vector`, `source_features`...); schemas show
  the real names.

## Known gaps

- Rasters are stored as nested JSON lists (safe, but bulky): a binary raster format would be
  smaller and faster.
- `requirements.lock` is not regenerated for the new extra; CI skips the HTTP
  test module when `mcp` is absent.
- No per-user identity, rate limiting per client, or tool-level OAuth scopes yet.
