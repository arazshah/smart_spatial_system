# Phase 5 Plan — Real Estate Ranking to Plugin/Workflow

## Status

Steps 1-5 of 6 done (verified 2026-09). See `docs/REFACTOR_PLAN.md`'s
Phase 5 status note for the up-to-date summary of what landed and what's
still open. Step 6 (removing the legacy direct handler) is deliberately
not done yet: the new path is opt-in via
`real_estate_query_spec_planning_enabled` (default `False`), and per this
plan's own rule ("remove legacy only after a tested replacement exists"),
that removal should wait until the flag has been enabled and verified in
real usage, not just in tests.

One resolution worth calling out explicitly: Open Question 3 (how DAG
ranking preserves both ranked and rejected properties) was resolved
*against* adding a monolithic `real_estate_rank` op as sketched in the
original target table below. Instead, `real_estate_score` only annotates
every feature with `eligible`/`score` (it does not split or sort) — the
already-registered generic `filter_attribute` (`where eligible=true`) and
`rank_features` ops handle the split and ranking. This turned out simpler
and more reusable than the original sketch, so the "Target shape" section
below is superseded by `orchestrator/planning/op_catalog.py`'s actual
`real_estate_score`/`real_estate_spatial_enrich` entries and their notes.

## Correction to the earlier assessment

When this phase was first scoped (see `docs/REFACTOR_PLAN.md`'s status
note), it looked like it would need a from-scratch op/capability design,
because `orchestrator/planning/op_catalog.py` has zero `real_estate_*`
entries. Digging into the actual pipeline changes that: the direct-handler
code is **already decomposed into a clean sequence of mostly-pure
functions**, and several of the *generic* capabilities it would need
already exist in `OP_CATALOG` today - `score_features`, `rank_features`,
`enrich_risk`, and `build_report` (backed by `plugins/feature_scoring.py`,
`plugins/risk_enrichment.py`, `plugins/report_builder.py`) are already
registered ops, already DAG-composable, and already exercised end-to-end
by `tests/test_report_builder.py`. This is a smaller, more mechanical
migration than "design a new architecture" - but the scoring formula and
spatial enrichment are still genuinely custom and need real design
decisions (below), and this remains the README's flagship demo workflow,
so treat the risk level as real even though the shape of the work is
smaller than first thought.

## Current pipeline (verified 2026-09)

`smart_spatial_system/application/services/query_execution/
real_estate_ranking_direct_handler.py::try_handle_real_estate_ranking_directly`
runs, in order:

1. `looks_like_real_estate_ranking_query(query)` - keyword-based classifier
   (`real_estate_classifier.py`). This is how the query even reaches this
   handler; the deterministic/LLM planner never sees a QuerySpec for it.
2. `extract_property_feature_collection_from_inputs` /
   `extract_real_estate_spatial_context_from_inputs` (`real_estate_context.py`)
   - pull the property FeatureCollection and optional POI/road/zone layers
   out of the request's `inputs`.
3. `enrich_property_feature_collection_with_spatial_context`
   (`real_estate_context.py`) - additive fallback: for properties missing
   `distance_to_metro_m`/`distance_to_mall_m`/`distance_to_main_road_m`/
   `in_allowed_zone`, compute them from the optional spatial layers using
   nearest-distance and point-in-polygon helpers in
   `real_estate_spatial_helpers.py`. Conceptually close to what
   `nearest_neighbor`/`spatial_predicate` (both already-registered ops) do
   generically, but wired to specific real-estate field names.
4. `execute_real_estate_ranking` (`real_estate_ranking_execution.py`) -
   genuinely pure: for each feature, calls
   `evaluate_real_estate_eligibility` and `score_real_estate_property`
   (`real_estate_scoring.py`), splits into ranked vs. rejected.
   **`score_real_estate_property` is a hardcoded, non-linear formula**
   (distance clamps like `min(best_poi, 1500.0)/500.0*10.0`, tiered risk
   penalties, a hard penalty for disallowed zones) - it does not fit the
   generic `score_features` capability's linear-weighted-factors model
   without extending that model first (see Open Question 1 below).
