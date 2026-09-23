# 005 — `filter_attribute`'s `geometry_type` param_map entry doesn't match the real function signature

- **Status:** fixed upstream (`smart-spatial-system==0.5.1`)
- **Found:** 2026-09-19, phase 4 (`notebooks/03_llm_arm.ipynb`, §5, the
  N=20 batch — 2 of 20 real runs, `run_03.json` and `run_11.json`)
- **Affects:** smart-spatial-system 0.5.0
  (`orchestrator/planning/op_catalog.py`'s `filter_attribute`
  `OpDescriptor`, `plugins/spatial_query_filter.py::filter_features`)
- **Severity:** hard failure, and unlike `bugs/003`/`bugs/004` this one
  is **not an LLM mistake at all** — a perfectly-prompted, perfectly-
  behaving model fails 100% of the time it uses this parameter, because
  the catalog itself is internally wrong

## Symptom

2 of 20 real `s3geo.query()` calls in the N=20 batch failed with:

```
Node underserved_mahalle failed: filter_features() got an unexpected keyword argument 'geometry_type'
```

The LLM's plan used `filter_attribute` with a `geometry_type` parameter
— exactly the parameter name `_op_param_reference()` (the `bugs/003` fix)
told it to use. `strict_params=True` (also the `bugs/003` fix) didn't
catch this either, because `geometry_type` genuinely is a key in
`filter_attribute`'s `param_map` — planning succeeds, and the call still
crashes at execution.

## Root cause

`orchestrator/planning/op_catalog.py`, `filter_attribute`'s
`OpDescriptor` (line 244):

```python
"filter_attribute": OpDescriptor(
    ...
    param_map={
        "where": "where",
        "case_sensitive": "case_sensitive",
        "sort_by": "sort_by",
        "sort_order": "sort_order",
        "limit": "limit",
        "offset": "offset",
        "bbox": "bbox",
        "bbox_mode": "bbox_mode",
        "geometry_type": "geometry_type",   # <-- both sides wrong
        "metadata": "metadata",
    },
    ...
),
```

`plugins/spatial_query_filter.py::filter_features`'s real signature:

```python
def filter_features(
    features: Any,
    where: dict[str, Any] | None = None,
    bbox: list[float] | dict[str, float] | None = None,
    bbox_mode: str | None = None,
    geometry_types: str | list[str] | None = None,   # <-- plural
    case_sensitive: bool | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    limit: int | None = None,
    offset: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
```

The real parameter is `geometry_types` (plural) — used consistently
throughout the rest of `spatial_query_filter.py`
(`_normalize_geometry_types`, `_normalize_bbox`'s neighbor functions,
etc., all plural). `param_map`'s entry maps the *catalog-facing* key
`"geometry_type"` to the *capability-facing* key `"geometry_type"` too
(singular on both sides) — so `_map_params()`
(`orchestrator/planning/planner.py`) passes `geometry_type=` straight
through to `filter_features(**static_params)`, which has no such
parameter and raises `TypeError`.

This is a different shape of bug from `bugs/003`/`bugs/004`: those were
about the LLM not being told something real. Here, the LLM was told
something *wrong* — `_op_param_reference()` (generated directly from
this same `param_map`) faithfully advertises `geometry_type` as a valid
`filter_attribute` parameter, and using it exactly as advertised still
fails. `strict_params=True` only validates that a params key exists in
`param_map` — it has no way to know `param_map`'s own *target* name is
wrong, since nothing checks `param_map`'s values against the real bound
function's actual signature.

Checked every other `filter_attribute` param_map entry against
`filter_features`'s real signature — all nine other keys match exactly;
`geometry_type`/`geometry_types` is the only mismatch in this operation.
Not independently audited across the rest of `OP_CATALOG`'s ~40 other
operations here (see "Proposed fix" for a suggested systematic check
that would catch this class of bug directly, rather than one at a time).

## Reproduction

Independent of the LLM, directly against the planner (same style as
`bugs/003`):

```python
from orchestrator.planning.spec import QuerySpec, OperationSpec, EntitySpec
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig

spec = QuerySpec(
    goal="repro",
    entities=[EntitySpec(ref="features", kind="vector", binding={}, hints={})],
    operations=[
        OperationSpec(
            op="filter_attribute",
            inputs={"vector": "features"},
            params={"geometry_type": "Point"},  # exactly what _op_param_reference() advertises
            output="filtered",
        )
    ],
    outputs=[],
)

# Planning succeeds -- "geometry_type" IS in filter_attribute's param_map:
plan = DeterministicPlanner(PlannerConfig(strict_params=True)).build(spec)

# Execution fails regardless:
# TypeError: filter_features() got an unexpected keyword argument 'geometry_type'
```

