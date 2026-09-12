# Phase 7 Plan — Legacy Cleanup

## Status

Step 1 (below) done (2026-09): `SimpleCapabilityRouter`
(`orchestrator/capability_router.py`) and
`run_natural_query` (`orchestrator/natural_query_runner.py`) now carry
deprecation docstrings recording this investigation's finding. No
behavior change - full suite pass count identical before/after. Everything
else in this document remains deliberately **not** scheduled, because
it's genuinely blocked, not just being cautious - see "What actually
unblocks the rest of Phase 7" below.

## The headline finding: this phase is mostly blocked, and that's correct

REFACTOR_PLAN.md's own rule for Phase 7 is "remove only after Phases 4-6
give it a tested replacement path - it isn't safe to touch in isolation."
Verifying that against the actual code (not just trusting the rule)
confirms it's not a formality: three of the four named legacy items —
legacy `PlanNode`/`QueryPlan` (`orchestrator/models.py`),
`run_natural_query_with_routing_evidence`
(`orchestrator/routing_aware_natural_query_runner.py`), and the
"routing-aware raster-only planner" (`RoutingAwarePlanBuilder`,
`orchestrator/routing_aware_plan_builder.py`) — are **one connected live
production call chain**, not three independent cleanup targets:

```
query_execution_service.py:1321
  natural_query_runner=run_natural_query_with_routing_evidence
    -> routing_aware_natural_query_runner.py
         -> RoutingAwarePlanBuilder (the "raster-only planner")
              -> produces QueryPlan/PlanNode (orchestrator/models.py)
                   -> SimplePipelineExecutor executes it
```

This chain is the production fallback whenever a query reaches
`execute_and_persist_natural_query_success_path` (in
`natural_query_execution.py`) and neither a direct-dispatch handler
(real-estate ranking, vector display - these run and return first,
regardless of any planning flag, per `direct_query_dispatch.py`) nor
`query_spec_planning_enabled`'s QuerySpec/DAG planning attempt already
produced a response. `query_spec_planning_enabled` (Phase 3/4) now
defaults `True` (flipped in #12, after this document was first drafted);
`real_estate_query_spec_planning_enabled` (Phase 5) still defaults
`False`, but that flag only chooses *how* the real-estate direct handler
computes its ranking (legacy bridge vs. QuerySpec/DAG) - either way the
real-estate direct handler still intercepts the query before this
fallback chain is reached, so this flag's value doesn't gate whether this
chain runs for real-estate queries at all. As long as any query type
isn't covered by a direct-dispatch handler and either isn't covered by
`query_spec_planning_enabled`'s QuerySpec/DAG attempt or that attempt
fails, this chain is not legacy code kept around for compatibility — it
is **the live, exercised, currently necessary path** for that remainder
of natural-language queries. Removing any piece of it today would break
the product, not just some tests (11 test files exercise
`run_natural_query_with_routing_evidence` alone).

So for these three items, Phase 7 isn't a cleanup task waiting to be
scheduled — it's waiting on a decision that belongs to Phase 4/5, not
Phase 7: now that `query_spec_planning_enabled` defaults `True`, the open
question is whether its QuerySpec/DAG coverage is verified broad enough
(across every intent this fallback chain currently still catches, not
just the query types Phase 4/5 explicitly migrated) to retire this chain
- that needs real production usage data of the already-built,
already-flag-gated replacement paths, per general "don't flip/retire a
production path based on tests alone" caution — not a Phase 7 code
change.

## The fourth item is different, and is this plan's one actionable step

`SimpleCapabilityRouter` (`orchestrator/capability_router.py`) is *not*
part of that chain. It's used by exactly one thing, `run_natural_query`
(plain, not the `_with_routing_evidence` variant) in
`orchestrator/natural_query_runner.py` — and a repo-wide search found
**no caller of `run_natural_query` anywhere in
`smart_spatial_system/application/services/...`**, i.e. nowhere in the
actual request-handling code that builds `/query` responses. Its only
production-code references are:

- `orchestrator/__init__.py`'s package-level re-export (so it's public
  API surface for anything importing `orchestrator` directly, even if
  nothing inside this repo currently calls it that way).
- Its own test coverage: `tests/test_orchestrator_natural_query_pipeline.py`
  (5 tests, imports and exercises the real
  `orchestrator/capability_router.py`/`orchestrator/natural_query_runner.py`)
  and `tests/test_orchestrator_registry_router.py` (calls
  `run_natural_query` with `RegistryBackedCapabilityRouter` substituted
  for `SimpleCapabilityRouter` via the `router` parameter - the
  router-substitution integration path `run_natural_query` was built to
  support).

One thing worth flagging so it doesn't get treated as corroborating
evidence it isn't: `tests/test_end_to_end_natural_query_pipeline.py`
also references `SimpleCapabilityRouter`/`PlanNode`/`QueryPlan` by name,
but it defines its **own self-contained local copies** of all of them
("Test-time models" section) and never imports from `orchestrator` at
all — so despite the name match, it is not evidence that the real
`orchestrator/capability_router.py` is exercised end-to-end anywhere;
it's testing a historical duplicate implementation that happens to share
names.