5. `build_real_estate_ranking_artifacts` (`real_estate_ranking_artifacts.py`)
   - builds table rows, ranked GeoJSON, summary, and a `ReportSpec`-shaped
   report dict. `build_report` (already a registered op, backed by
   `plugins/report_builder.py`) does something structurally similar for
   arbitrary ranked features already.
6. `try_render_real_estate_ranking_document`
   (`real_estate_document_renderer.py`) - PDF rendering via
   `build_real_estate_pdf_report_payload` + the already-generic
   `pdf_renderer` plugin.
7. `build_real_estate_ranking_response` (`real_estate_ranking_response.py`)
   - assembles the final response dict. Depends on Phase 2 (unified
   response) to not need its own bespoke shape - see that plan's step 3.4,
   which deliberately schedules real-estate last for the same reason.

`real_estate_missing_inputs.py`'s preflight handler (asks the user to
supply property data when the query looks like real-estate ranking but no
usable inputs were found) is UX, not ranking logic - it can very likely
stay a direct handler regardless of how the ranking op itself is
migrated; revisit only if it turns out to depend on internals that move.

## Non-goals for this phase

- No change to the scoring formula's actual behavior (the specific
  numbers/thresholds) - only how it's invoked.
- No change to the PDF report's visual output.
- Not required to also migrate the classifier
  (`looks_like_real_estate_ranking_query`) to something LLM-based - a
  QuerySpec can still be produced by a rule-based/keyword path per
  ADR-001, as long as it goes through QuerySpec/DAG from there instead of
  returning a hand-built dict directly.

## Open questions to resolve before writing code

1. **Does the scoring formula become its own capability, or does
   `score_features`'s `scoring_spec` get extended to express it?**
   Extending the generic scorer (adding clamp/threshold/tiered-penalty
   support to `scoring_spec`) is more reusable for future domains but
   changes a shared capability's contract - more review needed, more
   blast radius. Keeping `score_real_estate_property` as its own
   capability (e.g. `score_real_estate_properties`, registered as its own
   op) is lower-risk and faster, at the cost of not generalizing the
   pattern. Recommendation: start with the low-risk option (own
   capability) - revisit generalizing only if a second domain needs the
   same shape of formula.
2. **Does spatial enrichment become its own capability, or a QuerySpec
   node sequence using existing `nearest_neighbor`/`spatial_predicate`
   ops?** The existing function is a single additive-fallback pass over
   multiple optional layers (metro/malls/roads/zones) in one call, which
   doesn't cleanly decompose into independent DAG nodes without changing
   the "only fill what's missing" semantics. Recommendation: keep as one
   capability (e.g. `enrich_real_estate_spatial_context`) for this phase;
   revisit decomposing further only if a later use case needs the
   individual steps addressable separately.
3. **Where does eligibility rejection fit in DAG semantics?** Today
   rejected properties are filtered out of the ranked table but still
   reported (with reasons) in the response. Whatever capability boundary
   is chosen for step 4 needs to preserve returning both
   `ranked_features` and `rejected_rows`, not just the survivors - a
   single-output DAG node type may need a documented convention for this
   (e.g. an op that returns a dict artifact with both lists, not a bare
   vector artifact) or a second output edge.

## Target shape

Per REFACTOR_PLAN.md's original phrasing, still a reasonable target
capability/op pair for the *ranking* step specifically:

```
capability: rank_real_estate_properties
op: real_estate_rank
```

But per the pipeline breakdown above, the full workflow realistically
needs a **small number of new/wrapped capabilities**, not one:

| New capability | Wraps | New op |
|---|---|---|
| `enrich_real_estate_spatial_context` | `real_estate_context.py`'s enrichment | `real_estate_spatial_enrich` |
| `rank_real_estate_properties` | `real_estate_ranking_execution.py` + `real_estate_scoring.py` | `real_estate_rank` |

...composed with the **already-registered** `build_report` op for the
report/table/PDF step (reusing `real_estate_ranking_artifacts.py`'s logic
to build the `ReportSpec`/params for it, rather than a new capability).

## Migration steps (small, independently testable, in order)

