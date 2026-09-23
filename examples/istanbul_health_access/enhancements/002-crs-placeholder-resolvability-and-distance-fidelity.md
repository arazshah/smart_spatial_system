# 002 — Data-derived CRS choice, CRS resolvability check, distance-fidelity warning

- **Status:** implemented in `smart-spatial-system==0.5.5` (all four items)
- **Proposed:** 2026-09-23, in response to the first N=20 batches run at `smart-spatial-system==0.5.4` (both the canonical unmitigated batch and the `system_hints`-mitigated comparison batch, `notebooks/03_llm_arm.ipynb` §5 and §7)
- **Motivating finding(s):** `paper/PLAN.md` "Findings", the 0.5.4 batch entry
- **Request:** the prompt below, ready to paste into the session with write access to `smart_spatial_system`
- **Resolution:** see below — landed same day as `v0.5.5`; confirmed against a fresh clone and the real diff, with every part that doesn't need `pyproj` executed

---

## Why this isn't a `bugs/` report

Same posture as `enhancements/001`: nothing here is the framework
misbehaving against its own documentation. `crs_transform` correctly and
loudly rejects an unresolvable CRS string; the 0.5.4 area-of-use warning
correctly stays silent for a CRS whose area of use does cover the data.
The gaps are that a realistic, non-expert prompt still produces a plan
the framework has to reject one node into execution, or a plan that
passes every current check while being measurably wrong.

## Checked before writing this (so the ask is grounded, not guessed)

- `s3geo/__init__.py::query()` at `v0.5.4` converts every input layer to
  GeoJSON (`initial_inputs`) **before** calling
  `LLMQuerySpecGenerator.generate()`, but passes only `raw_query`,
  `context` and `system_hints` into it — the data's actual location
  never reaches the prompt. The model only knows where the data is from
  whatever the user's own words say. That's what makes a deterministic,
  data-derived fix possible (item 1 below): the framework already holds
  the information the model is being asked to guess.
- `pyproj` is an optional dependency in 0.5.4's own new code (lazy
  import, silent no-op on `ImportError`), so everything below keeps that
  same posture.

## Why this prompt deliberately contains no example CRS codes

Two reasons, both learned from this study's own data. (1) 0.5.4's root
cause was exactly this: a literal example code (`EPSG:31256`) in
`_domain_guidance()` got copied verbatim into every plan for a city it
didn't belong to. Suggesting upstream put *any* region-specific code
into a generic prompt or error message would recreate that bug for every
other city. (2) An Istanbul-specific code in the framework's own prompt
would silently leak this study's correct answer into its unmitigated
arm, making that arm no longer a test of a naive prompt. The prompt
below asks for codes to be *computed from the data*, never listed.

## The ready-to-paste prompt