This is a genuinely different risk profile from the other three items:
no known production caller, so it's a real candidate for the "mark"
half of Phase 7's own instruction ("**mark or move** legacy modules") —
but "no caller found by grep" is not the same certainty as "confirmed
dead," and it's still public `orchestrator` package surface. The
right-sized action now is to mark it deprecated and document the finding
for whoever eventually decides to remove it, not to delete it in this
pass.

## Non-goals for this phase

- No removal of any of the four named items. Three are actively
  necessary; the fourth (`SimpleCapabilityRouter`/`run_natural_query`)
  gets marked, not deleted, per the reasoning above.
- No change to `query_spec_planning_enabled`/
  `real_estate_query_spec_planning_enabled` defaults — flipping those is
  Phase 4/5's decision, made from production usage evidence, not
  something this cleanup phase should force as a side effect.
- No attempt to "finish" Phase 6's connector-registry design just to
  unblock this phase faster — that's its own from-scratch effort per
  `docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md`, unrelated in scope to
  whether `SimpleCapabilityRouter` can be marked deprecated.

## Migration steps

1. **Mark `SimpleCapabilityRouter` and `run_natural_query` as
   deprecated**, not removed. Add a module-level deprecation docstring
   note to `orchestrator/capability_router.py` and
   `orchestrator/natural_query_runner.py::run_natural_query` stating:
   no confirmed production caller as of this investigation (searched
   `smart_spatial_system/application/services/...`), only reachable via
   `orchestrator/__init__.py`'s package re-export and its own test
   suite (`tests/test_orchestrator_natural_query_pipeline.py` and
   `tests/test_orchestrator_registry_router.py`); candidate for removal
   once that's re-confirmed at removal time (re-run the same search,
   since new code could start calling it between now and then) and once
   both of those test files are either removed or repointed at whatever,
   if anything, replaces this simplest-tier pipeline. No behavior
   change — this is a comment/docstring-only commit, verified by running
   the full suite unchanged before and after.
2. **Do not touch the other three items** (`PlanNode`/`QueryPlan`,
   `run_natural_query_with_routing_evidence`,
   `RoutingAwarePlanBuilder`) in this phase. Re-verify their blocked
   status at the start of whatever future session revisits Phase 7 —
   don't assume this document's findings are still accurate without
   re-checking `query_execution_service.py`'s dispatch wiring and the
   current state of `query_spec_planning_enabled`/
   `real_estate_query_spec_planning_enabled`, since those are exactly
   the things expected to change over time as Phase 4/5's new paths get
   more production exposure.

## What actually unblocks the rest of Phase 7 (not this plan's job to do)

Recorded here so a future session doesn't have to re-derive it:

1. `query_spec_planning_enabled` and `real_estate_query_spec_planning_enabled`
   become the default (`True`) — a decision for whoever owns production
   usage of this deployment, informed by real traffic through the
   already-built, already-tested DAG paths, not by this repo's test
   suite alone.
2. Once those defaults flip, `execute_and_persist_natural_query_success_path`'s
   fallback to `run_natural_query_with_routing_evidence` should, in
   practice, stop being reached for the query types Phase 4/5 cover
   (vector display, real estate ranking). Confirm that with real usage
   data or a deliberate monitoring period, not just by reading the code.
3. Only then does removing `run_natural_query_with_routing_evidence`,
   `RoutingAwarePlanBuilder`, and the legacy `PlanNode`/`QueryPlan`
   dataclasses become safe — and even then, check whether any query
   type *outside* what Phase 4/5 cover still falls through to this
   runner (this repo has more intents than just vector-display and
   real-estate-ranking; nothing in this investigation confirmed 100%
   coverage of every query type by the new DAG paths) before deleting
   the fallback that currently catches everything else.
4. Phase 6's connector-registry design (ADR-004's separately-numbered
   Phase 7 — confirmed via full-document search to have zero overlap in
   content with this document's Phase 7) is unrelated to unblocking any
   of the four items here; it does not need to land first.

## Testing strategy

- Step 1 (deprecation marking) needs no new tests — it's non-executable
  documentation. Run the full suite once before and once after to
  confirm zero behavior change (same pass count).
- Everything else in this document is explicitly not being executed, so
  no test plan is needed for it yet — the point of this plan is to
  record *why* it's blocked and *what* unblocks it, not to schedule
  work that can't safely happen yet.

## Suggested commit breakdown

1. Deprecation docstrings on `orchestrator/capability_router.py` and
   `orchestrator/natural_query_runner.py::run_natural_query` (step 1) —
   one small commit, no behavior change.

No further commits are proposed for this phase until the unblocking
conditions above are met.
