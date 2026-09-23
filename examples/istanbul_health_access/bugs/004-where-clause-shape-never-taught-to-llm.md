# 004 — `filter_attribute`'s `where` value shape is never taught to the LLM

- **Status:** fixed upstream (`smart-spatial-system==0.5.3`)
- **Found:** 2026-09-19, phase 4 (`notebooks/03_llm_arm.ipynb`, §4, the
  smoke test — first real LLM call after `bugs/003` was fixed and the pin
  moved to 0.4.2)
- **Affects:** smart-spatial-system 0.4.2
  (`orchestrator/planning/llm_spec_generator.py::_op_param_reference`/`_domain_guidance`,
  `plugins/spatial_query_filter.py::_eval_where`)
- **Severity:** hard failure with a clear underlying message (the plugin's
  own validation error is good; the problem is the LLM had no way to
  produce a valid value in the first place, and the failure still only
  surfaces after a full LLM round trip)

## Symptom

With `bugs/003` fixed (pin at 0.4.2, confirmed: this run's error is
different in kind from `bugs/003`'s, proving the parameter-*name* fix is
working), the smoke test still failed at execution, this time one layer
deeper:

```
success: False  latency: 126.6s
FAILED at stage=execution: Node underserved_mahalle failed: where must be a dict/object or None.
```

The LLM's plan now uses the correct parameter *name* (`where`) for its
`filter_attribute` operation — `bugs/003`'s fix is doing its job — but
whatever *value* it supplied for `where` isn't a dict, and
`plugins/spatial_query_filter.py::_eval_where` rejects anything that
isn't a `dict` or `None`. The exact malformed value isn't captured
(`run_once()` in `notebooks/03_llm_arm.ipynb` only stores `operations`/
`output` on success, same limitation noted in `bugs/003`), but the
mechanism fully explains the failure independent of what the model
specifically guessed.

## Root cause

`where`'s real accepted shape, read directly from
`plugins/spatial_query_filter.py`:

```python
def _eval_where(properties: dict[str, Any], where: Any, *, case_sensitive: bool) -> bool:
    """
    Evaluate full where expression.

    Supported:
        None
        {"field": "name", "op": "contains", "value": "teh"}
        {"and": [cond1, cond2]}
        {"or": [cond1, cond2]}
        {"not": cond}
        {"name": "Tehran", "population": {"gt": 1000}}
    """
    if where is None:
        return True
    if not isinstance(where, dict):
        raise ValueError("where must be a dict/object or None.")
    ...
```

A real, non-trivial structured schema: canonical `{field, op, value}`
conditions, boolean combinators (`and`/`or`/`not`), and a shortcut dict
form. None of this is documented anywhere the LLM can see it.
`_op_param_reference()` (added in the `bugs/003` fix, 0.4.2) generates
only the *parameter key list* per operation from `OP_CATALOG.param_map`:

```python
def _op_param_reference() -> str:
    """
    Every supported operation's accepted params - the exact keys "params"
    may use for that operation - generated directly from OP_CATALOG's
    param_map rather than hand-written per-operation examples.
    ...
    """
    lines = []
    for name in list_supported_ops():
        params = list(get_op(name).param_map)
        param_desc = ", ".join(params) if params else "(none)"
        lines.append(f"- {name}: params keys = {{{param_desc}}}")
    return "\n".join(lines)
```