```
Hi - first real batches since 0.5.4 are in. I ran two N=20 batches at
0.5.4: the canonical one (no system_hints) and a comparison batch where
my repo passes system_hints stating the correct projected CRS for the
data plus a note about max_distance. Short version: 0.5.4's warnings
work exactly as documented, but a plain prompt still doesn't reliably get
a valid answer, and the reason points at something the framework can
fix deterministically rather than by prompting harder.

Numbers:
- No system_hints: 8/20 executed successfully (was 18/20 at 0.5.3).
  12/20 failed at execution with "Invalid CRS transformation EPSG:4326 ->
  <PROJECTED_CRS>" (9 runs) or "-> EPSG:XXXX" (3 runs) - the model
  copying, or faking, the placeholder 0.5.4 put into _domain_guidance()
  instead of choosing a real code. All 8 that ran chose EPSG:3857.
- With system_hints: 20/20 executed, 20/20 used the correct UTM zone,
  18/20 returned the identical result. So the planner does fine once it
  is told the right CRS - it just can't work it out from the prompt.

## 1. (Main ask) Derive a suitable projected CRS from the input data itself

Root cause, from your v0.5.4 source: s3geo.query() builds initial_inputs
from the layers BEFORE calling LLMQuerySpecGenerator.generate(), but
generate() only receives raw_query/context/system_hints. The data's
actual extent never reaches the prompt, so the model is guessing a CRS
for a location it can only infer from the user's wording. That's why
removing the literal EPSG:31256 example didn't produce correct choices -
it removed the wrong answer without giving the model any way to find the
right one.

Requested fix:
- In query() (or wherever you think it belongs), compute the combined
  WGS84 extent of the input layers from initial_inputs, and from its
  centroid derive a suitable projected CRS deterministically - the
  standard UTM rule is enough as a default: zone =
  floor((lon + 180) / 6) + 1, EPSG = 32600 + zone (northern hemisphere)
  or 32700 + zone (southern). Pass it to the planner as a stated fact,
  e.g. "Input data extent: <bbox>; suitable projected CRS for metric
  work on this data: EPSG:<computed>". Computed per query from the
  query's own data, so there is no fixed example for a model to copy.
- Edge cases worth a line each: data spanning more than one UTM zone
  (warn, or fall back to something appropriate for wide extents); input
  not in EPSG:4326 (reproject the extent first, or skip the suggestion
  if the CRS is unknown); pyproj missing (skip silently, same as your
  other 0.5.4 checks).
- Please don't put any real CRS code into generic prompt text or error
  messages as an example. 0.5.4's own root cause was exactly that kind of
  example being copied verbatim for a city it didn't belong to. A value
  computed from the current data avoids that entirely.

This is the fix I care about most: it makes the correct CRS a property
of the framework, not of how well a user phrases their question.

## 2. Generation-time check that every target_crs actually resolves

Root cause: nothing at generation time checks target_crs values, so
"<PROJECTED_CRS>" / "EPSG:XXXX" only fail one node into DAG execution
with a raw PROJ error. crs_transform rejecting them is correct - I'm only
asking for the failure to happen at the same stage as your other
structural checks (_validate_structured_param_shapes,
_validate_distance_op_crs_symmetry, etc.).

Requested fix:
- A generation-time validator that resolves each crs_transform
  target_crs (and distance-op source_crs/target_crs) with
  pyproj.CRS.from_user_input(), raising LLMSpecGenerationError on
  failure with a corrective message. If item 1 lands, the message can
  name the CRS computed from the data; if not, give the UTM formula
  above rather than an example code. Same optional-pyproj posture as
  0.5.4.

## 3. Distance-fidelity warning in addition to area-of-use coverage

Symptom: all 8 no-hints runs that executed chose EPSG:3857 (Web
Mercator). Its published area of use is essentially worldwide
(-85.06 to 85.06 latitude), so _crs_area_of_use_warning() correctly stays
silent - but Web Mercator's scale factor at Istanbul's ~41N is about
1/cos(41 deg) = 1.325, so every distance comes out ~32.5% too long with
no warning anywhere. The one run that returned a non-empty result
reported 56 "underserved" areas at 2013.6-4800.1 m, which is roughly
1520-3620 m of real ground distance. "The CRS covers this location" and
"the CRS measures distance accurately at this location" are different
properties, and only the first is checked today.

Requested fix:
- Add a check next to the area-of-use one in find_nearest_neighbors,
  appending to the same `warning` field: evaluate the CRS's scale factor
  at the data's recovered centroid (pyproj.Proj(crs).get_factors(lon,
  lat) exposes this) and warn when it's more than some tolerance from 1.0
  - you know better than I do where to set it; 5-10% would flag Web
  Mercator here without flagging a sensible UTM zone. Optional pyproj,
  never raises, same as the rest.
- If item 1 lands, this becomes mainly a safety net for callers who
  override the suggested CRS.

## 4. Revisiting the "not landed" max_distance/filter validator from enhancements/001

With system_hints, 2/20 runs set nearest_neighbor max_distance=1000.0,
which excluded 166 of 964 sources (17.2%, so under the 0.2 default for
max_distance_warning_fraction) and the filter step then returned 0 of
the remaining 798, with no warning. The other 18 runs, same prompt and
same CRS, each matched exactly 166 areas, all with nearest distance above
1000 m (minimum 1010.0). Distances in the same CRS are deterministic, so
those 166 are the same features max_distance=1000 dropped in the two
failing runs. (My run records don't include the plan JSON, so I can't
quote the failing runs' where clause. The 0 result from 798 remaining
features, all at 1000 m or less, fits a threshold of about 1000 m.)

A fraction threshold can't catch this, and changing the threshold won't
help. When max_distance applies to the same distance field a downstream
filter tests with "greater than threshold", and max_distance <=
threshold, the excluded features are always exactly the ones the filter
would have kept, whether that's 1% or 90% of the data. You can only see
that by looking at the downstream op, which is what the systematic check
you deferred in 0.5.4 would do. Given this concrete repro, would you
reconsider it? If it's more than it's worth, I'm fine keeping the
fraction-based warning as a partial mitigation. I just wanted you to
decide with this example in hand.

## What I need back

Same as always: confirm via CHANGELOG.md what landed, and what didn't and
why, so I can independently verify against the real diff before bumping
my pin. Tag the release as usual. Nothing on my side is blocked - the
system_hints mitigation covers items 1-3 for my study in the meantime.
```