This repo's real trigger: `notebooks/03_llm_arm.ipynb` §5, `run_03.json`
and `run_11.json` out of the N=20 batch — same raw query as
`bugs/002`/`bugs/003`/`bugs/004`.

## Proposed fix

### Plugin layer (deterministic)

One-line fix: `orchestrator/planning/op_catalog.py` line 244,
`"geometry_type": "geometry_type"` → `"geometry_type": "geometry_types"`
(keep the catalog-facing/LLM-facing key singular if that reads more
naturally in a query — only the *target* side needs to match the real
function parameter).

More valuable long-term: add a test (or a startup-time assertion in
`CapabilityRegistry`/`OP_CATALOG` loading) that checks every
`OpDescriptor.param_map`'s *values* are real keyword parameters of the
capability function they're bound to, via `inspect.signature()`. This
class of bug — a catalog entry silently drifting out of sync with the
plugin it describes — can't be caught by `strict_params=True` no matter
how strict, since that only validates against the catalog's own
(possibly wrong) idea of the truth. A signature-based consistency check
would catch this one and prevent the same mistake in any future catalog
entry, for every operation at once rather than one report at a time.

### LLM-prompt layer (generative)

Not applicable — this isn't a case of the LLM guessing wrong or not
being told something; it did exactly what `_op_param_reference()` (built
from this same `param_map`) told it to do. No prompt change fixes a
wrong catalog entry.

## Local mitigation in this repo, if any

None available or needed — `filter_attribute`'s `where`-based filtering
(the path most runs actually take) doesn't need `geometry_type` for this
study's query at all; this only surfaces on the specific plans that
happen to add a geometry-type filter. Not a blocker: 2 of 20 real runs
hit this, faithfully recorded by `run_once()`, and it doesn't stop the
batch from completing or from producing its one real success
(`run_06.json`).

## Resolution

**Fixed upstream in `smart_spatial_system` 0.5.1** (2026-09-19), commit
`c97929c` ("Fix OP_CATALOG param_map drift (filter_attribute
geometry_type/geometry_types) (#48)"). Confirmed by cloning
`github.com/arazshah/smart_spatial_system` at tag `v0.5.1` and reading
the actual diff directly, not taken on the fixer's word:

1. The one-line fix requested: `orchestrator/planning/op_catalog.py`,
   `filter_attribute`'s `param_map` entry changed from
   `"geometry_type": "geometry_type"` to
   `"geometry_type": "geometry_types"`.
2. The systematic fix requested: `tests/test_op_catalog_param_map_signatures.py`
   added — builds the real `CapabilityRegistry` from
   `DEFAULT_SAFE_PLUGIN_MODULES` and asserts, for every `OP_CATALOG`
   entry whose capability is registered, that every `param_map` *target*
   is a real keyword parameter of the bound capability function (via
   `inspect.signature()`) or that the function accepts `**kwargs`. Exactly
   the check this report's "Proposed fix" asked for.
3. Running that new test against the rest of the catalog surfaced four
   more instances of the same drift class (per `CHANGELOG.md [0.5.1]`),
   all fixed in the same commit: `query_database` and its
   `load_postgis_layer` alias both advertised a `metadata` param that
   `query_database_postgis()` doesn't accept; `inspect_vector`
   advertised `sample_size`/`include_geometry`/`metadata` that
   `inspect_vector()` doesn't accept; `summarize_vector` advertised
   `metadata` that `summarize_vector_layer()` doesn't accept;
   `display_vector` advertised `title`/`style`/`metadata` that
   `display_vector_layer()` doesn't accept. All five were dead/always-
   `TypeError`-on-use params, none reachable through today's planner
   defaults except `filter_attribute`'s (confirmed live in this study's
   own N=20 batch). Removed from `param_map` rather than guessed at, so
   `_op_param_reference()` no longer advertises any of them to the LLM
   and `strict_params=True` now correctly rejects them at planning time
   if a model tries anyway.

This repo's pin moved `smart-spatial-system==0.5.0` → `==0.5.1` — closes
`bugs/005`. `bugs/004` (`where`-clause shape) is unaffected by this bump,
still open.
