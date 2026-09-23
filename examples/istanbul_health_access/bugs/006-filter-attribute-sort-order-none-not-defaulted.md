# 006 — `filter_attribute`/`sort_limit`'s `sort_order` rejects an explicit `None` instead of falling back to its own default

- **Status:** fixed upstream (`smart-spatial-system==0.5.2`)
- **Found:** 2026-09-19/20, phase 4 (`notebooks/03_llm_arm.ipynb`, §5, the
  re-run N=20 batch at `smart-spatial-system==0.5.1` — 8 of 20 real runs,
  plus the smoke test, `run_01.json`/`run_03.json`/`run_06.json`/
  `run_07.json`/`run_09.json`/`run_11.json`/`run_18.json`/`run_19.json`/
  `run_smoke.json`)
- **Affects:** smart-spatial-system 0.5.1 (confirmed present back to at
  least 0.4.2, where `_op_param_reference()` first started advertising
  `sort_order` to the LLM; not investigated further back than that)
  (`plugins/spatial_query_filter.py::filter_features`,
  `orchestrator/planning/planner.py::_map_params`,
  `orchestrator/planning/dag_executor.py::_build_kwargs`)
- **Severity:** hard failure — and, like `bugs/005`, this is **not really
  an LLM mistake**: the model supplied a value (`null`) that is a
  perfectly reasonable way to say "I'm not using this parameter", and the
  framework had every opportunity to treat it as "not specified" (it
  already does exactly that for the sibling parameter `bbox_mode`) but
  doesn't for `sort_order`

## Symptom

After `bugs/005` was fixed (pin moved to 0.5.1) and the N=20 batch was
re-run in full, the batch went from 1 nominal success / 19 failures (at
0.5.0) to **0 successes / 20 failures** (at 0.5.1). 12 of 20 still hit
`bugs/004` (`where must be a dict/object or None.`), but the other 8 of
20 — plus the smoke test — hit a **new** error that did not appear even
once in the previous 0.5.0 batch:

```
Node underserved_mahalle failed: sort_order must be a non-empty string.
```

