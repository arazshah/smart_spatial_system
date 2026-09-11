# Phase 6 Plan — Source Abstraction

## Status

Steps 1-2 of 3 done (verified 2026-09). Step 3 (optional reachability
notes) skipped — this document's own "Current state" section already
records that information, so a separate notes file would just
duplicate it. See `docs/REFACTOR_PLAN.md`'s Phase 6 status note for the
up-to-date summary. The larger ADR-004 Phase 7 connector-registry
design (see "What's deliberately not in this plan" below) remains not
started, as intended.

One correction made while implementing step 1: `postgis_connector.py`'s
int handling turned out not to share real duplication with the other
two plugins once read side by side (it's a looser, isinstance-only
convention with different call sites) — only `wms_wfs_fetcher.py` and
`geocoding_resolver.py`'s `_to_int` were byte-identical. Only those two
were unified; `postgis_connector.py` was left untouched. See
`plugins/_shared/numeric_validation.py`'s docstring for the full note.

## Why this is its own document, and why it's split in two

REFACTOR_PLAN.md's own status note already did the hard part: it
identified that the *only* piece of source-plugin duplication that was
genuinely unsafe to leave (byte-identical local path/allowed-roots
validation, a security-relevant path-traversal guard) is already fixed
(`plugins/_shared/local_path_validation.py`), and that a full common
source-capability contract with "optional semantic discovery" is a
from-scratch design question — the same territory as
`docs/ADR-004-service-oriented-modular-backend.md`'s Phase 7
("Multi-source Data Connectors": connector contracts, connector registry,
PostGIS/WFS/WMS hardening, URL/CSV connectors, local loader alignment,
metadata and preview normalization).

Verifying that against the actual code (five source plugins:
`postgis_connector.py`, `local_vector_loader.py`, `local_raster_loader.py`,
`wms_wfs_fetcher.py`, `geocoding_resolver.py`) confirms the split is
correct, and sharpens it into two genuinely different-sized pieces:

1. **A handful of small, low-risk, independently mergeable fixes** for
   real (if minor) duplication and one real gap — safe to do now, with
   the same discipline as every other phase in this repo (unit tests,
   no behavior change to what already works).
2. **The actual "connector contract + registry" design** ADR-004's
   Phase 7 describes — genuinely a from-scratch effort, not a quick
   follow-on, and explicitly out of scope for this document. This plan
   only records what the five plugins look like today so that whoever
   picks up that design later doesn't have to re-derive it.

## Current state (verified 2026-09, not aspirational)

### Five source plugins, five different capability shapes

| Plugin | Capabilities | Return type | Config file |
|---|---|---|---|
| `postgis_connector.py` | `fetch_postgis_layer`, `fetch_postgis_sql_layer`, `query_database_postgis` | `VectorOut` | `config/plugins/postgis_connector.yaml` |
| `local_vector_loader.py` | `load_local_vector` | `VectorOut` | `config/plugins/local_vector_loader.yaml` |
| `local_raster_loader.py` | `load_local_raster` | `RasterOut` | `config/plugins/local_raster_loader.yaml` |
| `wms_wfs_fetcher.py` | `fetch_wfs_features`, `fetch_wms_map` | `VectorOut` / `RasterOut` | `config/plugins/wms_wfs_fetcher.yaml` |
| `geocoding_resolver.py` | `geocode_place`, `reverse_geocode_point` | `VectorOut` | `config/plugins/geocoding_resolver.yaml` |

All five already share the same config-loading primitives
(`plugins/_shared/plugin_config.py`: `load_plugin_config`,
`get_profile_config`, `pick_first`, `resolve_env_refs`) — that part is
not duplicated and needs no work.

### What's real duplication vs. legitimately domain-specific

Already fixed, per REFACTOR_PLAN.md: local path/allowed-roots validation
(`plugins/_shared/local_path_validation.py`).

Checked again for this plan, three more candidates found:

