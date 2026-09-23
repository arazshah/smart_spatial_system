# 001 — CRS-appropriateness and max_distance/where composition warnings

- **Status:** implemented (partially — see Resolution) in `smart-spatial-system==0.5.4`
- **Proposed:** 2026-09-23, in response to the `smart-spatial-istanbul-health-access` study's N=20 batch at `smart-spatial-system==0.5.3`
- **Motivating finding(s):** `paper/PLAN.md` "Findings", the 0.5.3 batch entry — 18/20 structurally successful runs, but 0/20 fully valid answers, for two reasons unrelated to any of `bugs/004`/`005`/`006`
- **Request:** the prompt below, ready to paste into the session with write access to `smart_spatial_system`
- **Resolution:** see below — landed same-day as `v0.5.4`, confirmed for real against a fresh clone, not taken on the changelog's word alone

---

## Why this isn't a `bugs/` report

Both problems below are the LLM's planning choice, not the framework
misbehaving — every parameter involved does exactly what its docstring
says. That's why this lives in `enhancements/`, not `bugs/`: nothing here
asks upstream to fix a defect, only to add the kind of guardrail that
already exists for a structurally identical case (`warn_if_geographic_crs`
below) so a **generic, non-expert prompt** — the kind of prompt this study
is deliberately testing, and the kind most real callers will actually
write — has a better chance of producing a valid answer without the
caller having to already know the pitfall in advance.

## The ready-to-paste prompt

```
Hi — following up after 0.5.3. All three bugs from before (004/005/006)
are confirmed fixed: a fresh N=20 batch against a real 964-mahalle /
1020-hospital Istanbul dataset went from 0/20 structurally-successful
runs to 18/20, and every successful run now reaches and completes the
filter_attribute step. Thank you — that's a real, measurable fix.

This is a different kind of report: not a bug, an enhancement request.
Both problems below are the LLM choosing something individually valid
but collectively wrong — every parameter behaves exactly as documented.
I'm not asking you to change that behavior; I'm asking for an opt-in
warning surface, the same shape as one that already exists for a
structurally identical case.

## 1. CRS-appropriateness warning for nearest_neighbor

Symptom: across every batch I've run at every pin (0.5.0 through 0.5.3),
100% of successful runs reproject both layers to a *self-consistent but
geographically wrong* CRS — specifically EPSG:31256, "MGI / Austria GK
East", for a dataset that is entirely within Istanbul, Turkey. The
distance values that come out are internally consistent (same CRS on
both sides, so _validate_distance_op_crs_symmetry correctly does not
fire) but physically meaningless — Istanbul's coordinates are nowhere
near EPSG:31256's actual area of use.

Root cause, read directly from plugins/nearest_neighbor.py (your v0.5.3
tag): there's already a near-identical check for a related problem —

    geographic_warning = None
    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        geographic_warning = (
            "Nearest-neighbor distance is being calculated on a geographic "
            "CRS. Reproject to a projected CRS for reliable physical "
            "distance values."
        )
    ...
    output_metadata = {
        ...
        "warning": geographic_warning,
        ...
    }

This catches "still in degrees" but not "projected to the wrong part of
the planet" — a plan that dutifully reprojects both layers to the same
projected CRS passes with warning=None even when that CRS's own area of
use doesn't contain a single one of the actual input coordinates.

Requested fix:

- Immediate: extend this same `warning` field (no new field, no new
  param — just don't leave it None in this case) to also fire when the
  chosen *projected* CRS's own `area_of_use` (pyproj.CRS(final_source_crs
  or final_target_crs).area_of_use — a real, uncontroversial property of
  every registered CRS, no hardcoded region list needed) does not contain
  the input geometries' actual bounds/centroid before reprojection. Same
  posture as the existing check: doesn't raise, doesn't change the
  computed output, just stops leaving a caller with no signal at all that
  something is off.
- Systematic: the same check belongs wherever else a CRS gets chosen for
  a metric operation, not just nearest_neighbor — worth an audit of every
  op that accepts/produces a target_crs (crs_transform itself included)
  for the same class of blind spot.
- LLM-prompt layer: once the warning field exists, it's already visible
  in the tool output an LLM planner would see in a multi-turn/agentic
  setting — but for the direct s3geo.query() path (no multi-turn repair
  loop that I can see), the real fix has to be prompt-side too:
  _op_param_shape_reference() or a new _domain_guidance() addition could
  state the general principle ("choose a projected CRS whose area of use
  actually covers your input data's location — don't default to a
  familiar or example CRS") without needing per-region hardcoding.

## 2. max_distance / downstream filter composition warning

Symptom: in the same 0.5.3 batch, 7 of 18 successful runs (an 8th
strongly implied, see below) set nearest_neighbor's own
`max_distance=5000.0`, then follow it with a filter_attribute step whose
`where` clause is meant to find mahalle *farther* than some threshold
from their nearest hospital. Every one of these 7-8 runs silently
produces zero results — max_distance discards any candidate beyond
5000m before the filter step ever runs, and nothing downstream flags
that this is why the count came back empty. The other 10/18 runs, which
leave max_distance uncapped, produce real, non-trivial results (128-166
matches). This is invisible without manually cross-referencing each
run's nearest_neighbor metadata against its filter_attribute metadata —
manifest.csv's own success flag reports all 18 as equally "successful."

(One run, out of the 18, inserts an enrich_feature_properties step
between nearest_neighbor and filter_attribute, and that step's own
metadata doesn't forward the parent nearest_neighbor metadata chain, so
I can't directly confirm its max_distance value — only that its filter
step receives exactly 912 candidates, matching every confirmed-capped
run exactly, versus 964 for every uncapped run. Worth checking
independently on your end rather than taking my inference at face value.)

Root cause: `find_nearest_neighbors`'s own `max_distance` behaves exactly
as documented (drops out-of-range candidates before ranking, per its own
docstring) and `filter_attribute`'s `where` behaves exactly as documented
too (evaluates the condition against whatever properties survive).
There's no code anywhere linking the two — no way for either op, in
isolation, to know it's feeding a plan that's mathematically unable to
produce a non-empty result. I checked: `max_distance` doesn't appear
anywhere near `_validate_distance_op_crs_symmetry` or the other
generation-time validators in llm_spec_generator.py.

Requested fix:

- Immediate: nearest_neighbor's output_metadata already carries
  `unmatched_source_count` and `dropped_unmatched_count` right alongside
  `match_count` (confirmed in your v0.5.3 source, same dict literal as
  `warning` above) — the runtime fact needed is already computed, it's
  just never surfaced as a warning. A cheap, always-safe addition: when
  `max_distance` is set and `unmatched_source_count` (or
  `dropped_unmatched_count`) is a non-trivial fraction of
  `source_feature_count`, populate the same `warning` field requested
  above, e.g. "max_distance=5000.0 excluded N of M source features from
  ranking." That alone would have made this pattern visible in every one
  of my 7-8 affected runs without needing any new validator.
- Systematic: a generation-time check, same shape as
  _validate_distance_op_crs_symmetry, that looks for a nearest_neighbor
  op feeding (directly or via a short chain) a filter_attribute/sort_limit
  op whose where clause references this op's own output field (e.g.
  distance_to_<target>) with a numeric threshold — and warns (doesn't
  necessarily reject; a caller might genuinely want a bounded search) when
  max_distance is set low enough that it could make that filter's
  threshold unsatisfiable. I know this is more speculative than #1 above
  and may not be worth a full validator on its own — happy to hear your
  take on whether the cheap metadata-level fix alone is enough.

## What I need back

Same as always: confirm via CHANGELOG.md once you've landed something (or
decided not to, and why — this one's more of a judgment call than
004/005/006 were), so I can independently verify against the real diff
before I change anything on my end. No urgency — I'm handling this with a
documented local mitigation (system_hints text, see this repo's
notebooks/03_llm_arm.ipynb §7) in the meantime, so this isn't blocking.
```