## Resolution

`smart-spatial-system==0.5.5` (2026-09-23), `CHANGELOG.md [0.5.5]`. All
four items landed. Confirmed by cloning
`github.com/arazshah/smart_spatial_system` fresh, checking out tag
`v0.5.5`, and reading the actual `git diff v0.5.4 v0.5.5`. The changelog
was not taken as evidence on its own. `pyproj` still can't be installed in
this session's sandbox (pip and apt both return 403), so everything that
needs it was verified by reading the diff and upstream's own new tests.
Everything that doesn't need it was executed against the cloned source.
The orchestrator package `__init__` was bypassed by registering
`orchestrator` / `orchestrator.planning` as bare namespace packages, so
the real submodules load without the heavy import chain.

1. **Data-derived CRS: landed as asked, and verified on this study's own
   data.** New `orchestrator/planning/input_data_extent.py`.
   `s3geo.query()` calls `derive_input_data_extent(layers)` before
   planning and renders an "Input data facts" section into the system
   prompt, placed before `system_hints`. The section gives each layer's
   CRS, the EPSG:4326 extent and centroid, and "Suitable projected CRS
   ... Use exactly this value".
   - Executed with the real module's pyproj-free functions on this repo's
     `data/raw/` (staged from the author's machine: 1020 hospitals, 964
     mahalle). Combined bbox is lon 27.9708-29.9588, lat 40.8027-41.5833,
     centroid (28.9648, 41.1930). Both edges fall in UTM zone 35, and
     `_candidate_crs()` returns `EPSG:32635`, which is exactly Arm 1's CRS.
   - An independent analytic check (transverse Mercator
     k = k0(1 + (Δλ cos φ)²/2), same 5×5 grid) gives a maximum scale
     error of about 0.036% against the module's 1% `MAX_SCALE_ERROR`. So
     on the author's machine, where `pyproj` is installed, the suggestion
     will be made with no caveat notes. Not executed end to end here:
     `derive_input_data_extent()` returns `None` without `pyproj`, as
     documented.
   - Also executed: the data-independent system prompt contains no EPSG
     code other than `EPSG:4326`, and it points to the facts section.
   - The `<PROJECTED_CRS>` placeholder paragraph is still in
     `_domain_guidance()`, now pointing at the facts section. So whenever
     no extent can be derived (pyproj missing, unknown layer CRS), the
     0.5.4 failure mode can come back. Item 2 now catches it at
     generation.
   - `S3GeoResult.input_data_extent` reports what was computed.
     `notebooks/03_llm_arm.ipynb` now records it on every run and checks
     it before any LLM call (§3b).
