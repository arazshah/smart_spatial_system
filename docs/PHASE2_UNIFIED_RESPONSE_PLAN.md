# Phase 2 Plan — Unified Response Assembler

## Status

Planned, not started. This is a plan document only — REFACTOR_PLAN.md's
Phase 2 stays "NOT done" until this is executed and merged.

## Why this is its own document

REFACTOR_PLAN.md's Phase 2 entry is three lines ("add
orchestrator/response_assembler.py, tests, produce one schema for
planning/legacy/direct/failure results"). That undersells the real shape
of the problem — verified against the actual current code below — and this
is the highest-risk remaining phase (touches every response the frontend
renders), so it gets a real plan instead of being picked up as a quick
follow-on to something else.

## Current state (verified 2026-09, not aspirational)

`smart_spatial_system/application/services/query_execution/
direct_query_dispatch.py::try_dispatch_direct_query_response` tries, in
order, and returns the **first non-None result as-is, each with its own
independently-built dict shape**:

1. `preflight_direct_response_handler` (missing real-estate inputs prompt)
2. `direct_response_handler` → real-estate ranking
   (`real_estate_ranking_response.py::build_real_estate_ranking_response`)
3. `vector_display_handler` → vector display/summary
   (`vector_display_handler.py::try_handle_vector_display_directly`)
4. `query_spec_planning_handler` (if `query_spec_planning_enabled`) →
   `planning_response.py::build_query_spec_planning_response`

Only if **all four** return `None` does execution fall through to the
legacy `natural_query_runner` path, whose result goes through
`self.response_builder.build_dict(...)` →
`orchestrator/production_response.py::ProductionResponseBuilder` — the
one path that actually produces the "canonical" shape.

**Concrete, verified inconsistency** (not hypothetical): the top-level
`status` field alone has at least two different value sets in production
today:
- `ProductionResponseBuilder`: `"status": "success"` (present tense)
- Real-estate direct response (`real_estate_ranking_response.py:157`):
  `"status": "succeeded"` (past tense) — even though *nested* per-output
  entries in the same payload use `"status": "success"` (lines
  101-139), i.e. the inconsistency exists *within a single response*.
- Planning response (`planning_response.py:100`): also `"succeeded"`/
  `"failed"`.

`frontend/src/components/InspectorPanel.jsx` already has defensive
fallback-chasing to cope with this in practice - it checks
`response.layers`, `response.outputs.vectors`, `response.result.geojson`,
`response.outputs.files`, `response.output_files`,
`response.outputs.documents`, and `response.inspector.documents` as
alternates for what should be one field. That accumulated frontend
complexity is itself evidence for why this phase matters, and a
reasonable secondary cleanup once the backend is unified (not required to
land this phase, and out of scope for the backend-side migration below).

`orchestrator/kernel_artifacts.py` already exists and gives artifact
normalization a real foundation (`GeoArtifact` → the canonical
`vector_layer`/`raster_layer`/`table`/`report`/`file`/`map_view`/`chart`/
`text`/`json` types from ADR-002) - Phase 1 in REFACTOR_PLAN.md's terms.
This phase is about response *envelope* shape, not artifact typing, which
is comparatively done.

## Non-goals for this phase

- No change to what data is computed or returned - only how it's shaped.
- No change to the direct-dispatch *order* (real-estate → vector display →
  planning → legacy fallback) - that's REFACTOR_PLAN.md Phases 4/5/7's
  concern, not this one.
- No frontend rewrite. The frontend's defensive fallback-chasing can stay;
  simplifying it is optional follow-up work once the backend is unified,
  tracked separately.

## Target shape

Per ADR-002, converging on:

```json
{
  "schema_version": "1.0",
  "status": "success|partial_success|failed",
  "success": true,
  "request_id": "string",
  "answer": "string",
  "artifacts": [],
  "layers": [],
  "outputs": { "files": [], "vectors": [], "tables": [], "rasters": [] },
  "report": null,
  "steps": [],
  "warnings": [],
  "next_actions": [],
  "metadata": {}
}
```

`layers`/`outputs`/`report` stay as compatibility projections (the
frontend reads them today per the grep above); `artifacts` becomes the
canonical model other code should build toward, not something this phase
forces every caller to switch to immediately.

## Migration steps (small, independently testable, in order)

1. **Golden-test the current behavior before touching anything.**
   Add `tests/test_query_response_shape_golden.py`: for each of the four
   response sources (real-estate, vector display, planning, legacy
   fallback), capture a full response dict from a real `/query` call
   through `TestClient` with realistic inputs, and assert on it field by
   field (not just top-level keys — nested `outputs`/`layers`/`report`
   shapes too). This is the safety net for every step after it. No
   production code changes in this step.

2. **Add `orchestrator/response_assembler.py`** with a single
   `assemble_response(*, source: str, raw: dict, request_id: str,
   metadata: dict) -> dict` entry point. Initially it does the *minimum*
   normalization: coerce `status` to the `success|partial_success|failed`
   vocabulary (mapping `"succeeded"` → `"success"`, `"failed"` stays), and
   ensure `schema_version`/`success`/`artifacts` (empty list default if
   absent) are always present. Add focused unit tests for the assembler
   itself (input variations → expected normalized output), independent of
   the golden tests from step 1.

3. **Wire the assembler into exactly one source at a time**, verifying the
   step-1 golden test for that source still passes (updated only for the
   specific, intentional normalization — e.g. `"succeeded"` →
   `"success"`) before moving to the next:
   1. `ProductionResponseBuilder` output (lowest risk - already closest to
      canonical).
   2. Planning response
      (`query_execution/planning_response.py`).
   3. Vector display response (`vector_display_handler.py`).
   4. Real-estate ranking response
      (`query_execution/real_estate_ranking_response.py`) - do this one
      last: it's the most differently-shaped today, and it's also the
      README's flagship demo workflow, so it gets the most scrutiny and
      the most golden-test coverage before touching it.

4. **Only after all four sources are wired through the same assembler**,
   consider whether `direct_query_dispatch.py`'s handlers still need to
   return full response dicts at all, versus returning a smaller
   intermediate shape the assembler builds from - that's a further
   simplification, not required to close this phase, and should be its
   own follow-up once the assembler has been live and stable.

## Testing strategy

- Golden tests (step 1) must exist and pass *before* each wiring step,
  and be re-asserted (with only the intentional diff) *after* it - this
  phase is entirely about not changing behavior except the specific
  normalization being made.
- Run the full suite after every step, not just at the end -
  `smart_spatial_system/application/services/query_execution/*` and
  `tests/test_orchestrator_*` are the modules most likely to have
  assertions on exact response shape that need updating in lockstep.
- `pytest tests/test_frontend_api_surface_contract.py` doesn't cover
  `/query` response shape today (verified) - do not treat it as a safety
  net for this phase; the golden tests from step 1 are the real one.

## Suggested commit breakdown

Matches REFACTOR_PLAN.md's own "small commits" rule:

1. Golden tests only (step 1) - no production code change.
2. `response_assembler.py` + its own unit tests (step 2) - not wired in
   yet, dead code at this point, but reviewable in isolation.
3. One commit per source wired in (step 3, four commits).
4. Optional simplification commit (step 4), separate proposal.
