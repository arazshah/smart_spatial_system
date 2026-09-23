# 003 — unknown operation parameters reach plugins unchecked, failing late with a raw `TypeError`

- **Status:** fixed in 0.4.2
- **Found:** 2026-09-19, phase 4 (`notebooks/03_llm_arm.ipynb`, §4, the
  smoke test, first real LLM call after `bugs/002` was fixed and the pin
  moved to 0.4.1)
- **Affects:** smart-spatial-system 0.4.1
  (`orchestrator/planning/planner.py::PlannerConfig`/`_map_params`,
  `orchestrator/planning/llm_spec_generator.py::_domain_guidance`,
  `s3geo/__init__.py::query`)
- **Severity:** hard failure with a misleading error (not silent — but the
  message names an internal plugin function and its Python-level keyword
  error, not the operation or parameter the LLM's plan actually used, and
  it only appears after a full LLM round trip has already completed)

## Symptom

The very first real `s3geo.query()` call (after the `bugs/002` fix,
0.4.1, `rasterio` installed) reached the LLM, got a plan back, started
executing it, and failed 71.8 seconds in:

```
success: False  latency: 71.803s
FAILED at stage=execution: Node n4_filter_attribute failed: filter_features() got an unexpected keyword argument 'attribute'
```

The error names `filter_features` — the internal plugin function bound to
the `filter_attribute` operation — not `filter_attribute`, the op name
the LLM's plan actually wrote. Nothing in the message says which
parameter name *was* expected, or that `attribute` isn't one of them.
Diagnosing this required reading `op_catalog.py` and the plugin source
directly; nothing surfaced by the framework itself pointed there.

## Root cause

Two independent gaps compound here.

**1. `PlannerConfig.strict_params` defaults to `False`, and `s3geo.query()`
never overrides it.** `orchestrator/planning/planner.py`:

```python
@dataclass(frozen=True)
class PlannerConfig:
    """
    strict_params:
        If True, operation params not listed in OpDescriptor.param_map are rejected.
        If False, unknown params are passed through with their original name.
    """
    strict_params: bool = False
    allow_implicit_entities: bool = True
```

```python
def _map_params(operation, descriptor, *, strict_params):
    static_params = {}
    for logical_name, value in operation.params.items():
        if logical_name in descriptor.param_map:
            static_params[descriptor.param_map[logical_name]] = value
            continue
        if strict_params:
            raise PlanningError(
                f"Operation {operation.op!r} has unsupported parameter "
                f"{logical_name!r} for capability {descriptor.capability_name!r}."
            )
        # MVP-friendly pass-through.
        # This allows plugins to accept new params before op_catalog is updated.
        static_params[logical_name] = value
    return static_params
```

`s3geo/__init__.py::query()` calls `DeterministicPlanner().build(query_spec)`
with no config, so `PlannerConfig()`'s default (`strict_params=False`)
applies. The LLM's plan used `{"op": "filter_attribute", "params":
{"attribute": ...}}`. `"filter_attribute"`'s real `OpDescriptor`
(`op_catalog.py`) maps to capability `filter_features` with `param_map`
`{"where": "where", "case_sensitive": ..., "sort_by": ..., ...,
"geometry_type": "geometry_type", "metadata": "metadata"}` — no
`"attribute"` key. With `strict_params=False`, `_map_params` doesn't
reject `"attribute"`; it passes it through unchanged as a literal
`attribute=` kwarg straight to `filter_features()`, whose real signature
(`plugins/spatial_query_filter.py`) has no such parameter — hence the
`TypeError`, raised deep inside `DagExecutor`, long after generation and
planning both reported success.

**The framework's own test suite already treats `strict_params=True` as
the correct mode**, exactly the same "precedent exists elsewhere, `s3geo`
just didn't follow it" shape as `bugs/002`: every single test in this
repo's clone that constructs a `PlannerConfig` at all passes
`strict_params=True` explicitly (`tests/test_llm_raster_queryspec_normalization.py`,
`tests/test_planning_raster_op_catalog_mapping.py`,
`tests/test_planning_raster_queryspec_execution.py`,
`tests/test_planning_registry_backed_raster_execution.py`,
`tests/test_planning_registry_backed_real_estate_execution.py` — none use
the bare default). `s3geo.query()`, meant to be the simple one-call
entry point, is the one caller in the codebase that doesn't.

**2. The LLM was never told `filter_attribute`'s real parameter names —
and the framework has already solved this exact class of problem for
*inputs*, just not for *params*.** `_domain_guidance()` (the hand-written
prompt text in `llm_spec_generator.py`) never mentions `filter_attribute`
at all. But the file also contains `_op_input_roles_reference()`, which
generates the *input role* section of the prompt directly from
`OP_CATALOG` rather than by hand, with a docstring that names this exact
failure mode almost verbatim:

```python
def _op_input_roles_reference() -> str:
    """
    Every supported operation's required input roles - the exact keys
    "inputs" must use for that operation - generated directly from
    OP_CATALOG's input_map rather than hand-written per-operation
    examples.

    This exists because a hand-written example is exactly how distance_to
    went undocumented here: "Important mappings" below spells out
    filter_by_distance, filter_points_in_polygon and enrich_risk by hand,
    but nobody added an entry when distance_to (source_features/
    target_features, i.e. inputs "vector"/"target") was added to the
    catalog, and the LLM repeatedly omitted "target" as a result. Deriving
    this list from OP_CATALOG means a future operation can't silently go
    undocumented the same way.
    """