That produces `- filter_attribute: params keys = {where, case_sensitive,
sort_by, sort_order, limit, offset, bbox, bbox_mode, geometry_type,
metadata}` — the key `where` is now correctly listed, but nothing says
what a valid *value* for `where` looks like. Searching
`_domain_guidance()`'s full body for `where` finds exactly one mention,
and it's about a completely different operation
(`query_database`/PostGIS's own `where`, a raw-ish clause string in that
context, not `filter_attribute`'s structured dict) — actively confusing
if the model generalizes from it. `filter_attribute`'s `where` shape
(`field`/`op`/`value`, `and`/`or`/`not`, or the shortcut form) appears
nowhere in the prompt.

This is the same underlying problem `bugs/003` diagnosed and half-fixed:
the LLM is taught operation names and, as of 0.4.2, parameter *names* —
but for any operation whose parameter *value* is itself structured (not
a plain string/number/bool), there is still no worked example or shape
description, so the model has nothing to generalize from beyond the bare
key name. `filter_attribute`'s `where` is the first one this study hit,
but the same gap likely affects any other structured param in
`OP_CATALOG` (e.g. `bbox`'s `list[float] | dict[str, float]` union,
`score_features`'s factor specs, etc.).

**Confirmed a second time, independently, in the N=20 batch
(2026-09-19).** 2 of 20 real runs (`run_02.json`, `run_12.json`) failed
with `Node mahalle_enriched failed: rules[0].target is required.` — a
completely different operation (`enrich_feature_properties`, node named
`mahalle_enriched`) hitting the exact same class of gap.
`plugins/feature_enrichment.py::enrich_feature_properties`'s `rules`
parameter is a list of dicts, each requiring a `target` key (plus
`source`/`transform`/`value`), fully documented in the function's own
docstring:

```python
"""
Enrich features using property derivation rules.

Example rules:
    [
      {"target": "distance_to_poi", "source": "distance", "transform": "float"},
      {"target": "inside_buildable_zone", "source": "__in_polygon__", "transform": "bool"},
      {"target": "flood_risk", "value": "low"}
    ]
"""
```

— none of which reaches the LLM prompt, same as `where`.
`_op_param_reference()` lists `rules` as a valid key for this operation
and stops there. This is no longer a one-off hypothesis: two unrelated
operations (`filter_attribute`, `enrich_feature_properties`), both hit
in the same 20-call batch, both failing on the identical underlying
gap — any operation with a structured (not plain-scalar) parameter value
is affected.

## Reproduction

Independent of the LLM, directly against the plugin (mirroring
`bugs/003`'s style of isolating the mechanism, not just the one observed
crash):

```python
from plugins.spatial_query_filter import filter_features

filter_features(features={"type": "FeatureCollection", "features": []},
                 where="amenity = hospital")
# ValueError: where must be a dict/object or None.

# What the plugin actually wants instead:
filter_features(features={"type": "FeatureCollection", "features": []},
                 where={"field": "amenity", "op": "eq", "value": "hospital"})
# (succeeds - empty result set here, but no shape error)
```

This repo's real trigger: `notebooks/03_llm_arm.ipynb` §4's smoke test,
same raw query as `bugs/002`/`bugs/003` (`data/raw/hospitals.geojson` +
`data/raw/mahalle_boundaries.geojson`), `filter_attribute` node named
`underserved_mahalle` by the LLM's own `output` ref.

## Proposed fix

### Plugin layer (deterministic)

Two independent options, not mutually exclusive:

1. Add a generation-time validator in `llm_spec_generator.py`, in the
   same family as the existing `_validate_distance_op_crs_symmetry` and
   `_validate_filter_points_in_polygon_usage`, that structurally checks
   any `filter_attribute`/`sort_limit` operation's `where` value (when
   present) is a `dict` before the plan is accepted — turning this into
   a clear `LLMSpecGenerationError` at generation time instead of a
   `RuntimeError` after a full DAG build and partial execution.
2. `filter_features`'s own `ValueError` message is already reasonably
   clear (`"where must be a dict/object or None."`) — the DAG executor
   could catch and pass through plugin-raised `ValueError`s with the
   node id and operation name prefixed (it already does this for the
   node id per the `"Node {id} failed: {message}"` format observed
   here), which is already happening; the remaining gap is purely that
   it happens at execution time rather than generation/planning time.

### LLM-prompt layer (generative)

Extend `_op_param_reference()` (or add a sibling function) to include a
short worked shape example for any operation parameter whose type isn't
a plain scalar — confirmed needed for at least two operations now, both
already fully documented in their own plugin's docstring, just not
surfaced to the model:
```
- filter_attribute.where: {"field": "<property>", "op": "eq|gt|lt|contains|in|...", "value": <value>}
  or shortcut {"<property>": <value>}, or {"and": [...]}/{"or": [...]}/{"not": ...}
- enrich_feature_properties.rules: [{"target": "<new_property>", "source": "<existing_property_or___in_polygon__>",
  "transform": "float|bool|...", "default": <value>}, ...] (or {"target": ..., "value": <literal>})
```
This is exactly the same fix shape `bugs/003` already established
(derive prompt guidance from the real source of truth rather than a
hand-written example that can silently drift) — applied one level
deeper, to parameter *values* rather than just parameter *keys*.

## Local mitigation in this repo, if any

None available through `s3geo.query()`'s public API — there's no
`system_hints` content this repo could add that would reach
`_op_param_reference()`'s auto-generated section (that part of the
prompt isn't influenced by the caller's `system_hints` string, it's
built from `OP_CATALOG` internally). `system_hints` *could* in principle
carry a hand-written note about `where`'s shape as a workaround, but
that reintroduces exactly the hand-maintained-example fragility this
framework's own `_op_input_roles_reference()`/`_op_param_reference()`
docstrings say they were built to avoid — not applied here for that
reason. Not treated as a Phase 4 blocker for the same reasoning as
`bugs/003`: one LLM plan producing an invalid parameter value is a
legitimate, informative outcome for the N=20 batch to characterize the
rate of, not a hard block.