This is not evidence of a 0.5.1 regression (nothing in the 0.5.1 diff
touches `sort_order` — see `bugs/005`'s "Resolution"); it's a
pre-existing bug that this run's particular sample of LLM plans happened
to trigger where the previous batch's sample didn't. `filter_attribute`
was never asked to sort in this study's query, so whatever plan the LLM
produced evidently still included a `sort_order` key — almost certainly
with the value `null`, matching the same "the LLM includes every key
`_op_param_reference()` lists, using `null` for the ones it doesn't want
to set" behavior already documented in `bugs/004`.

## Root cause

`plugins/spatial_query_filter.py::filter_features`'s real signature:

```python
def filter_features(
    features: Any,
    where: dict[str, Any] | None = None,
    bbox: list[float] | dict[str, float] | None = None,
    bbox_mode: str | None = None,
    geometry_types: str | list[str] | None = None,
    case_sensitive: bool | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    limit: int | None = None,
    offset: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
```

`sort_order` has a real Python default, `"asc"` — so omitting the
keyword entirely works fine. The problem is what happens when the
keyword *is* supplied with the value `None` instead of omitted, which is
exactly what `_map_params()` (`orchestrator/planning/planner.py`) and
`_build_kwargs()` (`orchestrator/planning/dag_executor.py`) do with
whatever the LLM's plan contains — neither function filters out
`None`-valued params before building `static_params`/`kwargs`:

```python
# planner.py::_map_params — passes every param the LLM's plan included,
# value untouched, no None-check:
for logical_name, value in operation.params.items():
    if logical_name in descriptor.param_map:
        static_params[descriptor.param_map[logical_name]] = value
        continue
    ...

# dag_executor.py::_build_kwargs — same, no None-check:
kwargs = dict(node.static_params)
for param_name, ref in node.inputs.items():
    kwargs[param_name] = _resolve_ref(...)
return kwargs
```

So a plan that includes `"sort_order": null` calls
`filter_features(..., sort_order=None)` — an *explicit* `None` passed as
a keyword argument, which in Python overrides the function's own
`sort_order: str = "asc"` default rather than triggering it. Inside
`filter_features`, this raw value is validated immediately and
unconditionally, regardless of whether sorting was even requested
(`sort_by` can be `None` too — the validation still runs):

```python
final_sort_order = _validate_sort_order(sort_order)   # line 922

...

def _validate_sort_order(order: str) -> str:
    if not isinstance(order, str) or not order.strip():
        raise ValueError("sort_order must be a non-empty string.")
    order = order.strip().lower()
    if order not in VALID_SORT_ORDERS:
        raise ValueError(f"Unsupported sort_order '{order}'. Valid orders: {sorted(VALID_SORT_ORDERS)}")
    return order
```

`None` is not a `str`, so this raises immediately.

**The asymmetry that makes this clearly a bug, not just strict
validation:** `filter_features` has a sibling optional parameter,
`bbox_mode`, with the exact same shape — an optional string with a real
default — and it *does* tolerate an explicit `None` from the caller,
via `pick_first()` (`plugins/_shared/plugin_config.py`, returns the
first non-`None` argument or a `default`):

```python
final_bbox_mode = _validate_bbox_mode(
    str(pick_first(bbox_mode, config.get("default_bbox_mode"), default="intersects"))
)
```

If a caller passes `bbox_mode=None`, `pick_first` skips it and falls
through to the config/hardcoded default — no crash. `sort_order` skips
this step entirely and calls `_validate_sort_order(sort_order)` directly
on the raw argument, so the exact same caller behavior (explicit `None`)
is handled two different ways by two parameters of the same function,
one lines apart. This reads as an oversight in `filter_features` itself,
not a deliberate strictness choice — `sort_order` should get the same
`pick_first(sort_order, config.get(...), default="asc")` treatment
`bbox_mode` already gets.

This also affects `sort_limit`, a second `OP_CATALOG` operation that
maps to the same `filter_features` capability and advertises the same
`sort_order` param (`orchestrator/planning/op_catalog.py`, `sort_limit`'s
`param_map`) — not yet observed triggering this in this study's runs
(the LLM never picked `sort_limit`), but the mechanism is identical.

## Reproduction

Against the plugin directly (mirroring `bugs/003`/`bugs/005`'s style).
**Actually executed** against the real `v0.5.1` source (cloned into a
scratch probe directory, `geochat_sdk` stubbed with no-op decorators and
a bare `VectorOut` shim purely to satisfy its two import-time
dependencies — none of the stubbed surface is touched by the code path
this repro exercises, which fails inside `filter_features` itself before
any `VectorOut` construction) — confirmed, not just derived from reading
source:

```python
from plugins.spatial_query_filter import filter_features

empty_fc = {"type": "FeatureCollection", "features": []}

# Omitting sort_order entirely: Python's own default ("asc") applies, works fine.
filter_features(features=empty_fc)
# -> OK

# Passing sort_order=None explicitly — exactly what a plan with "sort_order": null
# in its params produces once _map_params()/_build_kwargs() pass it through:
filter_features(features=empty_fc, sort_order=None)
# -> ValueError: sort_order must be a non-empty string.

# For contrast, the sibling optional param bbox_mode tolerates the identical
# explicit-None case, via pick_first() falling back to its own default:
filter_features(features=empty_fc, bbox_mode=None)
# -> OK, resolves to "intersects"
```

Real output from the executed run:

```
omitted sort_order: OK
explicit sort_order=None: ValueError: sort_order must be a non-empty string.
explicit bbox_mode=None: OK (falls back to default, as expected)
```

This repo's real trigger: `notebooks/03_llm_arm.ipynb` §5, 8 of 20 runs
in the re-run N=20 batch plus the smoke test — same raw query as every
other Phase 4 bug in this repo.

## Proposed fix

### Plugin layer (deterministic)

One-line fix, mirroring the pattern `bbox_mode` already uses two
parameters below it in the same function:
`plugins/spatial_query_filter.py`, change

```python
final_sort_order = _validate_sort_order(sort_order)
```

to

```python
final_sort_order = _validate_sort_order(
    pick_first(sort_order, config.get("default_sort_order"), default="asc")
)
```

(or, more minimally, `_validate_sort_order(sort_order or "asc")` if no
config-level default is wanted — either way, an explicit `None` should
resolve to the function's real default instead of raising).

More valuable long-term, in the same spirit as `bugs/005`'s systematic
fix: audit every `OP_CATALOG`-reachable plugin parameter that has a
non-`None` Python default (`sort_order: str = "asc"` here,
`bbox_mode: str | None = None` is a false-positive-safe example since
its own default *is* `None`, `sort_order: bool = False`-style flags
elsewhere, etc.) for the same "explicit `None` defeats the real default"
pattern — `sort_order` is the one this study happened to hit, but
nothing suggests it's the only parameter across the ~40 `OP_CATALOG`
operations with a non-`None` default that skips a `pick_first()`-style
guard. Ideally this would be enforced structurally: a shared helper (or
a `_build_kwargs()`-level policy in `dag_executor.py`, since that's
where *every* plan's params flow through regardless of which plugin they
target) that drops `None`-valued entries from `kwargs` before the
capability call whenever the target parameter's own signature default is
not `None`, via the same `inspect.signature()` introspection
`bugs/005`'s new test already added for a different check. That would
fix this bug, `bugs/005`'s general shape, and any future parameter with
the same mismatch, in one place.

### LLM-prompt layer (generative)

Nothing currently tells the model that omitting an unused optional
parameter and passing `null` for it are different — from the model's
point of view both plausibly mean "I'm not using this." Two independent
options:

1. State explicitly in the system prompt (near where
   `_op_param_reference()`'s output is inserted) that unused parameters
   should be omitted from `params` entirely, not included with a `null`
   value.
2. Given how naturally an LLM reaches for `null` to mean "not set" (this
   is now the second time this exact behavior has surfaced in this
   study, after `bugs/004`'s `where`/`rules` findings, albeit for a
   different underlying reason), treat this primarily as the plugin
   layer's job: a framework that already tolerates `bbox_mode: null`
   should tolerate `sort_order: null` too, rather than relying on prompt
   wording to stop every model from ever doing something this
   reasonable-looking.

## Local mitigation in this repo, if any

None applied. `s3geo.query()`'s public API doesn't expose a hook to
post-process the LLM's generated `QuerySpec` params before planning, and
adding a `system_hints` note asking the model to omit `sort_order` has
the same fragility problem `bugs/004`'s "Local mitigation" section
already declined for the same reason (hand-maintained prompt patches
drift, and this framework's own `_op_param_reference()`/
`_op_input_roles_reference()` design explicitly exists to avoid that).
Not a Phase 4 blocker in the sense `bugs/002` was (it doesn't crash every
call), but between this and `bugs/004` it is currently blocking *every*
call in this batch from reaching a real result — worth prioritizing
alongside `bugs/004` before the next N=20 re-run.

## Resolution

**Fixed upstream in `smart_spatial_system` 0.5.2** (2026-09-23), commits
`2c1cf2b` ("Treat explicit None as 'not set' for params with real
defaults (0.5.2)") and a same-release follow-up, `922383a` ("Keep
explicit None for params annotated to accept it"). Confirmed by cloning
`github.com/arazshah/smart_spatial_system` at tag `v0.5.2` and reading
both commits' actual diffs directly, plus re-executing this report's own
repro against the fixed source (not taken on the fixer's word):

```
omitted sort_order: OK
explicit sort_order=None: OK (fixed!)
```

Fixed twice over, exactly matching both halves of what this report
asked for:

1. **The immediate fix requested**: `plugins/spatial_query_filter.py`,
   `final_sort_order = _validate_sort_order(sort_order)` →
   `final_sort_order = _validate_sort_order(pick_first(sort_order,
   default="asc"))` — the identical `pick_first()` pattern `bbox_mode`
   already used, two lines above.
2. **The systematic fix requested**: `dag_executor.py::_build_kwargs`
   now calls a new `_drop_none_overriding_defaults()`, which drops any
   `None`-valued *static* (plan-literal) param whose target keyword has
   a non-`None` signature default *and* an annotation that doesn't
   itself declare `None` acceptable (`_annotation_allows_none()`,
   checked via `typing.get_type_hints()` with a textual fallback for
   `from __future__ import annotations`-deferred annotations). Wired in
   by resolving `capability_fn` before building kwargs, so the check
   applies at the one place every plan's params flow through — closing
   this class of bug for every `OP_CATALOG` operation at once, not just
   `filter_attribute`/`sort_limit`. Values resolved from `$inputs.*`/
   `$node.*` references are left untouched (only literal plan params are
   ever dropped), and `planner.py::_map_params` itself is unchanged —
   the plan still records the `null` as given; the fix is applied only
   at the capability call.

**Auditing the rest of `OP_CATALOG` with that same check (per
`CHANGELOG.md [0.5.2]`) found this exact class of bug in several more
operations — some worse than `sort_order`'s loud failure, since they
were *silently* changing behavior instead of raising**:
`filter_points_in_polygon.predicate`, `join_feature_properties.unmatched`,
and `render_pdf.template_name` used to raise the same way `sort_order`
did; `rank_features`/`top_n.descending`, `filter_points_in_polygon.drop_outside`,
`enrich_feature_properties.skip_missing`, `render_pdf.save_to_disk`, and
several field-name params (`score_field`/`rank_field`/`left_key`/
`right_key`/`prefix`/`id_field`/`name_field` across a few operations)
used to silently apply the wrong value instead — e.g. an explicit `null`
for `rank_features.descending` (real default `True`) silently sorted
ascending instead of raising or defaulting correctly. All of these are
fixed by the same `_build_kwargs` change. Params genuinely meant to
accept `None` (e.g. `calculate_attribute_statistics.precision: int |
None = 6`, where `None` means "don't round") are correctly left
untouched — confirmed by the follow-up commit `922383a`, which caught
and fixed an overreach in the first pass of this same fix (the initial
version would have silently rewritten `precision=None` to `6`).

**Also affects, and is fixed for, `sort_limit`** — the second
`OP_CATALOG` operation bound to the same `filter_features` capability
this report flagged as sharing the mechanism, confirmed by the new
`tests/test_op_catalog_none_param_defaults.py` running both
`filter_attribute` and `sort_limit` end to end through the real
`DagExecutor`/`CapabilityRegistry` with `sort_by`/`sort_order`/`limit`
all `None`.

This repo's pin moved `smart-spatial-system==0.5.1` → `==0.5.2` — closes
`bugs/006`. `bugs/004` (`where`-clause shape) is unaffected by this
bump, still open — expected to be the dominant real failure mode in the
next N=20 re-run.