## Resolution

`smart-spatial-system==0.5.4` (2026-09-23, same day as the request),
`CHANGELOG.md [0.5.4]`. Confirmed for real: cloned
`github.com/arazshah/smart_spatial_system` fresh into a new probe
directory, checked out tag `v0.5.4`, read `CHANGELOG.md` and the actual
diff directly (`git diff v0.5.3 v0.5.4`) — not taken on the changelog's
word — and independently executed the parts of the fix that don't need
`pyproj` (unavailable in this session's sandbox; PyPI is blocked here,
the same standing constraint noted since `requirements.txt`'s first
entry) against the real cloned `v0.5.4` source.

**More landed than either request asked for, in one important way.**
Both requested warnings shipped essentially as specified — but upstream
also found and fixed the actual **root cause** of the CRS problem, which
neither this study nor the original request had located: `_domain_guidance()`
in `orchestrator/planning/llm_spec_generator.py` used `EPSG:31256` — the
exact wrong CRS every single run in every batch of this study chose,
from 0.5.0 through 0.5.3 — as the literal worked example for the
`crs_transform` → distance-op pattern, with generic unlabeled
`sites`/`metro` placeholders never tied to any specific city. A model
given a different city's data had nothing telling it not to copy the
example verbatim. This explains a fact this study could never otherwise
account for: not "the model tends to pick geographically inappropriate
CRSs," but "the model reliably reproduces one specific, real, wrong CRS
because the framework's own prompt handed it out as a template." The fix
(confirmed in the real diff) replaces the literal code with a
`<PROJECTED_CRS>` placeholder plus an explicit instruction never to reuse
a CRS code from the prompt or a prior answer, and to pick one whose area
of use actually covers the query's data.