2. **CRS resolvability at generation time: landed.**
   `_validate_crs_params_resolve()` runs in `generate()` next to the
   other structural validators. It checks each value after
   `crs_transform`'s own normalization (upper-case, spaces stripped), so a
   PROJ string that would break under that normalization is rejected too.
   The corrective message names the CRS computed from the data, or gives
   the UTM formula, never an example code. Upstream's tests cover
   `<PROJECTED_CRS>`, `EPSG:XXXX` and `EPSG:999999`, and a regex test
   asserts that the no-extent message contains no `EPSG:<digits>`. Not
   executed here, because it needs `pyproj` and skips silently without it.
3. **Distance-fidelity warning: landed.**
   `_crs_scale_distortion_warning()` in `nearest_neighbor.py` uses
   `pyproj.Proj.get_factors` at the recovered centroid, taking the worse
   Tissot axis. The default tolerance is 2%, tighter than the 5-10% I
   suggested. Upstream's reasoning holds: UTM stays under 2% until about
   11° from its central meridian. For EPSG:3857 at Istanbul the warning
   quotes 1.325 / ~32.5%, matching this study's own calculation. It is
   appended to the same `warning` field, with an opt-out key. Not
   executed here (needs `pyproj`).
4. **max_distance vs. downstream filter: landed at generation time**,
   the item deferred in 0.5.4. `_validate_max_distance_filter_composition()`.
   **Executed here through the real `LLMQuerySpecGenerator.generate()`**
   with `StaticLLMClient` and plans shaped like this study's, using the
   real `distance_field` name `distance_to_nearest_hospital`.
   - Rejected, as it should: the mitigated run_08/19 repro
     (`max_distance=1000`, keep `gt 1000`); the 0.5.3-style
     `max_distance=5000`, keep `gt 5000`; the shortcut `where` form; the
     condition under `and`; the `max_distance_m` alias with
     `between [1500, …]`; and the default `_nearest_distance` field.
   - Accepted, as it should, because each can return features:
     `max_distance=5000` with keep `gt 1000`; no `max_distance` at all
     (the 18/20 good plan); `gte 1000` exactly at the cap; the condition
     under `or`; and a threshold on a different field. The rejection
     message names both ops and says to remove `max_distance`.
   - Cross-checked `OP_CATALOG`'s `_NEAREST_PARAM_MAP`: `distance_field`
     is a real LLM-level param mapped to the function kwarg, so the
     validator reads the key plans actually use. The filter plugin's
     `VALID_OPERATORS` accepts only canonical names (`gt`, `gte`, `eq`,
     `between`, …), which is what the validator handles.

**What this means for the next batch.** Read these before reading the
next manifest:

- There is still no retry anywhere. Grepped `s3geo/__init__.py` and
  `llm_spec_generator.py` for any retry or repair loop and found none.
  So a plan rejected by item 2 or item 4 is still a failed run. It now
  fails at `error_stage="generation"` with a clear message, where before
  it failed one node into execution (item 2's case) or "succeeded" with
  zero features (item 4's case).
- Some self-defeating runs that the 0.5.4 manifest counted as successes
  will therefore show up as failures. That is the manifest becoming more
  truthful, not a regression.
- For the canonical §5 batch, the planner now receives the correct CRS
  from the framework itself, computed from the data. It no longer comes
  from this study's `system_hints`. That is the test this whole thread
  was aiming at.

**Not landed / out of scope, per the changelog's own notes:**

- `OrchestratorService`'s `/query` path doesn't call
  `derive_input_data_extent()` yet. It is irrelevant to this study, which
  uses `s3geo.query()` only.
- UTM's Norway/Svalbard zone exceptions are not handled. Also irrelevant
  here.