## Resolution

**Fixed upstream in `smart_spatial_system` 0.5.3** (2026-09-23), commit
`078d552` ("Teach the LLM structured param value shapes; reject
mis-shaped where/rules (0.5.3)"). Confirmed by cloning
`github.com/arazshah/smart_spatial_system` at tag `v0.5.3` and reading
the actual diff directly, plus independently executing every `where`/
`rules` example the fix adds against the real plugin functions in this
session (not taken on the fixer's word):

```
OK: {'field': 'amenity', 'op': 'eq', 'value': 'hospital'} -> matched 1
OK: {'amenity': 'hospital'} -> matched 1
OK: {'beds': {'gte': 100}} -> matched 1
OK: {'and': [...]} -> matched 1
rules OK: {..., 'distance_to_metro': 0.0, 'flood_risk': 'low'}
```

Fixed exactly along the two-part shape this report asked for, both
parts implemented (the report had marked part 2 optional; upstream did
both anyway):

1. **LLM-prompt layer (the primary ask)**: new
   `orchestrator/planning/op_param_shapes.py` — a maintained
   `PARAM_SHAPES` table, keyed by `(capability_name, target_kwarg)` so
   operations sharing a capability (`filter_attribute`/`sort_limit`)
   share one entry, with a description and real JSON examples for
   *every* structured `OP_CATALOG` param, not just `where`/`rules` —
   also `score_features.factors`, `enrich_risk`'s params,
   `join_feature_properties.fields`, `generate_ring_buffers.distances`,
   the raster params, and more, per this report's request not to stop at
   the two confirmed cases. Rendered into the system prompt as a new
   "Structured param VALUES" section, right after the existing param-key
   list. A new `tests/test_op_param_shapes.py`: (a) fails if any
   structured `param_map` target lacks an entry or if an entry is stale,
   closing exactly the "a new structured param added later shouldn't
   silently reopen this gap" request; (b) — the detail worth
   highlighting — **runs every example through the real plugins**, so an
   example that the plugin would itself reject fails the test suite
   instead of teaching the model a wrong shape; (c) asserts the
   validator's operator set (below) is identical to
   `spatial_query_filter.py`'s own `VALID_OPERATORS`, so the two can't
   silently drift apart the way `param_map` and the real signature did
   in `bugs/005`.
2. **Plugin layer (offered as optional; implemented anyway)**: new
   `_validate_structured_param_shapes()` in `llm_spec_generator.py`,
   wired into `LLMQuerySpecGenerator.generate()` alongside the existing
   `_validate_distance_op_crs_symmetry`/`_validate_filter_points_in_polygon_usage`
   checks this report pointed to as precedent. Rejects a mis-shaped
   `filter_attribute`/`sort_limit` `where` or `enrich_feature_properties`
   `rules` with a clear `LLMSpecGenerationError` — naming the operation,
   its output ref, the exact problem, and a correct example — at
   generation time instead of a runtime `ValueError` after a full DAG
   build.

**Upstream is explicit that this was not spot-checked against a live
LLM** (no endpoint/API key available when 0.5.3 was cut) — their tests
confirm the guidance is present in the prompt and that its examples are
accepted by the real plugins, not how often a model now actually
produces a valid value. This repo's next N=20 batch at 0.5.3 is the real
measure of whether the model's success rate improves — not yet run as of
this pin bump.

This repo's pin moved `smart-spatial-system==0.5.2` → `==0.5.3` — closes
`bugs/004`. With this, all three bugs found in this study's Phase 4 work
(`bugs/004`, `bugs/005`, `bugs/006`) are fixed upstream. The next N=20
batch is the first real chance at a clean, complete Arm 2 run — though
even then, the earlier CRS-choice and task-completeness findings from
the 0.5.0 batch's one nominal "success" (wrong CRS, no threshold step)
remain open questions about the LLM's *planning* choices, separate from
these three parameter-handling bugs.