1. **Golden-test the current direct-handler output first.** Before any
   code moves, add `tests/test_real_estate_ranking_golden.py`: real
   property FeatureCollection input (reuse the fixtures already in
   `tests/test_report_builder.py` and
   `tests/test_real_estate_ranking_bridge.py` where possible) through
   `try_handle_real_estate_ranking_directly`, asserting on ranked order,
   scores, rejection reasons, table rows, and report/document metadata
   field-by-field. This is the regression net for every step after it.
2. **Wrap `evaluate_real_estate_eligibility`/`score_real_estate_property`
   as a plugin capability** (resolving Open Question 1) in a new
   `plugins/real_estate_scoring.py`, calling the existing
   `real_estate_scoring.py` functions unchanged underneath (move the pure
   logic into the plugin, or import it - prefer moving it into the
   plugin file directly so plugins/ stays self-contained the way other
   source/analysis plugins are, and leave a re-export shim behind if
   anything else still imports the old location). Unit-test the plugin
   capability directly; re-run the golden test from step 1 unchanged
   (behavior must not move yet - the direct handler still calls the old
   path).
3. **Wrap the spatial enrichment step** the same way (resolving Open
   Question 2), into `plugins/real_estate_spatial_enrichment.py` or
   similar. Same pattern: unit test the plugin directly, golden test
   still passes because nothing is wired differently yet.
4. **Register both as capabilities + `OP_CATALOG` entries**
   (`real_estate_spatial_enrich`, `real_estate_rank`), following the
   `OpDescriptor` shape already used by `score_features`/`enrich_risk` in
   `orchestrator/planning/op_catalog.py`. Add
   `tests/test_planning_registry_backed_real_estate_execution.py`-style
   coverage (mirroring the existing raster equivalent) asserting the DAG
   can resolve and execute these ops in isolation - still not wired into
   the natural-language dispatch path.
5. **Add QuerySpec generation for real-estate queries.** The existing
   `looks_like_real_estate_ranking_query` classifier can stay as the
   trigger, but instead of calling the direct handler, it should build a
   `QuerySpec` with operations
   `real_estate_spatial_enrich → real_estate_rank → build_report` and
   hand it to the same `DeterministicPlanner`/`DagExecutor` path
   `query_spec_planning_handler` already uses (see
   `_try_handle_query_with_planning` in `query_execution_service.py`).
   This is the step that actually moves this out of "direct-dispatch
   shortcut" territory per ADR-001. Verify against the golden test - this
   is where a real behavior diff is most likely to surface (DAG execution
   and the direct handler may format traces/metadata differently even if
   the artifacts match), so expect to spend real time here, and update
   the golden test deliberately (not silently) for any intentional
   normalization.
6. **Only after step 5 is verified equivalent**, remove
   `real_estate_ranking_direct_handler.py`'s registration from
   `direct_query_dispatch.py` (per REFACTOR_PLAN.md Phase 7's own rule:
   remove legacy only after a tested replacement exists). Keep the module
   itself until Phase 7's cleanup pass, in case of rollback.

## Testing strategy

- Step 1's golden test is mandatory before any other step and must keep
  passing (with only deliberate, documented diffs) through step 5.
- `tests/test_real_estate_ranking_bridge.py` and
  `tests/test_report_builder.py` already exist and exercise adjacent
  surface (the compatibility bridge in `orchestrator/service.py`, and the
  generic `build_report` op respectively) - run both after every step,
  not just at the end.
- This phase depends on Phase 2 (`docs/PHASE2_UNIFIED_RESPONSE_PLAN.md`)
  for the response-shape half of the work (step 5's response, once
  produced via the planning path instead of the direct handler, should go
  through the same response assembler as everything else) - sequencing
  matters: land Phase 2's assembler (at least through the planning-path
  wiring) before step 5 here, or step 5 will need its own one-off
  response shaping that then needs revisiting anyway.

## Suggested commit breakdown

1. Golden tests only (step 1).
2. Scoring capability + its own tests (step 2).
3. Spatial enrichment capability + its own tests (step 3).
4. OP_CATALOG registration + DAG-resolution tests (step 4).
5. QuerySpec generation + planning-path wiring (step 5) - likely the
   largest single commit; consider splitting further once step 4 is done
   and the actual diff size is known.
6. Legacy direct-handler removal from dispatch (step 6), separate PR,
   only after step 5 has been live and verified.
