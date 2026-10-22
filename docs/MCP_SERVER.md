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
| (none) | pure computation on inline data (38 tools) | nothing, enabled by default |
| `filesystem` | `load_local_vector/raster`, `export_*`, `render_pdf` | `SMART_SPATIAL_MCP_ALLOWED_ROOTS` |
| `network` | `fetch_wfs_features`, `fetch_wms_map`, geocoding | `SMART_SPATIAL_MCP_ALLOWED_HOSTS` |
| `database` | `fetch_postgis_layer`, `query_database_postgis` | server-side `profile` only |
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

## Data model (v0 limits)

- Vector data is exchanged as GeoJSON, so one tool's output feeds the next.
- Raster tools take an **inline** raster object. Server-side raster files are
  only reachable through the `filesystem` class. An artifact/handle store for
  large rasters is the next step (ADR-002).
- Input names differ per capability (`features`, `vector`, `source_features`...);
  schemas show the real names.

## Known gaps

- `requirements.lock` is not regenerated for the new extra; CI skips the HTTP
  test module when `mcp` is absent.
- No per-user identity, rate limiting per client, or tool-level OAuth scopes yet.