```

That fix covers `inputs` keys only. There is no equivalent
`_op_param_reference()` deriving each operation's *parameter* names from
`OP_CATALOG.param_map` — so `filter_attribute`'s real params (`where`,
`sort_by`, `limit`, ...) are exactly as undocumented to the LLM today as
`distance_to`'s inputs were before that fix, for the identical reason
this docstring already diagnoses. `attribute` is a plausible-sounding
guess for "filter by an attribute" with nothing in the prompt to correct
it.

## Reproduction

Not independent of the LLM (temperature 0.1, not 0 — the model is not
guaranteed to reproduce this exact plan every call), but the mechanism is
fully deterministic and reproducible directly against the planner,
without any LLM call:

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
            params={"attribute": "amenity"},   # not a real filter_attribute param
            output="filtered",
        )
    ],
    outputs=[],
)

# Default config (== s3geo.query()'s behaviour): no error yet, silently
# passes "attribute" straight through to filter_features() at execution time.
plan = DeterministicPlanner().build(spec)  # succeeds

# strict_params=True catches it immediately, at planning time, with a
# clear message naming the op and the bad parameter:
DeterministicPlanner(PlannerConfig(strict_params=True)).build(spec)
# PlanningError: Operation 'filter_attribute' has unsupported parameter
# 'attribute' for capability 'filter_features'.
```

This repo's real trigger was `s3geo.query()`'s smoke test
(`notebooks/03_llm_arm.ipynb` §4) against `data/raw/hospitals.geojson` +
`data/raw/mahalle_boundaries.geojson`, raw query in
`paper/PLAN.md` "Arm 2" — the LLM's actual plan JSON wasn't captured
(`run_once()` only stores `operations`/`goal` on success), but the error
message and node id (`n4_filter_attribute`, the planner's own
`{index}_{op}` naming convention) place it unambiguously in a
`filter_attribute` step with an `attribute` param key.

## Proposed fix

### Plugin layer (deterministic)

`s3geo.query()` should build its plan with `strict_params=True` — the
same "the codebase's own tests already treat this as correct, `s3geo`
just needs to catch up" shape as `bugs/002`'s `tolerant` fix:

```python
plan = DeterministicPlanner(PlannerConfig(strict_params=True)).build(query_spec)
```

This turns this exact failure into a clear `PlanningError` raised before
any execution starts: *"Operation 'filter_attribute' has unsupported
parameter 'attribute' for capability 'filter_features'."* — a caller (or
an `LLMSpecGenerationError`-style retry loop, if one existed) could act
on that; nobody can act on `filter_features() got an unexpected keyword
argument 'attribute'` without reading the source, as this report had to.
As with `bugs/002`, exposing it as a real `query(..., strict_params: bool
= True)` parameter (mirroring the now-fixed `tolerant`) would let a
caller opt back into the permissive MVP pass-through if they want it.

### LLM-prompt layer (generative)

Add an `_op_param_reference()` alongside the existing
`_op_input_roles_reference()`, generated the same way — directly from
`OP_CATALOG[name].param_map`, not hand-written — and include it in the
system prompt next to the existing input-roles reference. The existing
function's own docstring already states the exact justification needed;
this is applying it to the other half of `OpDescriptor` it doesn't yet
cover. This wouldn't guarantee the LLM never picks a wrong parameter
name again, but it removes the "wasn't told" version of this failure
entirely, the same way the `distance_to`/`target` fix did for inputs.

## Local mitigation in this repo, if any

None applied — and none is available through `s3geo.query()`'s public
API as it stands in 0.4.1: unlike `tolerant` (now a real parameter after
`bugs/002`'s fix), `strict_params` is not exposed by `query()` at all, so
this repo cannot opt into the safer planner behavior without either
bypassing `s3geo.query()` to wire `DeterministicPlanner(PlannerConfig(strict_params=True))`
manually (defeating the point of testing the one-call `s3geo.query()` API
specifically, which is what Arm 2 is defined to exercise), or patching
pinned source (not permitted, per this file's own top rule).

This is not treated as blocking Phase 4, unlike `bugs/002`: it didn't
fail on every call regardless of content, it's a single LLM plan choosing
a wrong parameter name on one operation, and `notebooks/03_llm_arm.ipynb`'s
`run_once()` already records this outcome faithfully
(`error_stage="execution"`) without crashing the notebook — precisely the
kind of run the N=20 batch (§5) is designed to characterize the rate of.
Reported here per this repo's rule (any confirmed framework gap gets a
report, whether or not it blocks); the plan is to proceed to the N=20
batch and let this report's "Resolution" section, plus
`paper/PLAN.md`'s findings, track how often the same class of parameter
mistake recurs.

## Resolution

**Fixed upstream in `smart_spatial_system` 0.4.2** (2026-09-19,
`CHANGELOG.md` `[0.4.2]` "Fixed") — confirmed by cloning
`github.com/arazshah/smart_spatial_system` at tag `v0.4.2` and reading
the actual diff, not taken on the fixer's word alone:

- `s3geo/__init__.py::query()` now takes a `strict_params: bool = True`
  parameter and builds
  `DeterministicPlanner(PlannerConfig(strict_params=strict_params)).build(query_spec)`
  — exactly the fix proposed above, plus a way to opt back into the
  permissive pass-through that wasn't requested but is a reasonable
  addition (mirroring `tolerant` from the `bugs/002` fix).
- `orchestrator/planning/llm_spec_generator.py` gained
  `_op_param_reference()` (line 998), generated directly from
  `OP_CATALOG`'s `param_map` the same way
  `_op_input_roles_reference()` is, and wired into the system prompt
  alongside it (line 1024) — exactly the prompt-layer fix proposed
  above.

This repo's pin moved `smart-spatial-system==0.4.1` → `==0.4.2` in
`requirements.txt` (same bump as `bugs/002`'s pin move, updated same
day). No local mitigation existed for this bug to begin with (see
"Local mitigation" above), so nothing to remove — the fix is purely
upstream.