1. **A three-times-reimplemented "coerce to int, reject bool, raise
   ValueError" helper** — `postgis_connector.py`, `wms_wfs_fetcher.py`
   (`_validate_non_negative_int`/`_validate_positive_int`), and
   `geocoding_resolver.py` (`_validate_limit`) each hand-roll their own
   int-coercion logic. This *is* genuine duplication (same shape, same
   bug surface if one copy's edge case is fixed and the others aren't) —
   but low security relevance (input validation, not access control), so
   lower priority than the local-path fix was, but still worth
   extracting for the same "one bug fixed in three places, three
   maybes-not-fixed copies" reason.
2. **Every other numeric bound is legitimately domain-specific and
   should NOT be unified**, confirming REFACTOR_PLAN.md's existing call
   on `_validate_limit`: postgis allows `0-100000`, wms_wfs's
   `max_features`/`width`/`height` have different per-field caps
   (`100000`/`8192`), geocoding's `_validate_limit` is `1-50` — and is
   reused, oddly, to also validate `timeout_seconds` (capped at 50
   seconds by a function named/intended for result-count limits; a real
   but harmless-today quirk, not a bug, since 50s is still a sane
   timeout ceiling — noted for whoever touches that function next, not
   worth a dedicated fix).
3. **Error-message redaction is inconsistent, and this is a real gap,
   not just style debt.** Only `postgis_connector.py` wraps failures
   through `orchestrator/provider_error_mapping.py`
   (`make_provider_execution_error`/`redact_provider_error_message`) to
   strip credentials from error text before it reaches the response.
   `wms_wfs_fetcher.py` makes outbound HTTP calls
   (`_http_get_json`/`_http_get_bytes`) and `geocoding_resolver.py` calls
   external geocoding providers, but neither redacts anything — a
   failure just re-raises `RuntimeError(f"...Error: {exc}")` with the
   raw exception text, which can include the request URL (and, for a
   provider configured with an API key in the query string rather than
   a header, potentially the key itself). This is the same class of
   issue this session already hardened once this year (WMS/WFS SSRF,
   PostGIS SQL injection) — lower urgency (it needs a failing request to
   trigger, not exploitable on its own), but a real, concrete, low-risk
   fix, not a redesign.

### Connection/credential/CRS handling: confirmed domain-specific, not shared