**Both requested warnings, confirmed:**

1. **CRS-appropriateness warning** — new `_crs_area_of_use_warning()` in
   `plugins/nearest_neighbor.py`, wired into the same `warning` metadata
   field `warn_if_geographic_crs` already used (multiple warnings now
   join with `" | "` rather than needing a new field). Inverse-transforms
   the input data's centroid from the claimed CRS back to `EPSG:4326` via
   `pyproj` and checks it against `pyproj.CRS(...).area_of_use` — exactly
   the mechanism requested, no hardcoded region list. Silent (never
   raises) on any resolution failure, matching the request's "opt-in,
   doesn't change behavior" framing. Opt-out via new
   `warn_if_crs_area_mismatch: false`. **Not independently executed in
   this session** — needs `pyproj`, which this sandbox cannot install
   (PyPI blocked); verified by reading the real diff instead, including
   upstream's own new test
   `test_find_nearest_neighbors_warns_for_crs_area_of_use_mismatch`,
   which uses this exact scenario almost verbatim — real Istanbul
   coordinates (28.98, 41.01) reprojected to `EPSG:31256`, asserting the
   warning fires — plus a paired negative test confirming `EPSG:32635`
   (the correct zone, and Arm 1's own CRS choice in this study) produces
   no warning.
2. **`max_distance` exclusion warning** — new
   `_max_distance_exclusion_warning()`, reading a new
   `max_distance_excluded_count` metadata field against
   `source_feature_count`, populating the same `warning` field when
   `max_distance` is set and excludes at least `max_distance_warning_fraction`
   (new config key, default `0.2`) of sources. Correctly narrower than
   `unmatched_source_count`, exactly as this request asked: a dedicated
   `source_had_computable_distance` flag keeps a source excluded by
   `max_distance` distinct from one that was never going to match
   anything (null/invalid geometry) — the changelog notes this
   distinction was "caught in review before merge" after an earlier
   version blamed `max_distance` too broadly, good evidence of real
   review, not a rushed patch. **Independently executed in this
   session** against the real cloned `v0.5.4` source (stub `geochat_sdk`/
   `geochat_kernel` packages, same technique as `bugs/004`/`006`'s
   verification, extended with one more stub — `geochat_sdk.exceptions.
   SDKDependencyError` — that this file's import chain needed and
   earlier verifications hadn't touched): reproduced all three of
   upstream's own new test cases using their exact fixtures
   (`TARGET_POINT_B`, `SOURCE_POINT`) — 6/10 sources excluded at
   `max_distance=2.0` correctly warns with the exact documented message
   text, 4 null-geometry sources correctly do **not** trigger the
   warning (they were never `max_distance`'s doing), and 1/10 excluded at
   `max_distance=7.5` correctly stays under the 20% threshold and warns
   nothing. All three matched upstream's own assertions exactly.

**Explicitly not landed, per the changelog's own "Not landed" note:** the
systematic generation-time validator that would trace a
`nearest_neighbor` → `filter_attribute`/`sort_limit` chain and warn (or
reject) *before* execution when `max_distance` could make the downstream
threshold unsatisfiable, and the broader audit of every op that
accepts/produces a `target_crs` for the same area-of-use blind spot
(`crs_transform` itself included). Upstream's stated reasoning: both were
flagged as more speculative than the two warnings above, and the
cheap metadata-level warning already makes the pattern visible in tool
output. **Practical consequence for this study**: the `max_distance`
self-defeat pattern can still be *generated* by the LLM at 0.5.4 — it's
now loudly flagged in `output.metadata.warning` after the fact rather
than silent, but nothing stops the plan from being produced in the first
place, since `s3geo.query()` has no multi-turn repair loop that would let
the model see and react to this warning before returning. Worth
reopening this half if the next batch shows the pattern is still common.

**Consequence for this study's pin and next batch:** `requirements.txt`
bumped `==0.5.3` → `==0.5.4` (see its own comment block for the
changelog-sourced detail). Because the *root cause* of the CRS problem
(the `_domain_guidance()` example) is now fixed — not just a warning
added — the next N=20 batch is a genuine test of whether the CRS finding
is actually resolved, not just made visible: run the **canonical,
unmitigated** `§5` batch first at this pin before touching `§7`'s
`system_hints` mitigation, since a clean pass with no local mitigation
at all would mean the upstream fix alone closed the gap the `system_hints`
CRS hint was written for. The `max_distance` hint in `§7` still carries
its own weight regardless, since only a warning — not a preventive
validator — landed for that half.
