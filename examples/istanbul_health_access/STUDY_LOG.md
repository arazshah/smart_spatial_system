# STUDY_LOG.md

> **Provenance.** This is the study's working log and conventions file, copied
> from `CLAUDE.md` in the original study repository,
> [`arazshah/smart-spatial-istanbul-health-access`](https://github.com/arazshah/smart-spatial-istanbul-health-access),
> and renamed so it is not picked up as agent instructions inside
> `smart_spatial_system` itself. "This repo" below means the study repository,
> and the rule against editing `smart_spatial_system` applies to the study, not
> to this repository. Content is unchanged otherwise.

## What this is

A spatial-accessibility case study asking **which Istanbul neighbourhoods
(*mahalle*) are underserved by hospitals and clinics** — where "underserved"
means the nearest hospital/clinic is farther than a walking/driving threshold
(2000 m by default). The same question is answered twice, by two pipelines:

1. a **rule-based** arm, where the operation plan is written explicitly
   (`nearest_neighbor` / `zonal_statistics`, threshold, dissolve), and
2. an **LLM-planned** arm, where the same question is handed to
   `s3geo.query()` / `LLMQuerySpecGenerator` as natural language and the
   planner decides the operations and parameters itself,

and the two are then compared. Method, metric and phases:
[`paper/PLAN.md`](paper/PLAN.md) and
[`paper/comparison_metric.md`](paper/comparison_metric.md).

It is a sibling of
[`smart-spatial-vienna-accessibility`](https://github.com/arazshah/smart-spatial-vienna-accessibility)
and deliberately reuses that repo's structure, comparison metric and
working style — a second city, a second domain (health access rather than
general amenity access), and a newer pin of the same framework.

## THE RULE: `smart_spatial_system` is a pinned dependency

**This repo consumes `smart_spatial_system` as a pinned dependency
(exact version in `requirements.txt` — currently `0.5.6`). Never edit its
source. If a bug is found in it, write a structured bug report in `bugs/`
instead, following the exact format used in `smart_spatial_system`'s own
`CHANGELOG.md` bug entries — root cause, reproduction, and a proposed
two-part fix (plugin layer + LLM-prompt layer).**

This is not a style preference, it is what makes the case study mean
anything. The experiment measures how a *released, pinned* version of the
framework behaves. A local patch to a vendored copy would make the numbers
unreproducible for anyone who installs the same pin, and would quietly
convert "the framework has this failure mode" into "our fork doesn't."

Concretely, in this repository:

- Never edit anything under the installed `smart_spatial_system` /
  `geochat_sdk` package tree, never vendor a copy of it here, and never
  monkey-patch its internals at runtime to make an experiment pass.
- Reading its source to understand behaviour is not only allowed, it is
  encouraged — most of the Vienna repo's real findings came from reading
  `plugins/*.py` and `orchestrator/planning/*.py` directly. Read freely,
  write never.
- A bug found while running an experiment here is a **finding**, and the
  deliverable for it is a report in `bugs/` (see `bugs/README.md` for the
  required format and file naming). Write the report, then **stop and
  surface it** — do not silently work around it and carry on.
- Prompt-side mitigations (extra `system_hints` in this repo's own
  notebooks) are allowed, but only as a *named, documented* safety net
  recorded in the bug report's "plugin layer + LLM-prompt layer" fix
  proposal — never as an undocumented fudge that hides the failure.
- Bumping the pin is a deliberate act: change `requirements.txt`, say in
  the commit message which upstream release it moves to and which bug
  report it closes, and re-run every phase whose outputs could change.

## Two more things this repository is NOT

- **Not a place for invented or synthetic data as final results.** The
  point is real, citable OpenStreetMap data (see `data/README.md`) and, for
  the LLM arm, a real model called for real, N times. Synthetic data is fine
  *only* for smoke-testing a code path before the real run, and must be
  labelled as such wherever it appears.
- **Not a place to quietly skip a blocked phase.** If the session's network
  policy or a missing key blocks a phase, say so explicitly and stop (see
  "Network and secrets" below). A phase that silently produces nothing is
  worse than one that fails loudly.

## Current state

**Phases 0–2 are done (2026-09-19).** `data/raw/` has 1020 hospitals/clinics
(421 hospital, 599 clinic) and 964 mahalle boundaries (955 Polygon, 9
MultiPolygon) — see `data/README.md` "Fetched" for why the hospital count
differs from an earlier same-day fetch (OSM is live, not a bug). Phase 2
(`notebooks/01_data_and_problem.ipynb`) ran clean end to end on the paper
author's own machine — every sanity check passed (0 null/invalid
geometries, 0 unnamed mahalle, CRS asserted after every reprojection) — and
wrote `data/processed/hospitals.geojson` (1020 features) and
`data/processed/mahalle.geojson` (964 features), both `EPSG:32635`. See
`paper/PLAN.md`'s "Findings" for the full real output, including the 9
mahalle whose centroid falls outside their own polygon (island/irregular
shapes — named there, not a bug).

**Phase 3 is done (2026-09-19).** `notebooks/02_rule_based_arm.ipynb`
revision 3 ran clean end to end on the author's own machine, no errors
across all 11 code cells — see `paper/PLAN.md` "Findings" for full numbers:
207 mahalle underserved at the 2000 m headline threshold (325 at 1000 m,
251 at 1500 m), dissolving into 6 contiguous regions; 1020/1020 hospitals
matched to a mahalle via `spatial_join_features`; the CRS-mismatch safety
net on `find_nearest_neighbors` (Vienna's candidate Bug 5) **confirmed
present and correct at 0.3.0 by an actual triggered run**, not just
source-reading. `results/rule_based_underserved.csv`,
`results/underserved_regions.geojson`, `results/rule_based_report.json`
are written and current.

Two bugs surfaced and got fixed along the way, worth remembering both kinds
exist:

1. **Upstream**: `bugs/001-vectorout-from-geopandas-timestamp-crash.md` —
   `VectorOut.from_geopandas()` crashing on OSM `check_date`/`start_date`
   columns. Documented, mitigated in `gdf_to_vectorout()` (never edited the
   pinned source), confirmed fixed by the clean revision-2 run. **Fixed
   upstream in `geochat-sdk` 1.0.1** (2026-09-19) — pin bumped, see below.
   `gdf_to_vectorout()`'s cast is now a harmless no-op, not required.
2. **Our own code, not upstream**: revision 2's `centroid_outside_polygon`
   column matched mahalle names against a set of 9 bare names
   (`"Fatih"`, `"Kınalıada"`, ...) copied from this file's own phase-2
   summary, but the real `name` field includes the `Mahallesi` suffix —
   the `.isin()` check silently returned `False` for all 964 rows instead
   of erroring. No `bugs/` report needed (this repo's rule is about
   `smart_spatial_system`, not our own code); fixed in revision 3 by
   recomputing the flag directly with `geometry.contains()`, which the
   revision-3 run confirmed finds **exactly the same 9 mahalle**
   `01_data_and_problem.ipynb` found independently — two different
   computations agreeing is real evidence both are right. Worth
   remembering: a silent-wrong-answer failure mode can just as easily be
   ours as upstream's.

**Phase 4 was blocked on `bugs/002`, now unblocked by an upstream fix
(2026-09-19).** `notebooks/03_llm_arm.ipynb` implements Arm 2
(`s3geo.query()`) and is deliberately structured to stop after a single
smoke-test call before committing to the N=20 batch — see the notebook's
own "## STOP" cell for why. The author ran §1-4 and the smoke test itself
crashed: `s3geo.query()`'s registry build
(`CapabilityRegistry.from_plugin_modules()`, called with no args →
`tolerant=False`) eagerly imports every plugin module including
`plugins/ndvi_analysis.py`, which did an unconditional `import rasterio`
— not installed in this repo's `.venv`, and irrelevant to this 100%
vector query. Full writeup:
`bugs/002-s3geo-query-crashes-without-raster-extras.md`. **Fixed upstream
in `smart_spatial_system` 0.4.1** (2026-09-19, confirmed by cloning the
tag and reading the diff, not taken on the fixer's word): `query()` now
defaults to `tolerant=True`, and `ndvi_analysis.py`'s `rasterio` import
moved to a lazy import inside `process_ndvi()`. **This repo's pin is
bumped**: `requirements.txt` now pins `smart-spatial-system==0.4.1` and
an explicit `geochat-sdk==1.0.1` (was `0.3.0` / implicit `>=1.0.0`) —
closes both `bugs/001` and `bugs/002`. The old `pip install rasterio`
local mitigation is superseded, not required on a fresh install at the
new pin. **Next step: the author reinstalls `requirements.txt` at the
new pin and re-runs the smoke test (§1-4)** before anything else in
Phase 4 proceeds — this has not been empirically confirmed to work yet,
only the upstream fix's source has been read. Verified against 0.3.0
source before writing any code (same discipline as Phase 3), which
surfaced three more facts worth remembering (still true at 0.4.1 — the
`CHANGELOG.md` diff for 0.4.0/0.4.1 touches none of this):

1. `LLMQuerySpecGenerator` already defaults `temperature=0.1` — this
   study's methodology needs no code change to get it.
2. `PlannerConfig.allow_implicit_entities=True` by default means any
   unresolved input ref in the LLM's plan becomes `$inputs.<ref>` — there is
   no formal schema/binding layer, so the raw query's layer names
   ("hospitals", "mahalle") must exactly match the `layers=` dict keys
   passed to `s3geo.query()` or the DAG won't wire correctly.
3. Two generation-time validators already exist in 0.3.0 and are directly
   relevant to this study's own Arm 1 design decisions:
   `_validate_distance_op_crs_symmetry` (Arm 2's own CRS-symmetry guard,
   parallel to Arm 1's `find_nearest_neighbors` runtime check) and
   `_validate_filter_points_in_polygon_usage` (blocks that op when the
   query needs zone identity — its docstring says this was added because
   `gpt-4o-mini` kept making that mistake even with prompt guidance,
   independent corroboration of this repo's own `spatial_join` choice in
   Arm 1). See `paper/PLAN.md` Arm 2 section for the docstring quotes.

`bugs/001` is now fixed upstream too (`geochat-sdk` 1.0.1, pin bumped
alongside `bugs/002`'s fix) — moot either way for Arm 2, since
`s3geo._to_geojson_dict()` never depended on `VectorOut.from_geopandas`
in the first place.

**Re-run at `0.4.1` (2026-09-19): `bugs/002` is gone, a new real bug
showed up on the first actual LLM call.** The smoke test reached the LLM
(71.8s), got a plan back, and failed during execution:
`filter_features() got an unexpected keyword argument 'attribute'`. Root
cause, verified against real 0.4.1 source: `PlannerConfig.strict_params`
defaults to `False` and `s3geo.query()` never overrides it, so an
operation parameter name (`attribute`) not in `filter_attribute`'s real
`param_map` (`where`, not `attribute`) gets passed straight through to
the plugin instead of being rejected at planning time — the exact same
"every test in the framework's own suite uses the safe setting, `s3geo`
alone doesn't" shape as `bugs/002`'s `tolerant` issue. Compounded by a
prompt gap: `_op_input_roles_reference()` auto-generates the *input-role*
part of the LLM's system prompt from `OP_CATALOG` specifically to avoid
undocumented operations (its own docstring names the `distance_to`
incident that motivated it) — but there's no equivalent for *parameters*,
so `filter_attribute`'s real param names were never taught to the model
at all. Full writeup: `bugs/003-planner-silently-passes-through-unknown-op-params.md`.
**Not a Phase 4 blocker** (unlike `bugs/002`): one LLM plan picked a
wrong parameter name on one op, `run_once()` already records this kind
of failure without crashing, and no local mitigation exists anyway
(`s3geo.query()` doesn't expose `strict_params` the way it now exposes
`tolerant`).

**Fixed upstream in `smart_spatial_system` 0.4.2 (2026-09-19)**,
confirmed by cloning the tag and reading the diff directly, not taken on
the fixer's word: `s3geo.query()` now defaults to `strict_params=True`
(also a real parameter now, mirroring `tolerant`), and
`llm_spec_generator.py` gained `_op_param_reference()` — generated from
`OP_CATALOG`'s `param_map` the same way `_op_input_roles_reference()`
already is — wired into the system prompt. Both halves of the two-part
fix this report asked for. **Pin bumped again**: `requirements.txt` now
pins `smart-spatial-system==0.4.2` (was `0.4.1`) — closes `bugs/003`
alongside the already-closed `bugs/001`/`bugs/002`.

**Empirically confirmed at 0.4.2, and a related new gap found
(2026-09-19).** The author re-ran the smoke test after reinstalling at
`0.4.2`: the error changed in *kind*, not just wording —
`"Node underserved_mahalle failed: where must be a dict/object or
None."` — proving `bugs/003`'s fix genuinely works (the LLM now uses the
correct parameter *name*, `where`; the failure moved one level deeper,
to the parameter's *value* shape). New report:
`bugs/004-where-clause-shape-never-taught-to-llm.md` — `where`'s real
shape (`{field, op, value}` conditions, `and`/`or`/`not`, or a shortcut
dict, all documented in `plugins/spatial_query_filter.py::_eval_where`'s
own docstring) is never surfaced to the LLM anywhere;
`_op_param_reference()` only lists parameter *keys*, not *value
shapes*, so any structured parameter (not just `where`) is likely
exposed the same way. Not a Phase 4 blocker, same reasoning as
`bugs/003`. **Next step: proceed to the N=20 batch** — no more waiting
on a fix before that; this class of outcome (a wrong value for an
otherwise-correct parameter) is exactly what it exists to characterize
the rate of.

**Performance fix, pin bumped again to 0.5.0 (2026-09-19).** The N=20
batch's first run took ~120-130s/call; traced to
`plugins/nearest_neighbor.py::find_nearest_neighbors` — a brute-force
O(source×target) nested loop, no spatial index anywhere in the file
(confirmed by grep), ~984,000 pairwise calls for this repo's 964×1020
dataset. Not a correctness bug (no `bugs/` report), so reported directly
upstream instead. **Fixed in `smart_spatial_system` 0.5.0**, confirmed
by cloning the tag and reading the diff: an STRtree-accelerated
candidate search, shapely-only (the `engine="python"` / shapely-
unavailable path is untouched), with a correctness-equivalence
regression test plus a performance benchmark
(`tests/test_nearest_neighbor_strtree_performance.py`) reporting ~33s →
~0.2s (~150x) on a synthetic 1000×1000 case and ~32s → ~0.2s on this
repo's own 964×1020 shape, identical output either way. Pin bumped
`==0.4.2` → `==0.5.0`. Note for later: any `latency_s` recorded in
`results/llm_runs/` from before this bump reflects the old, much slower
implementation — don't compare pre/post-bump latencies without flagging
the version change.

**Phase 4's real N=20 batch ran at 0.5.0 (2026-09-19) — DONE, and the
result is not what a raw "1/20 succeeded" count suggests.** Speed
confirms the 0.5.0 fix empirically: all 20 calls completed in 10-20s each
(was ~120-130s pre-0.5.0). Outcome breakdown, every run classified by its
own `success`/`error_stage`/`error` fields: 15/20 hit `bugs/004` (`where`
must be a dict), 2/20 (`run_03`, `run_11`) hit a **new** bug —
`filter_features() got an unexpected keyword argument 'geometry_type'` —
filed as `bugs/005-filter-attribute-geometry-type-param-map-typo.md`
(`op_catalog.py`'s own `param_map` maps `geometry_type` to a target name
that doesn't exist on the real function, which is `geometry_types`
plural; unlike every other Phase 4 bug, this is **not an LLM mistake** —
the LLM used exactly the name the framework's own `_op_param_reference()`
advertised), 2/20 (`run_02`, `run_12`) hit `rules[0].target is required.`
on `enrich_feature_properties` — the same class of gap as `bugs/004`
(structured param value shape never taught), folded into that report as
corroboration rather than filed separately.

The 1/20 nominal success (`run_06`) **is not a real answer to this
study's question**, on two independent grounds, worth remembering as the
single most important Phase 4 finding so far: (1) its plan reprojected
both layers to **`EPSG:31256`**, which is **"MGI / Austria GK East"** —
confirmed via web search, an Austrian CRS, geographically meaningless for
İstanbul — and the framework's `_validate_distance_op_crs_symmetry`
validator passed anyway, because it only checks both layers share the
*same* target CRS, never that the CRS is geographically appropriate; a
silent-wrong-answer failure mode, not a crash. (2) its plan has only 3
operations (two `crs_transform` + `spatial_nearest`) and stops — no
filter/threshold step, so it never actually determines which mahalle are
underserved; it answers a strict subset of the question and calls it
done. **Net: Arm 2's real full-and-correct success rate over this batch
is 0/20, not 1/20.** Full breakdown and the practical implication for
`04_comparison_metric.ipynb` (there's no valid Arm-2 underserved-mahalle
list to diff against Arm 1 yet at this pin): `paper/PLAN.md` "Findings",
Phase 4 real-batch entry.

**`bugs/005` fixed upstream, pin bumped to 0.5.1 (2026-09-19).** Confirmed
by cloning tag `v0.5.1` and reading the actual diff, not taken on the
fixer's word: `op_catalog.py`'s `filter_attribute.param_map` entry
corrected (`"geometry_type": "geometry_type"` → `"geometry_type":
"geometry_types"`), plus the systematic fix this report asked for —
`tests/test_op_catalog_param_map_signatures.py`, an `inspect.signature()`
check that every `OP_CATALOG` param_map target is a real keyword on its
bound capability, across the whole catalog at once. Running that new
test turned up and fixed four more instances of the identical drift
(dead params on `query_database`/`load_postgis_layer`, `inspect_vector`,
`summarize_vector`, `display_vector` — see `CHANGELOG.md [0.5.1]` and
`bugs/005`'s "Resolution" for the full list), none of which this study's
own runs had hit yet but all of which were silently broken the same way.
Pin bumped `==0.5.0` → `==0.5.1`. `bugs/004` is unaffected, still open —
still the dominant real failure mode (15/20 in the batch above) and the
next thing worth a fix before re-running the N=20 batch.

**N=20 batch re-run at 0.5.1 (2026-09-20/21) — `bugs/005` confirmed
fixed in practice (0 geometry_type failures, was 2/20), but the batch
still went to 0/20 successes, and surfaced a new bug, `bugs/006`.**
12/20 still hit `bugs/004` (`where must be a dict`); the other 8/20 plus
the smoke test hit a **new** error never seen in the 0.5.0 batch:
`Node underserved_mahalle failed: sort_order must be a non-empty
string.` Filed as
`bugs/006-filter-attribute-sort-order-none-not-defaulted.md`. Root
cause, read directly from `plugins/spatial_query_filter.py`:
`filter_features`'s `sort_order` has a real Python default (`"asc"`),
but neither `planner.py::_map_params` nor
`dag_executor.py::_build_kwargs` filters `None`-valued params before
the plugin call — so a plan that includes `"sort_order": null` (almost
certainly what's happening, same "the LLM includes every advertised key,
using null for the ones it doesn't want" pattern as `bugs/004`) passes
an explicit `None` straight through, which **overrides** the Python
default instead of triggering it, and `_validate_sort_order` rejects
`None` immediately. The clearest evidence this is a real framework bug,
not intended strictness: the sibling parameter `bbox_mode` has the exact
same shape and *does* tolerate an explicit `None`, via a `pick_first()`
fallback already present in the same function — `sort_order` just skips
that guard. Also affects `sort_limit`, a second `OP_CATALOG` op bound to
the same capability. **Not a 0.5.1 regression** (nothing in the 0.5.1
diff touches `sort_order`) — it's a pre-existing bug this run's sample
of LLM plans happened to trigger where the 0.5.0 batch's sample didn't;
worth remembering that a single N=20 run is a sample, not the full
error-rate distribution. Direct plugin-level repro (mirroring
`bugs/003`/`005`'s style) **executed for real** against the cloned
`v0.5.1` source (`geochat_sdk` stubbed with no-op decorators purely to
satisfy its two import-time dependencies, not touched by the failing
code path) — confirmed empirically, not just derived from reading
source: omitting `sort_order` works, explicit `sort_order=None` raises
`ValueError: sort_order must be a non-empty string.`, and the sibling
`bbox_mode=None` correctly falls back to its default instead of raising.
Full writeup, proposed two-part fix, and the broader systematic-check
recommendation: `bugs/006-filter-attribute-sort-order-none-not-defaulted.md`.

**`bugs/006` fixed upstream, pin bumped to 0.5.2 (2026-09-23).** Confirmed
by cloning tag `v0.5.2` and reading both real commits' diffs, plus
re-executing this report's own repro against the fixed source (not taken
on the fixer's word): `filter_features(sort_order=None)` now returns
cleanly instead of raising. Fixed twice over, exactly matching what was
asked: (1) `filter_features`'s `sort_order` now resolves via
`pick_first(sort_order, default="asc")`, the same pattern `bbox_mode`
already used; (2) `dag_executor.py::_build_kwargs` now drops any
`None`-valued static plan param whose target keyword has a non-`None`
default *and* an annotation that doesn't itself accept `None` — a
systematic fix at the one place every plan's params flow through,
closing this class of bug catalog-wide rather than one parameter at a
time. That audit found several more real instances beyond `sort_order`,
some *worse* (silently wrong instead of loud): `rank_features.descending`,
`filter_points_in_polygon.drop_outside`/`predicate`,
`enrich_feature_properties.skip_missing`, `render_pdf.save_to_disk`/
`template_name`, `join_feature_properties.unmatched`, and several
field-name params — all fixed in the same change. A same-release
follow-up commit (`922383a`) caught and fixed an overreach in the first
pass, which would have silently broken
`calculate_attribute_statistics(precision=None)` (`None` there
legitimately means "don't round") — good evidence of real self-review,
not just a quick patch. Pin bumped `==0.5.1` → `==0.5.2`. `bugs/004`
(`where`-clause shape) is unaffected, still open — expected to be the
dominant failure mode in the next N=20 re-run, which hasn't happened
yet at this pin.

**`bugs/004` fixed upstream, pin bumped to 0.5.3 (2026-09-23) — all
three Phase 4 bugs found in this study are now fixed upstream.**
Confirmed by cloning tag `v0.5.3` and reading the actual diff, plus
independently executing every `where`/`rules` example the fix adds
against the real `filter_features()`/`enrich_feature_properties()`
plugin functions in this session (not taken on the fixer's word — all
matched/enriched correctly). Fixed both ways this report asked for: (1)
new `orchestrator/planning/op_param_shapes.py`, a maintained table with
a description and real JSON examples for *every* structured
`OP_CATALOG` param (not just `where`/`rules`), rendered into the system
prompt as a new "Structured param VALUES" section; a new test not only
checks every structured param has an entry but **runs each example
through the real plugins**, so a wrong example fails the test suite
instead of teaching the model something false; (2) a new
generation-time validator, `_validate_structured_param_shapes()`,
rejects a mis-shaped `where`/`rules` with a clear
`LLMSpecGenerationError` instead of a runtime failure after a full DAG
build. Upstream is explicit this was **not spot-checked against a live
LLM** (no endpoint available when 0.5.3 was cut) — their tests confirm
the guidance is present and its examples work against the real plugins,
not how often a model now actually produces a valid value in practice.
Pin bumped `==0.5.2` → `==0.5.3`. **Next step: run a fresh N=20 batch at
0.5.3** — this is the first real chance at a clean Arm 2 run, and the
actual measure of whether the model's success rate improved. Even a
clean run wouldn't fully close Phase 4 though: the earlier 0.5.0 batch's
one nominal "success" also had planning-choice problems unrelated to
these three bugs (wrong CRS reprojected to Austria's `EPSG:31256`, and
no filter/threshold step at all) — worth checking for in the next
batch's output, not just whether it executes without error.

**N=20 batch re-run at 0.5.3 (2026-09-23) — DONE. Structural success
jumps from 0/20 to 18/20; the two planning-choice problems flagged above
are exactly what shows up, and the research question is still answered
0/20 times.** All three Phase 4 bugs are confirmed fixed in bulk: 18/20
runs complete execution (was 0/20 at 0.5.1/0.5.2), latencies 10.9-15.3s
(0.5.0 perf fix still holding), and **every** successful run now
includes a `filter_attribute` step (previously only the one 0.5.0
"success" existed and it never filtered). The 2/20 failures
(`run_01`/`run_02`) are `LLM HTTP error 401` from the external AvalAI
provider — a key/billing problem, not a framework bug; no `bugs/`
report, flag it to the author before the next batch. Both open,
non-`bugs/` concerns predicted above are confirmed present in 100% of
runs: (1) all 18 successful runs reproject to `EPSG:31256` again — the
model's unanimous, still-wrong CRS choice across every batch at every
pin so far, and no fixed bug touches it (`_validate_distance_op_crs_symmetry`
only checks CRS *symmetry*, never geographic appropriateness); (2) a
newly-characterized **self-defeating `max_distance` + `where`
combination**: 7 runs (an 8th, `run_13`, strongly implied but not
directly readable from its output JSON — see `paper/PLAN.md` Findings
for why) set `nearest_neighbor.max_distance=5000.0`, which silently
zeroes out the filter step's result every time; the other 10 leave it
uncapped and produce real (128-166 mahalle) but still CRS-invalid
results. Both are paper Findings, not `bugs/` reports — every parameter
involved works exactly as documented; the gap is LLM planning-choice
quality composing two individually-correct parameters into a plan that
can never produce a match, which is a materially different, more
interesting finding for the paper than "the framework crashes." **Net,
stated plainly for the paper's limitations**: fixing every reproducible
framework bug this study found did not by itself produce a single fully
valid Arm-2 answer — real structural progress (0/20 → 18/20 structural
completions) with the actual research-question success rate still 0/20.
Full breakdown, the per-run table, and the exact match counts:
`paper/PLAN.md` "Findings," 0.5.3 batch entry. **Decided (2026-09-23):
both an upstream enhancement request and a local mitigation, in
parallel** — the author chose "هر دو" when offered the options. Both are
now in place:

1. **Upstream enhancement request** — `enhancements/001-crs-appropriateness-
   and-max-distance-where-composition-warnings.md` (new directory,
   `enhancements/README.md` explains why this isn't a `bugs/` report:
   both findings are LLM planning choices, not framework defects — every
   parameter behaves exactly as documented). The ready-to-paste prompt
   asks for two *opt-in warnings*, not behavior changes: (a) extend
   `nearest_neighbor.py`'s existing `warn_if_geographic_crs` mechanism —
   which already populates a `warning` metadata field for "still in
   degrees" — to also fire when the chosen *projected* CRS's own
   `area_of_use` doesn't contain the input data's actual coordinates (no
   hardcoded region list, just `pyproj.CRS(...).area_of_use`, a real
   property of every registered CRS); (b) surface the `unmatched_source_
   count`/`dropped_unmatched_count` fields nearest_neighbor already
   computes into that same `warning` field when `max_distance` discards a
   non-trivial fraction of sources, so the self-defeating pattern is
   visible instead of silent. **Status: proposed, not yet sent** — next
   step is handing it to the session with write access, same as every
   `bugs/` prompt before it.
2. **Local mitigation** — `notebooks/03_llm_arm.ipynb` §7-8 (new
   sections, appended after the canonical §5/§6 batch, which is
   untouched): `run_once()` now accepts an optional `system_hints`
   string, passed straight through to `s3geo.query()` (confirmed in the
   cloned `v0.5.3` source: `llm_spec_generator.py:1289-1297` appends it
   verbatim to the system prompt — the same mechanism
   `examples/urmia_real_estate_ranking.py` uses for domain grounding). A
   new `SYSTEM_HINTS` constant states two general, non-answer-revealing
   facts: the correct projected CRS for this region (`EPSG:32635`) and a
   general DAG-composition principle about not letting an upstream
   `max_distance` cap silently defeat a downstream distance filter. A
   second N=20 batch runs with this hint and writes to
   `results/llm_runs_mitigated/` — **kept alongside, not instead of**,
   `results/llm_runs/`, so the paper can report the gap between
   unmitigated and mitigated as its own finding. Caveat carried over from
   `bugs/004`'s "Local mitigation" section: a hand-written hint like this
   can go stale the moment upstream's own behavior changes (including if
   `enhancements/001` above gets implemented) — re-check it on every
   future pin bump. **Not yet run** — needs the author's own machine
   (real LLM calls) — this notebook has only been edited, not executed,
   in this session.

**`enhancements/001` landed same-day, pin bumped to 0.5.4 (2026-09-23) —
more than requested, including the actual root cause of the CRS problem.**
Confirmed by cloning tag `v0.5.4` fresh and reading the real diff, plus
independently executing the `max_distance`-exclusion half against the
real cloned source (all three of upstream's own new test cases,
reproduced exactly with their own fixtures — not taken on the changelog's
word). Both requested warnings shipped in `nearest_neighbor.py`, wired
into the same `warning` metadata field: `_crs_area_of_use_warning()`
(needs `pyproj` — this session's sandbox still can't install it, PyPI
still blocked, so this half was verified by diff-reading and upstream's
own new test only, not executed here) and
`_max_distance_exclusion_warning()` (executed here for real, matched
upstream's assertions exactly). **The bigger find**: upstream traced *why*
every single run in every batch of this study picked `EPSG:31256` and
fixed it at the source — `_domain_guidance()` in `llm_spec_generator.py`
used that exact CRS as its literal worked example for the
`crs_transform` → distance-op pattern, generic placeholders and all, with
nothing telling a model handed different-city data not to copy it
verbatim. Replaced with a `<PROJECTED_CRS>` placeholder plus an explicit
instruction never to reuse a CRS code from the prompt. **Explicitly not
landed**: the systematic generation-time validator that would catch a
self-defeating `max_distance`+`where` combination *before* execution —
only the after-the-fact warning landed for that half, so the LLM can
still generate the pattern, just no longer silently. Full detail:
`enhancements/001`'s "Resolution" section; pin bump detail:
`requirements.txt`'s `0.5.4` comment block.

**Next step, in this order**: because this fixes the actual root cause
(not just a warning), run the **canonical §5 batch first, unmitigated, no
`system_hints`** — a clean CRS result with no local mitigation at all
would mean the upstream fix alone closed that half of the gap, which is
the real measure of whether `enhancements/001` worked. Only then run
§7's mitigated batch, whose `max_distance` hint still carries its own
weight (no preventive validator landed for that half) but whose CRS hint
may now be partly redundant — harmless to leave in, worth noting in the
paper either way. Neither batch has been run yet at this pin.

**Both §5 and §7 run at 0.5.4 (2026-09-23) — `system_hints` is now doing
the real work; the upstream fix alone isn't enough yet.** Full detail:
`paper/PLAN.md` "Findings", 0.5.4 batch entry.

- **§5 (unmitigated): 8/20 structural success, down from 18/20 at 0.5.3.**
  New failure mode: 12/20 fail at execution with `"Invalid CRS
  transformation EPSG:4326 -> <PROJECTED_CRS>"` (9) or `"-> EPSG:XXXX"`
  (3) — the model copying/faking the `<PROJECTED_CRS>` placeholder
  `enhancements/001`'s fix introduced, instead of substituting a real
  code. `crs_transform` rejects it correctly and loudly — not a
  framework bug, a new prompt-quality gap. Of the 8 that did succeed,
  **100% chose `EPSG:3857`** (Web Mercator) — passes the new
  area-of-use warning (near-worldwide coverage) but distorts real
  distance by ~32.5% at Istanbul's latitude, silently. Only 1/20 runs
  overall executed successfully AND produced a non-empty result.
- **§7 (`system_hints`-mitigated): 20/20 structural success, 20/20
  correct `EPSG:32635`, 18/20 identical reproducible result** (166
  underserved mahalle, distance range 1010.0-13895.6m on every checked
  run — implying a consistent ~1000m threshold, the same tier Arm 1
  uses). The other 2/20 hit the exact self-defeating `max_distance`+
  `where` pattern from the 0.5.3 batch, precisely at the shipped
  warning's blind spot: `max_distance=1000.0` excludes exactly 166 of
  964 sources (17.2% — under the 20% default threshold), silently zero
  output. **First batch in this study where Arm 2 produces something
  genuinely comparable to Arm 1** — but it took the local mitigation to
  get there, not the upstream fix alone.
- **`enhancements/002` finalized and handed to the author to send
  upstream (2026-09-23).** Main ask, added after checking `v0.5.4`'s
  `s3geo/__init__.py::query()`: the framework builds `initial_inputs`
  from the layers *before* calling `generate()` but never passes the
  data's extent into the prompt — so the model guesses a CRS for a
  location it only knows from the user's wording. Asks upstream to
  compute a suitable projected CRS **from the input data itself** (UTM
  rule on the extent's centroid) and state it to the planner — making
  the correct CRS a property of the framework, not of the prompt, which
  is the author's stated goal for this framework. Plus: a
  generation-time `target_crs` resolvability check (catch the
  placeholder-copy failure before execution), a scale-factor-based
  distance-fidelity warning (catch the Web Mercator case), and
  reconsideration of the declined `max_distance`/`where` chain-validator
  with this batch's concrete 17.2% repro. The prompt deliberately
  contains **no example CRS codes** — an example code is exactly what
  0.5.4's root cause was, and an Istanbul code in the framework's prompt
  would leak this study's answer into its unmitigated arm. **If item 1
  lands**, re-running §5 at the new pin becomes the key test: the
  unmitigated arm getting the right CRS with no `system_hints` is the
  first result that would show the framework itself doing the job.

**`enhancements/002` landed in full, pin bumped to 0.5.5 (2026-09-23).**
All four items were confirmed against a fresh `v0.5.5` clone and the real
diff. Full detail is in `enhancements/002`'s "Resolution".

- **Item 1, data-derived CRS.** `s3geo.query()` now computes the input
  extent and states "Suitable projected CRS ... EPSG:<computed>" to the
  planner. I executed the pyproj-free parts on this repo's real
  `data/raw/`: both bbox edges are in UTM zone 35, so the framework will
  suggest `EPSG:32635`, the same CRS as Arm 1, with a max scale error of
  ~0.04%.
- **Item 4, max_distance/where validator.** Executed through the real
  `generate()` with plans shaped like this study's. It rejects every
  provably-empty case and accepts every case that can return features.
- **Items 2 and 3.** These need `pyproj`, which pip and apt both block in
  this sandbox, so they are verified from the diff and upstream's tests
  only.
- **How to read the next manifest:** there is still no retry loop. Plans
  caught by items 2 and 4 now fail at `error_stage="generation"`, where
  before they failed at execution or "succeeded" with 0 features. The
  success count can drop while the results get more honest.
- **Notebook changes, not yet run** (`notebooks/03_llm_arm.ipynb`, merged
  onto the author's executed copy, 0.5.4 outputs preserved):
  - every record now carries `smart_spatial_system_version` and the
    framework's computed `input_data_extent`;
  - new §3b computes the extent before any LLM call and asserts it is
    `EPSG:32635`;
  - the setup cell copies any existing `results/llm_runs*` batch into
    `results/archive/<dir>_<timestamp>/` before it is overwritten, so the
    0.5.4 batches survive the re-run;
  - §7's note says its `SYSTEM_HINTS` is now largely redundant with the
    framework, kept unchanged for comparability.
- **Next step:** reinstall at 0.5.5 and run the whole notebook. §5
  (no hints) is the real test: it is the first unmitigated batch in which
  the correct CRS comes from the framework itself.

**Both batches run at 0.5.5 (2026-09-23): with no hints, 9/20 runs
reproduce Arm 1 exactly.** Full breakdown: `paper/PLAN.md` "Findings",
0.5.5 entry.

- **§3b check.** It printed `EPSG:32635` with a 0.0367% max scale error,
  as predicted (0.0364% analytic). That confirms item 1 by real execution.
- **§5 (no hints):**
  - 5 runs returned exactly Arm 1's boundary > 1000 m set (166 `osm_id`s,
    distance diff 0.0 m), and 4 runs exactly its > 2000 m set (127).
  - 1 run was silently truncated: `max_distance=5000` kept 52 of 103 and
    dropped the 51 farthest.
  - 10 were rejected at generation by the `max_distance`/`where`
    validator.
  - CRS: 11/11 executed plans used `EPSG:32635`, and 0/21 had an
    unresolvable CRS. The CRS problem is solved in the unmitigated arm,
    by the framework.
- **The old "166 vs 325" puzzle is solved.** Arm 1's headline counts use
  centroid distance, while Arm 2 always measures from the polygon.
  Against Arm 1's `dist_boundary_m` the two arms agree exactly.
  `04_comparison_metric.ipynb` must compare Arm 2 with the boundary
  columns and report the definitional gap separately.
- **§7 (with hints) is now worse than §5.** Every plan set a
  `max_distance` cap. 10 were rejected and 10 were truncated, and none
  matches Arm 1. **Retire §7 from future batches.** The 0.5.4 §7 batch
  stays in `results/archive/` as the historical data point.
- **`enhancements/003` drafted, not yet sent.** It asks for:
  1. one repair retry, feeding a validator rejection's message back to
     the planner (recorded in the result, configurable);
  2. flagging *truncation*: a cap above a "farther than T" threshold
     silently drops the farthest features;
  3. returning the plan (QuerySpec) in `S3GeoResult` and in
     `LLMSpecGenerationError`.

  Also a question: every rejected plan used the key `max_distance_m`,
  which the prompt shows only in the `filter_by_distance` example.

**`enhancements/003` landed in full, pin bumped to 0.5.6 (2026-09-23) —
this closes Phase 4 for real.** All three drafted items shipped per
`CHANGELOG.md [0.5.6]`: a generation-time validator that rejects the
`max_distance`/`where` self-defeating pattern outright (rather than
leaving it a repro-rate problem), one automatic repair retry that feeds
the rejection message back to the model before recording a failure, and
`S3GeoResult.attempt_count`/`.repaired`/`.generation_attempts` (plus
`LLMSpecGenerationError.attempts` and, on the `RuntimeError` subclass,
`.generation_attempts` under a different attribute name — confirmed by
reading both exception classes' real source, not assumed from symmetry).
Confirmed by cloning tag `v0.5.6` and reading the diff directly; full
verification log and requirements.txt's bump comment have the rest.
**Both N=20 batches re-run clean end to end
(`notebooks/03_llm_arm.ipynb`, 2026-09-23): canonical 20/20 succeeded,
every one with `attempt_count=1, repaired=False` — the new repair
mechanism was never actually exercised, because no plan needed rejecting
in the first place; mitigated 18/20 succeeded, the other 2 a transient
HTTP 401 from the LLM provider (`attempt_count=0`, an infrastructure
error, not a validator rejection).** Every one of the 38 successful runs
across both batches is an **exact** `osm_id` match to Arm 1's
boundary-distance underserved set at that run's own chosen threshold —
the "166 vs 325" gap from the 0.5.5 entry above is fully explained by
the centroid-vs-boundary distance definition, not by any remaining
planning error. Full numbers: `paper/PLAN.md` "Findings", 0.5.6 entry.

**Phase 5 and 6 are also done, as of 2026-09-23/24.**
`notebooks/04_comparison_metric.ipynb` (Phase 5) implements
`paper/comparison_metric.md`'s full metric — PAR 1.0/0.9, Set Stability
0.9907/1.0000, Jaccard vs. Arm 1 1.0000/1.0000 (`results/metrics.csv`) —
and was re-executed independently by the paper author with output that
matched this study's own pre-commit validation number-for-number, so
those figures are now confirmed twice over, not just once.
`notebooks/05_results.ipynb` (Phase 6) produces
`results/figures/fig1_underserved_map.png` (Arm 1's headline map, 2000 m
centroid),  `fig2_threshold_sensitivity.png` (underserved count vs.
threshold, both distance definitions, Arm 2's own runs overlaid exactly
on the boundary curve), and `fig3_arm_disagreement.png` (the
boundary-vs-centroid definitional gap at Arm 2's modal 1000 m threshold —
166 vs. 325, the same "166 vs 325" pair named above, now mapped). None
of the three figures is an Arm-1-vs-Arm-2 disagreement map — see the
notebook's own header for why that would be the wrong title. `paper/paper.md`
is a full draft through all 7 phases as of this update. **Only remaining
open items are named in `paper/PLAN.md`'s "Open decisions" and this
file's own stale-content warning below** — a dedicated batch to close the
partial Rank Stability gap, and the hospital-only re-run, neither
executed.

**Three environment facts, current as of 2026-09-19, likely to still matter
next session:**

1. Overpass works via the `overpass.kumi.systems` mirror, not the primary
   `overpass-api.de` host (connection reset at TLS). The mirror is a shared
   public instance — expect `504`/timeouts under load and retry with
   patience, not a smaller AOI. **A `200 OK` with a suspiciously small
   `elements` array is not proof of a real empty result** — this session hit
   exactly that once; see `data/README.md` "Fetched" before trusting a
   surprising count.
2. `github.com/arazshah/smart_spatial_system` is readable with a plain
   `git clone` (its GitHub *API* is still blocked — don't rely on `gh` or
   the API for it, clone it). **PyPI is still blocked** (`403`), so the
   package can be *read* from a git clone but not actually *installed* from
   here — `pip install` fails on build dependencies it can't fetch. Phases
   2+ still can't execute in this environment; the plan below is now
   verified against real 0.3.0 source, but nothing has run it yet.
   **Update (2026-09-23):** at the 0.5.6 pin, `pip install
   smart-spatial-system==0.5.6` succeeded outright in this study's cloud
   sandbox — the first time this fact has changed. Most likely
   session/network-scoped rather than a permanent fix (this note dates
   from 2026-09-19, three pins earlier), so keep the tag-install fallback
   in `requirements.txt` until it is confirmed working from a fresh
   environment more than once.
3. **`git push` to this repo's own GitHub remote is blocked** — a separate
   authorization from data network access ("not in this session's
   authorized repository set"). Confirmed still blocked after the network
   grant that fixed #1/#2. Commits exist locally / delivered as a bundle;
   check whether `origin/main` actually has them before assuming a push
   succeeded.

**Check `paper/PLAN.md`'s phase table and the actual contents of `data/`
and `results/` before assuming anything above is still current; this file
is not updated every session.**

## Verified against 0.3.0 source (2026-09-19) — corrections to the plan

Read directly from `github.com/arazshah/smart_spatial_system` at tag
`v0.3.0` (`orchestrator/planning/op_catalog.py` is the ground truth for
every planner-reachable operation name):

- `crs_transform`, `spatial_nearest` (`nearest_neighbor` is a confirmed
  alias), `score_features`, `rank_features`, `build_report`,
  `filter_points_in_polygon`, `spatial_join` are all real.
- **`zonal_statistics` is wrong for this study** — confirmed
  raster-over-polygon (`calculate_zonal_statistics`, takes a `raster`
  input). Use `filter_points_in_polygon` or `spatial_join` to count
  hospitals per mahalle instead. See `paper/PLAN.md` Arm 1 step 3.
- **`dissolve_features` exists as a plugin capability but is not in
  `OP_CATALOG`**, so `s3geo.query()`/`QuerySpec` planning can never produce
  it — only Arm 1, calling the plugin directly, can dissolve. This is a
  structural asymmetry between the two arms, not a bug; name it in the
  paper. See `paper/PLAN.md` Arm 1 step 5.
- `s3geo.query(raw_query, *, layers, context=None, system_hints=None)` is
  real and new in 0.3.0 (`s3geo/__init__.py`) — use it for Arm 2 rather than
  wiring the five underlying classes by hand (that manual path, from the
  Vienna study's 0.2.x notes, still works but is no longer the documented
  entry point). Don't confuse it with `orchestrator/llm_intent_planner.py`,
  an older/separate planning path used by a different REST-API-facing flow
  — out of scope here, and the one place `dissolve_features` actually is
  reachable, for context.
- Not yet checked: whether Vienna's candidate CRS-mismatch bug still exists
  at 0.3.0. The nearest-neighbour op's own param docstring claims passing
  `source_crs`+`target_crs` makes it raise instead of silently computing
  nonsense — verify this by deliberately triggering it before trusting it.

## Layout

```
data/README.md        the exact Overpass queries, the AOI, and licensing
data/raw/             Overpass output (gitignored - regenerate, don't commit)
data/processed/       small derived GeoJSON actually fed to the system
notebooks/01_..05_    one numbered notebook per phase, run in order
scripts/              data download + Overpass->GeoJSON conversion helpers
results/              rankings, per-run LLM specs, metrics, figures
paper/PLAN.md         research question, phases, methodology, estimate
paper/comparison_metric.md  the three-layer metric - read before comparing
paper/paper.md        the deliverable write-up
bugs/                 structured upstream bug reports (see bugs/README.md)
claude-notes/         session notes that don't belong in the paper
```

## Analysis conventions

- **CRS.** Source data is EPSG:4326. Every metric operation (distance,
  threshold, area) happens in **EPSG:32635** (WGS 84 / UTM zone 35N),
  which covers Istanbul either side of the Bosphorus. Reproject *every*
  layer that takes part in a distance call, individually — the Vienna repo
  lost three full N=20 batches to plans that reprojected one layer and
  silently compared it against another still in degrees (its upstream
  candidate Bug 5). Assert the CRS of every input immediately before a
  distance op, in both arms.
- **Threshold.** 2000 m nearest-facility distance is the default
  "underserved" cutoff; it is a parameter, not a finding. Report the
  underserved set at 1000 / 1500 / 2000 m so the headline number is
  visibly sensitive to it.
- **Distance semantics.** `nearest_neighbor` is polygon-to-point: a mahalle
  containing a hospital gets distance `0.0`. That is correct GIS behaviour,
  not a bug, but it means dense mahalles tie at the top. Report both the
  polygon-boundary distance and a **centroid-based** distance — for a
  health-access question the centroid figure is the more honest one, and
  the tie structure is itself worth a paragraph (Vienna tied 21 of 23
  districts this way).
- **The LLM arm chooses its own parameters.** The natural-language query
  handed to the LLM arm must *not* contain the 2000 m threshold or the CRS
  — picking those is exactly what is being measured. Keep the raw query
  string identical across all N runs, in one place, and commit it.
- **A degenerate result is a result.** Run a `ranking_is_degenerate()`-style
  check (all scores equal, all zero, or every mahalle flagged) automatically
  in both arms and record it as its own column, separate from
  "execution succeeded". Vienna hid three failed batches behind a clean
  100% success rate before adding this.
- **Smoke-test before any N-run batch.** One LLM call, printed plan, eyes
  on the operation sequence and the per-layer CRS, *then* the full loop.
  Vienna spent three full N=20 batches (~60 API calls) on mistakes a
  single-call smoke test would have caught.

## Network and secrets

- Data download (Overpass/OSM) and the LLM arm both need real internet.
  Test before assuming:
  `curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://overpass-api.de/api/status`
  If it is blocked, say so explicitly rather than silently skipping — the
  user needs to know to run that phase locally or open a session with a
  wider egress policy. Do not route around an egress denial.
- Three known ways to get `data/raw/` populated when the sandbox is
  blocked: (a) run the phase on a machine with normal internet;
  (b) run the queries from inside a real desktop browser via the Chrome
  integration, which uses the user's own network rather than the sandbox's
  (this is how the Vienna repo's data was fetched); (c) paste/attach the
  raw Overpass JSON into the session and convert it with
  `scripts/overpass_to_geojson.py`. All three end at the same converter,
  so the downstream phases don't care which was used.
- Installing the pin needs either PyPI (if `0.3.0` was published there) or
  read access to `github.com/arazshah/smart_spatial_system` — see
  `requirements.txt`.
- The LLM key goes in `.env` (copy `.env.example`), never hardcoded, never
  committed. Only the LLM arm needs it — don't ask for it, or fail earlier
  phases for its absence.

## Working style for this repo

- Notebooks are numbered and run in order; each phase's notebook is that
  phase's deliverable, not a scratch file — keep them readable enough for
  an advisor to open.
- Commit every output a later phase depends on (`data/processed/`,
  `results/llm_runs/`, `results/metrics.csv`) so nobody has to re-run an
  expensive step — especially the LLM arm — to reproduce a later one.
- When a phase finishes, update "Current state" above **in the same
  commit**. This file is how the next session picks up without a briefing.
- Keep `paper/PLAN.md`'s "Open decisions" current: resolve items there when
  they're decided rather than leaving them stale.