Each plugin protects a different kind of resource with a different
mechanism: PostGIS has DB credentials (`_build_conninfo`); WMS/WFS has
SSRF-style host blocking (`_is_blocked_ip`/`_validate_url`, with an
`allow_private_network` trust-boundary flag distinguishing
deployer-configured vs. caller-supplied URLs — the fix from this
session's earlier security-hardening work); local vector/raster has
filesystem `allowed_roots` containment. These are the filesystem/network/
credential analogs of the same underlying concern ("don't let the caller
reach somewhere they shouldn't"), but the actual resources being
protected are different enough that force-unifying them into one
mechanism would be a real redesign, not a cleanup — left for the ADR-004
Phase 7 connector-contract design, not this plan.

CRS handling is similarly scattered and non-unifiable without a real
design: PostGIS validates `output_srid` as a positive int; WMS/WFS
accepts a free-form `srs_name`/`crs` string with no validation; local
loaders don't validate/transform CRS at all (read as-is from file
metadata); geocoding has no CRS concept (always WGS84 lon/lat). No
shared CRS validation exists anywhere today.

### "Semantic discovery" is PostGIS-only today — confirmed, not assumed

`orchestrator/planning/postgis_semantic_resolver.py` and
`semantic_planning_context.py` implement natural-language-concept →
table/column resolution exclusively for PostGIS (`PostGISSchemaContext`,
`SemanticLayerCandidate`, safe predicate building). A repo-wide search
for "semantic" outside tests confirms it appears only in these two
files plus `orchestrator/capability_registry.py`,
`orchestrator/capability_scoring.py`, `orchestrator/service.py`, and
`orchestrator/planning/llm_spec_generator.py` — there is no equivalent
for local files, WFS, or WMS. So "optional semantic discovery" in
REFACTOR_PLAN.md's phrasing describes a capability that exists for
exactly one of five sources today; generalizing it is squarely a
from-scratch design question, not something a "quick" Phase 6 pass can
responsibly attempt.

### OP_CATALOG reachability is inconsistent — a real, separate finding

Of the five plugins' seven capabilities, `orchestrator/planning/op_catalog.py`
only maps two: `load_local_vector` (op `load_vector`) and
`query_database_postgis` (ops `query_database`/`load_postgis_layer`).
The other five capabilities are unreachable through QuerySpec/DAG
planning at all:

- `fetch_postgis_layer` and `fetch_wfs_features` are reachable, but only
  via direct API routes (`api/routers/data_source_connectors.py`), which
  resolve them through the capability registry directly, bypassing the
  planner entirely.
- `fetch_wms_map` has **no production caller anywhere** —
  `register_wms_source` in `data_source_service.py` only stores WMS
  connection metadata ("Map rendering can later read
  connection.base_url/layer_name/options") and never actually calls
  `fetch_wms_map`; the capability is exercised only by its own tests.
- `fetch_postgis_sql_layer`, `geocode_place`, and `reverse_geocode_point`
  have **no caller anywhere** in `api/` or `orchestrator/` outside their
  own test files.

This isn't itself unsafe (unreachable code isn't a security or
correctness bug), but it's real information for whoever designs the
ADR-004 connector registry: three of seven capabilities across these
five plugins are effectively dead in production today, and one
(`fetch_wms_map`) looks like it was built ahead of its caller and never
finished being wired in.

### No shared cross-source test contract exists

`test_local_vector_loader_contract.py` and
`test_local_raster_loader_contract.py` each parametrize over a
single-element list containing only their own plugin, exercising
`orchestrator/loader_plugin_contract.py`'s normalization wrapper — this
is a contract shared between exactly those two local loaders, not one
spanning all five source plugins. Per-plugin test files (30-ish tests
each, ~145 total across the five) independently re-implement similar
manifest/descriptor checks with no shared parametrized suite. This
matches "no shared contract exists yet" rather than "a shared contract
exists and needs fixing."

## Non-goals for this phase

- No unified `Connector` base class/interface across all five sources —
  that's ADR-004 Phase 7's job, explicitly deferred (see "What's
  deliberately not in this plan" below).
- No change to any plugin's actual query/fetch behavior, output shape,
  or existing config file format.
- No new OP_CATALOG entries for the five currently-unreachable
  capabilities — registering them is a QuerySpec/DAG reachability
  decision that belongs with the connector-registry design, not a
  drive-by addition here.
- No removal of `fetch_wms_map` or the other callerless capabilities —
  flagged for awareness, not deleted; removing working, tested code
  without understanding why it was never wired in is exactly the kind of
  unforced error this repo's conventions (CLAUDE.md, REFACTOR_PLAN.md)
  warn against.

## What's deliberately not in this plan: the ADR-004 Phase 7 design

ADR-004 already names the target shape: connector contracts, a connector
registry, PostGIS/WFS/WMS hardening, URL/CSV connectors (two source
types that don't exist as plugins yet at all), local loader alignment,
and metadata/preview normalization — with an illustrative `Connector`
list (`PostGISConnector`, `WFSConnector`, `WMSConnector`, `URLConnector`,
`CSVConnector`, `LocalRasterConnector`, `LocalVectorConnector`) that
doesn't even include `geocoding_resolver.py` — an open question for that
design (is geocoding a "connector" in this sense, or a different kind of
capability entirely?) worth flagging now rather than silently deciding
either way.

That design needs to answer, from scratch, at least:

1. What does a shared `Connector` contract's method surface look like,
   given the five sources' capabilities differ not just in parameters
   but in what they fundamentally do (query a database vs. read a file
   vs. fetch over HTTP vs. resolve a place name)?
2. Does "optional semantic discovery" become a contract method every
   connector implements (trivially, for the four without it today), or
   stays a PostGIS-specific extension point other connectors can opt
   into later?
3. Should the three currently-unreachable-via-planner capabilities
   (`fetch_postgis_sql_layer`, `geocode_place`, `reverse_geocode_point`)
   and the never-called `fetch_wms_map` get OP_CATALOG entries as part
   of standardizing the registry, or is "some connectors are
   API-route-only" an intentional, permanent design choice?
4. Do URL and CSV connectors (named in ADR-004, not implemented today)
   get built against the new contract from day one, or retrofitted
   after PostGIS/local/WMS/WFS are aligned?

None of these have an obviously-correct, low-risk answer the way "wrap
an existing function as a plugin capability" did in Phase 5 — each is a
real design decision with real blast radius across all five (soon to be
seven) source plugins and the router/registry layer. Treat as its own
planned effort, the same way this document treats Phase 2 and Phase 5's
former "NOT done" status.

## Migration steps (small, independently testable, in order)

These are the parts of Phase 6 safe to do now, without the ADR-004
design above.

1. **Extract the shared int-coercion helper.** Add
   `plugins/_shared/numeric_validation.py` with a single
   `validate_bounded_int(value, *, label, minimum=None, maximum=None,
   default=None) -> int` function, covering what `postgis_connector.py`'s
   inline checks, `wms_wfs_fetcher.py`'s
   `_validate_non_negative_int`/`_validate_positive_int`, and
   `geocoding_resolver.py`'s `_validate_limit` each do today (reject
   bool, coerce int, bounds-check, raise `ValueError` naming the field).
   Each plugin's existing wrapper (`_validate_limit`, etc.) becomes a
   thin call into it with its own label/bounds — same pattern
   `local_vector_loader.py`/`local_raster_loader.py`'s `_validate_path`
   already follows for `plugins/_shared/local_path_validation.py`. Unit
   test the shared function directly (bounds, bool-rejection, default
   handling); re-run each plugin's existing test suite unchanged
   (behavior must not move — same error messages, same accepted ranges).
2. **Extend error-message redaction to `wms_wfs_fetcher.py` and
   `geocoding_resolver.py`.** Route their outbound-HTTP failure paths
   (`_http_get_json`/`_http_get_bytes` in wms_wfs_fetcher.py; the
   provider-call failure path in geocoding_resolver.py) through
   `orchestrator/provider_error_mapping.py`'s
   `make_provider_execution_error`/`redact_provider_error_message`, the
   same way `postgis_connector.py` already does. Add tests asserting a
   failure with a URL containing a query-string credential-shaped
   parameter produces a redacted message (mirroring
   `tests/test_postgis_connector_percent_escape.py`'s style of a
   narrowly-targeted regression test). This is the one item in this
   plan with a real (if minor) security angle, so it gets the same
   "lowest-risk approach, test the exact failure mode" treatment this
   session's earlier SQL-injection/SSRF fixes did.
3. **Document the OP_CATALOG reachability gap** (no code change): add a
   short note to `orchestrator/planning/op_catalog.py`'s module
   docstring or a new `docs/phase6_source_reachability_notes.md`
   recording which of the five plugins' seven capabilities are
   QuerySpec/DAG-reachable vs. direct-API-route-only vs. callerless
   today (the table in "Current state" above), so this doesn't need
   re-deriving when the ADR-004 connector-registry design eventually
   picks it up. Optional and low-priority relative to steps 1-2; do it
   if there's time, skip it otherwise since this plan document itself
   already records the same information.

Steps 1 and 2 are independent of each other and of step 3 — land as
separate commits/PRs in any order.

## Testing strategy

- Each step's own new unit tests (shared helper, redaction) are the
  primary net — independent of any per-plugin behavior test.
- Re-run each affected plugin's full existing test file after its step
  (`tests/test_postgis_connector.py`, `tests/test_wms_wfs_fetcher.py`,
  `tests/test_geocoding_resolver.py`) — these steps must not change any
  currently-passing assertion, only add redaction/shared-validation
  underneath.
- No golden test is needed for these steps (unlike Phase 2/5): none of
  them touch response shape or move business logic, so there's no
  "before" output to protect against silent drift the way Phase 2's
  response envelope or Phase 5's scoring formula needed.

## Suggested commit breakdown

1. `plugins/_shared/numeric_validation.py` + its own tests + the three
   plugins' wrappers switched over (step 1) — one commit, mirrors the
   local-path-validation precedent's single-commit shape.
2. Redaction wiring for `wms_wfs_fetcher.py` and `geocoding_resolver.py`
   + new redaction regression tests (step 2) — one commit, or two if the
   two plugins' redaction wiring ends up non-trivially different once
   actually written.
3. Reachability notes (step 3), optional, separate commit if done at
   all.
