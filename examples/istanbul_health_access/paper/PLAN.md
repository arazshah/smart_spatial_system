# Experiment and paper plan

Working title: *Rule-Based versus LLM-Planned Spatial Query Pipelines for
Health-Facility Accessibility: An İstanbul Mahalle Case Study*

## Research question

**Which İstanbul mahalle (neighbourhoods) are underserved by hospitals and
clinics — i.e. whose nearest facility is farther than a reasonable
walking/driving threshold (2000 m)?**

And, running alongside it as the methodological question this repo shares
with its Vienna sibling: **given that same question in natural language,
does an LLM-backed query planner build the same pipeline, choose comparable
parameters, and identify the same underserved mahalle as an explicitly
written rule-based plan?** Where the two disagree, the disagreement is the
finding — the substantive map and the methodological result are both
deliverables, not one in service of the other.

## Units of analysis

İstanbul Province's *mahalle* (`admin_level=10` in OSM), expected ~950–1000.
Chosen because they are the finest official administrative unit for which
boundaries are citable and complete, they are the unit Turkish municipal
health planning actually uses, and they need no invented data. See
`data/README.md` for the AOI and the exact queries.

## Layers

| Layer | Geometry | Role |
|---|---|---|
| `mahalle` | Polygon / MultiPolygon | the sites being scored |
| `hospitals` | Point (nodes + way centroids) | the facilities distance is measured to |

`amenity` (`hospital` vs. `clinic`) is carried through as a property so the
whole analysis can be re-run hospital-only without re-fetching.

## Methodology

Both arms answer the same question, over the same two layers, in
EPSG:32635, and are then compared. Neither arm is allowed to see the other's
plan.

### Arm 1 — rule-based (deterministic by construction)

**Verified against 0.3.0 source 2026-09-19** (`orchestrator/planning/op_catalog.py`
— every op below is a real, planner-reachable `op_name`; this replaces an
earlier draft of this plan that used a wrong operation for step 3):

1. `crs_transform` **each** layer individually, 4326 → 32635. Assert the CRS
   of every input immediately before any distance call.
2. `spatial_nearest` (mahalle → hospitals; `nearest_neighbor` is a
   confirmed alias, prefer `spatial_nearest` per the catalog's own note) for
   the polygon-boundary distance, and a second pass on mahalle **centroids**
   for the centroid distance. Both are reported; the centroid figure is the
   honest one for a health-access question, because polygon-to-point
   returns `0.0` for any mahalle that happens to contain a facility. Pass
   `source_crs`/`target_crs` explicitly — the catalog notes this lets
   `find_nearest_neighbors` raise on a CRS mismatch instead of silently
   returning a nonsense planar distance (Vienna's candidate Bug 5, if still
   unhandled at 0.3.0 — check `plugins/distance_calculator.py` and file a
   `bugs/` report if it isn't).
3. **Not `zonal_statistics`** — confirmed from source to be a
   raster-over-polygon operation (`calculate_zonal_statistics`, takes a
   `raster` input; "mean NDVI per district" is its own example use case).
   It cannot count point features. For the per-mahalle facility count, use
   `filter_points_in_polygon` (hospitals against mahalle, true point-in-
   polygon, not bbox) or `spatial_join` (predicate `within`/`contains`),
   then aggregate the per-mahalle count in the notebook. This is what
   separates "has one clinic just inside the border" from "is genuinely
   well served".
4. Threshold at 2000 m → boolean `underserved`.
5. Dissolve the underserved mahalle into contiguous underserved regions —
   **⚠️ `dissolve_features` (`plugins/dissolve_aggregator.py`) exists as a
   registered capability but is confirmed absent from `op_catalog.py`'s
   `OP_CATALOG`**, so it is not a planner-reachable `op_name` and
   `s3geo.query()` / `QuerySpec` can never produce it (only a param of
   `buffer`, `dissolve: true`, which merges overlapping *buffers*, not
   arbitrary polygons by attribute). This arm can still call
   `dissolve_aggregator.dissolve_features` **directly as a plugin
   function**, bypassing the planner — that's fine for a rule-based arm
   that is allowed to call plugins directly. But it means **Arm 2 cannot
   ask for this step at all**, structurally, however good its prompt is.
   Name this explicitly in the paper as a capability asymmetry between the
   two arms, not a fair comparison point — don't score the LLM arm down for
   never producing a dissolve it has no way to request. (Separately: this
   reachability gap matches a pattern `op_catalog.py`'s own docstring
   already names for several source-loading plugins — likely known to
   upstream already, worth a quick issue/PR upstream rather than a `bugs/`
   report, which is for behaviour that's wrong, not merely unexposed.)
6. `build_report` → the underserved table.

Repeat 4–6 at **1000 / 1500 / 2000 m** so the headline count is visibly a
function of the threshold rather than a fact.

### Arm 2 — LLM-planned

**Confirmed 2026-09-19: `s3geo.query(raw_query, *, layers, context=None,
system_hints=None)` is real and new in 0.3.0** (`s3geo/__init__.py`) — a
thin wrapper that wires up exactly `OpenAICompatibleLLMClient` +
`LLMQuerySpecGenerator` + `DeterministicPlanner` + `CapabilityRegistry` +
`RegistryCapabilityResolver` + `DagExecutor`, still directly usable
individually (which is what the Vienna repo, on 0.2.x, used before this
wrapper existed — use `s3geo.query()` here since it's now the documented
one-call path and returns `.goal`/`.operations`/`.output` directly, closer
to what phase 4/5's extraction needs). `layers` accepts GeoJSON dicts or
`GeoDataFrame`s directly. Note: there is a *second*, older/separate planning
path in this codebase (`orchestrator/llm_intent_planner.py`, used by
`application/services/llm_intent_adapter.py` for a different, REST-API-
facing flow, not `s3geo.query()`) — do not confuse the two; it has its own
op vocabulary (including, notably, `dissolve_features` — reachable there
but not through `s3geo.query()`, see Arm 1 step 5) and is out of scope here.

It gets a natural-language version of the same question and the same two
layers as input. The raw query string:

- states the question, the two layers and the city;
- **deliberately omits** the 2000 m threshold, the CRS, the centroid-vs-
  boundary choice and the operation list — choosing those is exactly what is
  being measured;
- is identical across all N runs, lives in exactly one place in the
  notebook, and is committed.

Run N = 20 times at temperature 0.1 (both carried over from the Vienna
study's resolved decisions; revisit if early variance is high). **0.1 is
`LLMQuerySpecGenerator`'s own constructor default and `s3geo.query()`
never overrides it** — confirmed from source, so N=20 calls at 0.1 needs
no explicit temperature handling in the notebook at all. Every run —
success or failure — is saved to `results/llm_runs/run_{i:02d}.json` with an
index in `manifest.csv`. A failure is data, not something to retry away.

**Smoke test first.** One call, print the generated operation sequence,
check that operations chain (each op consuming the previous op's output, not
a stale pre-distance vector), that every layer in a distance call was
individually reprojected, and that the degeneracy check does not fire.
Only then the full loop. The Vienna repo burned three full N=20 batches on
mistakes one call would have caught. `notebooks/03_llm_arm.ipynb`
deliberately stops after the smoke test for a second reason too: `result.output`'s
shape depends on which operation the LLM's plan ends on (`VectorOut`,
`ReportOut`, or a plain dict), and the property name it used for the
underserved flag/distance is unknowable ahead of a real call — the
extraction logic for "which mahalle are underserved" belongs in
`04_comparison_metric.ipynb`, written after seeing real output, not
guessed at here.

**Two more things confirmed by reading `orchestrator/planning/planner.py`
and `llm_spec_generator.py` directly, both load-bearing for how the raw
query in this notebook is worded:**

- `PlannerConfig.allow_implicit_entities` defaults to `True` — any input
  ref the LLM's plan uses that isn't another operation's output becomes
  `$inputs.<that ref>` and is looked up directly in the `layers=` dict, no
  schema/binding step in between. So the raw query names the two layers
  as exact quoted literals (`'hospitals'`, `'mahalle'`) matching the
  `layers=` dict keys — a plan that names them anything else fails at
  execution with an unresolvable reference, not a wrong answer.
- `llm_spec_generator.py` already has two hard, generation-time
  validators directly relevant to this study, both raising
  `LLMSpecGenerationError` (not just prompt guidance) before a bad plan
  ever executes: `_validate_distance_op_crs_symmetry` (catches a
  distance op whose two inputs were reprojected to different CRSs, or
  asymmetrically — this is Arm 2's own version of the Vienna Bug 5
  concern, generation-time rather than Arm 1's runtime
  `find_nearest_neighbors` hint check, though it only fires when it can
  trace *both* refs to a `crs_transform` output — a plan that never
  reprojects either layer at all isn't caught by this validator, so that
  failure mode is still worth watching for in real output) and
  `_validate_filter_points_in_polygon_usage` (blocks `filter_points_in_polygon`
  when the raw query asks "which zone" a point falls into — its
  docstring says this was added because prompt guidance alone didn't
  stop `gpt-4o-mini` from repeating exactly the mistake this repo's
  Arm 1 step 3 analysis independently flagged: using
  `filter_points_in_polygon` for a question that needs to know *which*
  polygon matched, not just whether one did. Independent corroboration,
  from a different source, of the `spatial_join` decision above).

### Comparison

Per `paper/comparison_metric.md`: Plan Agreement Rate (structural),
parametric agreement (the thresholds/weights the LLM invents), and outcome
agreement — here measured both as rank stability and, because the primary
output is a *set*, as **Jaccard overlap of the underserved-mahalle sets**
between runs and against the rule-based reference.

## Data sources

OpenStreetMap via the Overpass API; exact queries, AOI, CRS justification,
caveats and licensing in [`data/README.md`](../data/README.md).

## Phases

| # | Phase | Output | Network? | LLM key? |
|---|---|---|---|---|
| 0 | Repository skeleton | this repo — **DONE (2026-09-19)** | no | no |
| 1 | Data acquisition | `data/raw/` — **DONE (2026-09-19)**: 1020 hospitals/clinics, 964 mahalle (see `data/README.md` "Fetched" for the two-fetch history) | **yes** (Overpass) | no |
| 2 | Data prep + problem definition | `notebooks/01_data_and_problem.ipynb`, `data/processed/` — **DONE (2026-09-19)**, executed on the author's own machine (real internet + installed pin) | no | no |
| 3 | Rule-based arm | `notebooks/02_rule_based_arm.ipynb`, `results/rule_based_*.{csv,json}` — **DONE (2026-09-19)**, executed on the author's own machine; see "Findings" below | no | no |
| 4 | LLM arm (N=20) | `notebooks/03_llm_arm.ipynb`, `results/llm_runs/` + `results/llm_runs_mitigated/` — **DONE, both batches re-run clean at 0.5.6 (2026-09-23)**: 20/20 canonical (no hints) and 18/20 mitigated (2/20 a transient provider-side auth error, not a planning failure) — **every successful run in both batches is an exact set match to Arm 1's boundary-distance set at that run's own chosen threshold**. Pin history and the full breakdown of every intermediate pin's batch: "Findings" below, 0.5.6 entry (latest). | **yes** | **yes** |
| 5 | Comparison metric | `notebooks/04_comparison_metric.ipynb`, `results/metrics.csv` — **DONE (2026-09-23)**: PAR 1.0 / 0.9, Set Stability 0.9907 / 1.0000, Jaccard vs. Arm 1 1.0000 / 1.0000. Rank Stability is partial only (shared-output-subset, not the full 964 mahalle) — see notebook §10 and "Findings" below. | no | no |
| 6 | Map + figures + table | `notebooks/05_results.ipynb`, `results/figures/` — **DONE (2026-09-24)**: `fig1_underserved_map.png` (Arm 1 headline, 2000 m centroid, 207/964), `fig2_threshold_sensitivity.png` (both distance definitions across 800/1000/1500/2000 m, Arm 2's own runs overlaid exactly on the boundary curve), `fig3_arm_disagreement.png` (the boundary-vs-centroid definitional gap at Arm 2's modal 1000 m: 166 boundary / 159 centroid-only / 639 served-under-both). Tested via `jupyter nbconvert --execute` against real data before committing (un-executed), same convention as `01`–`04`. | no | no |
| 7 | Paper | `paper/paper.md` — **DONE (2026-09-24)**: full draft through all 7 sections, Phase 6 figures folded into Results, no open placeholders. | no | no |

Phases 2, 3, 5, 6, 7 run entirely offline once `data/processed/` exists,
using only the pinned package. Phases 1 and 4 are the only ones needing
network; 4 is the only one needing a key.

**Phase 0 exit note (2026-09-19):** the first session that built this
skeleton could reach neither `overpass-api.de` nor PyPI, and had no
credentials for `github.com/arazshah/smart_spatial_system`. A follow-up
session the same day, after the user widened network access, found:
Overpass reachable via the `overpass.kumi.systems` mirror (the primary
`overpass-api.de` host still resets the connection; the mirror is a shared
public instance and needs retries under load — see `data/README.md`
"Fetched"); `github.com/arazshah/smart_spatial_system` readable via plain
unauthenticated `git clone` (its GitHub *API* stays blocked, but the repo
itself is public over git); **PyPI still blocked** (`403`, both directly
and through the proxy) — `pip install` cannot resolve `smart-spatial-system`
or its build dependencies from here, so the package still is not actually
*installed* anywhere phases 2+ could use it, only read. **Git push to this
repo's own GitHub remote is also still blocked** ("not in this session's
authorized repository set") — a separate authorization from data network
access; phase 1's commits exist locally / as a delivered bundle, not
pushed. See `STUDY_LOG.md` → "Network and secrets" for what each of these
means for the phases ahead.

## Expected outputs

- `data/processed/mahalle.geojson`, `data/processed/hospitals.geojson`
- `results/rule_based_underserved.csv` — one row per mahalle: name,
  boundary distance, centroid distance, facility count, `underserved` at
  each of the three thresholds. **No `ilçe` (district) column** — the
  fetched admin_level=8 relations don't carry a parent-district tag
  (verified against `data/raw/mahalle_boundaries.geojson`'s real
  properties: `osm_id`, `osm_type`, `admin_level`, `boundary`, `name`,
  plus incidental tags like `postal_code`/`wikidata`, no district field).
  Would need its own admin_level=6 fetch + spatial join to add.
- `results/underserved_regions.geojson` — the dissolved underserved areas
- `results/llm_runs/` — N QuerySpecs + N underserved sets + `manifest.csv`
- `results/metrics.csv` — PAR, parametric spread, Jaccard/rank stability,
  success rate, latency
- `results/figures/fig1_underserved_map.png` — the choropleth/dissolve map
- `results/figures/fig2_threshold_sensitivity.png` — underserved count vs.
  threshold
- `results/figures/fig3_arm_disagreement.png` — where the two arms differ
- `paper/paper.md` — map, underserved table, methodology, findings
- `bugs/*.md` — if and only if something upstream misbehaves

## Effort estimate

| Phase | Estimate | Dominated by |
|---|---|---|
| 0 | 0.5 d — **done** | — |
| 1 | 0.5 d | Overpass round-trips and retries; ~1000 relations with full geometry is a slow query |
| 2 | 1 d | ring assembly / invalid-geometry cleanup on ~1000 mahalle, and confirming the counts in `data/README.md`'s caveat list |
| 3 | 1 d | getting the two distance variants and `zonal_statistics` wired correctly; CRS assertions |
| 4 | 1–3 d | **the wide one.** ~20 API calls is an hour; the Vienna sibling needed six rounds of diagnosis before a batch came back non-degenerate. Budget for at least one upstream bug report. |
| 5 | 0.5 d | mostly mechanical once `results/llm_runs/` is populated |
| 6 | 1 d | cartography for ~1000 polygons is fiddly — legible at A4 is the constraint |
| 7 | 2 d | writing |
| | **~8–10 working days** | assuming phase 4 costs one bug-report cycle, not three |

## Open decisions

- **✅ Resolved 2026-09-19: mahalle is `admin_level=8`.** Probed against
  the live database — see `data/README.md` caveat 6. 964 mahalle fetched
  clean (0 skipped rings), so the "unit of analysis needs reconsidering"
  fallback (ilçe at level 6, ~39 units) did not end up needed.
- **✅ Resolved 2026-09-19: operation names verified against 0.3.0 source**
  (`orchestrator/planning/op_catalog.py`, read directly — see Arm 1/Arm 2
  above for the corrections this produced). `zonal_statistics` *was* the
  wrong operation, exactly as flagged; replaced with `filter_points_in_polygon`
  / `spatial_join` + notebook-side aggregation. `dissolve_features` is a
  real capability but not planner-reachable through `s3geo.query()` — a
  structural limit on Arm 2, not a bug to route around.
- **✅ Resolved 2026-09-19 (by design, execution pending): `spatial_join`,
  not `filter_points_in_polygon`, for step 3.** Read both plugins' full
  source (`plugins/spatial_predicate.py`, `plugins/spatial_join.py`).
  `filter_points_in_polygon` takes the target polygons as one
  undifferentiated containment mask and reports only whether each point
  fell inside *any* of them — no polygon identity survives into the
  output, so it cannot produce a per-mahalle breakdown without one call
  per mahalle (964 calls). `spatial_join_features` attaches the matched
  mahalle (`_target_index`, and with `include_target_properties=True` the
  mahalle's own properties) to each hospital in a single call, which a
  `groupby` then turns into per-mahalle counts. Implemented in
  `notebooks/02_rule_based_arm.ipynb` §5 with the reasoning inline. Also
  found while reading `spatial_join.py`: unlike `find_nearest_neighbors`,
  `spatial_join_features` has no `target_crs` param and never validates
  `source_crs` against anything — it is a passive hint only, not a
  mismatch check. Not a bug (its docstring never claims otherwise), just a
  reason both layers must already be reprojected to the same CRS before
  calling it, not an operation this repo can lean on for CRS safety.
  Also confirmed by the real `spatial_join_features` run (§5, below): 1020
  hospitals in, 1020 matched, 0 unmatched — no coastline/boundary edge
  cases lost.
- **✅ Fully resolved 2026-09-19, now empirically confirmed (real run,
  `notebooks/02_rule_based_arm.ipynb` §3): the CRS-mismatch safety net on
  `find_nearest_neighbors` (Vienna's candidate Bug 5) is present and
  correct at 0.3.0, with a caveat.** `plugins/distance_calculator.py`'s
  `_raise_if_crs_mismatch()`
  (imported into `nearest_neighbor.py`) raises when *both* `source_crs`
  and `target_crs` are supplied and the two strings don't match — read the
  full function body, not just its docstring. **Caveat**: it is a
  normalized *string* comparison of the hints the caller passes, not an
  inspection of the actual coordinates — it catches mismatched labels, not
  mislabelled-but-consistent data (e.g. both layers claimed as
  `EPSG:32635` when one was never actually reprojected). So it's real
  protection against the specific mistake Vienna hit (chaining a call
  across a still-4326 layer with mismatched hints), but not a substitute
  for actually calling `crs_transform` and using its output.
  `notebooks/02_rule_based_arm.ipynb` §3's smoke test confirmed this on a
  real run: the mismatched-hint call raised the exact message quoted in
  source, the matching-hint call succeeded. Not a bug — matches the
  documented behavior exactly.

- **N and temperature.** N=20 at 0.1, inherited from the Vienna study.
  Revisit once early variance is visible — a set-valued output may be more
  or less stable than a ranking, which is an open empirical question.
- **Hospital-only vs. hospital+clinic as the headline definition.** Default
  is both, with the hospital-only re-run reported alongside. Decide which
  leads once the counts are in.
- **Population weighting.** An underserved mahalle of 200 people and one of
  40,000 are not the same finding. TÜİK publishes mahalle-level population;
  whether to bring it in (and whether that makes this a different paper)
  is undecided. The OSM-only pipeline stands on its own either way.
- **Road-network distance vs. Euclidean.** Everything above is straight-line
  distance. In a city cut by the Bosphorus and steep topography that is a
  real limitation, and naming it is mandatory; upgrading to network distance
  is a possible follow-up, not in scope here.
- Target venue and its format, once the advisor weighs in.

## Findings to fold into the paper's discussion/limitations

**Phase 2 (`notebooks/01_data_and_problem.ipynb`), executed 2026-09-19,
real output — every check in the notebook passed, nothing silently
skipped:**

- Loaded 1020 hospitals/clinics (421 hospital, 599 clinic — see
  `data/README.md` "Fetched" for why this differs from the phase-1 count)
  and 964 mahalle (955 Polygon, 9 MultiPolygon) from `data/raw/`. Both
  layers confirmed `EPSG:4326` on load.
- `amenity` values are exactly `{hospital, clinic}` — the unanchored regex
  (`data/README.md` caveat 4) did not pull in anything unexpected.
- **Zero null geometries, zero invalid mahalle rings, zero unnamed
  mahalle** — the data needed no repair beyond the `buffer(0)` safety net
  (which found nothing to fix).
- Both layers reprojected to `EPSG:32635` individually and asserted;
  `mahalle`'s bounds in the metric CRS (`x: 581527–748500`,
  `y: 4519307–4604146`) are sane for İstanbul in UTM 35N — the
  reprojection-sanity check this repo's STUDY_LOG.md asks for (after the
  Vienna sibling's CRS-mismatch history) passed on the first real run.
- **9 mahalle have a centroid that falls outside their own polygon**:
  Mimar Kemalettin, Fatih, Orhanlı, Malkoçoğlu, Şamlar, **Kınalıada**,
  Maden, Esenkent, Karaburun. Kınalıada is literally one of the Princes'
  Islands — an island mahalle with an irregular/multi-part coastline is
  exactly the shape where a polygon centroid can land outside the polygon
  (or in the sea next to it). Not a bug; name these nine explicitly if the
  paper's methodology uses centroid distance, since their centroid-based
  distance to the nearest hospital may not mean what it looks like it
  means.
- `data/processed/hospitals.geojson` (1020 features) and
  `data/processed/mahalle.geojson` (964 features) written in `EPSG:32635`,
  spot-checked: CRS block reads `EPSG::32635`, sample coordinates are
  6-digit easting/northing in metres (not degrees), `osm_id` retained on
  every feature for ODbL attribution.

**Phase 3 (`notebooks/02_rule_based_arm.ipynb`), executed 2026-09-19, real
output — first run hit `bugs/001` (see there), revision 2 (with the
mitigation) ran clean end to end:**

- **`transform_vector_crs` vs. `geopandas.to_crs()` cross-check passed**:
  max bounds difference 0.9 m across all four corners of the mahalle
  layer — the framework's own reprojection op and phase 2's independent
  geopandas reprojection agree.
- **CRS-mismatch smoke test passed exactly as source predicted**: the
  deliberately-mismatched-hint call raised
  `find_nearest_neighbors: source_crs='EPSG:4326' and target_crs='EPSG:32635' do not match...`;
  the matching-hint call succeeded. Vienna's candidate Bug 5 does not
  reproduce at 0.3.0 — confirmed empirically, not just by reading source.
- **458 / 964 mahalle (47.5%) contain at least one facility**
  (`dist_boundary_m == 0.0`), matching exactly the 458 mahalle
  `spatial_join_features` found with `facility_count >= 1` — the two
  independent op calls (nearest-neighbor and spatial-join) agree on which
  mahalle have a facility inside them, a real internal-consistency check
  this notebook doesn't currently print explicitly but which held.
- **`spatial_join_features`: 1020 / 1020 hospitals matched to a mahalle, 0
  unmatched** — no coastline/boundary edge cases lost. 506 mahalle (52.5%)
  have zero facilities; among the 458 that have ≥1, mean 2.23, max 32
  (one dense central mahalle).
- **Underserved counts, all three thresholds** (centroid distance):
  1000 m → 325 (33.7%), 1500 m → 251 (26.0%), **2000 m → 207 (21.5%)**.
  `dist_centroid_m`: mean 1762 m, median 610 m, max 17,295 m (right-skewed
  — dense urban core near facilities, rural/exurban fringe far away).
- **207 underserved mahalle at 2000 m dissolve into 6 contiguous regions**
  (`dissolve_features(group_by=None)` + `.explode()`) — plausible for
  İstanbul's outer, less-built-up periphery; worth a map (phase 6) to
  confirm this visually rather than trusting the count alone.
- **`build_report`'s default `report_spec` truncates its own table to 50
  rows** even though `summary.total_count` correctly reports 207 — a real
  usability rough edge of the default real-estate-oriented spec (not a
  bug: `results/rule_based_underserved.csv`, written independently, has
  the full 964 rows and is the actual source of truth for the paper).
- **A real bug in this notebook's own code, not the framework, was found
  and fixed while reviewing this run**: revision 2's `centroid_outside_polygon`
  column was computed by matching `dist_df["name"]` against a hardcoded
  set of 9 names copied from this file's own Phase 2 findings above
  (`"Fatih"`, `"Kınalıada"`, ...) — but the real `name` field is
  `"Fatih Mahallesi"` etc. (includes the `Mahallesi` suffix), so the
  `.isin()` check silently matched nothing and every one of the 964 rows
  came back `False`. `01_data_and_problem.ipynb` must have stripped that
  suffix only for its own printed summary. Revision 3 recomputes this
  directly with `geometry.contains()` instead of trusting a copied name
  list — self-contained, not dependent on another notebook's stdout
  formatting. **Confirmed fixed by the revision-3 re-run**: recomputed
  directly with `geometry.contains()`, it found **exactly the same 9
  mahalle** `01_data_and_problem.ipynb` found independently (Mimar
  Kemalettin, Fatih, Orhanlı, Malkoçoğlu, Şamlar, Kınalıada, Maden,
  Esenkent, Karaburun) — two different computations (this notebook's
  `geometry.contains()` vs. phase 2's own check) agreeing is good evidence
  both are right. Every other number in this section was already
  unaffected by this bug (it only ever touched that one diagnostic
  column) and is unchanged in the revision-3 run.
- **Phase 3 is fully done.** Revision 3 of `notebooks/02_rule_based_arm.ipynb`
  ran clean end to end with no errors across all 11 code cells, all
  numbers above are from that real run, and `results/rule_based_underserved.csv`
  (964 rows, all columns including the corrected `centroid_outside_polygon`),
  `results/underserved_regions.geojson` (6 regions), and
  `results/rule_based_report.json` are written and current.

**Phase 4 (`notebooks/03_llm_arm.ipynb`), attempted 2026-09-19, real
output — the smoke test call (§4) itself crashed, not this repo's code:**

- `s3geo.query()`'s first call raised `ModuleNotFoundError: No module
  named 'rasterio'` before the LLM's plan ever ran — on a query that is
  100% vector (hospital points, mahalle polygons, nearest-facility
  distance) and never mentions raster/NDVI anywhere. Traced to real 0.3.0
  source, not guessed: `s3geo.query()` builds its plugin registry via
  `CapabilityRegistry.from_plugin_modules()` with no arguments, which
  defaults to `tolerant=False` and eagerly imports *all* 38 modules in
  `DEFAULT_SAFE_PLUGIN_MODULES` — including `plugins/ndvi_analysis.py`,
  which does an unconditional top-level `import rasterio` (an optional
  extra per `pyproject.toml`'s own `raster = ["rasterio"]`, not installed
  in this repo's `.venv`). Full writeup: `bugs/002-s3geo-query-crashes-without-raster-extras.md`.
- This is a hard framework bug, not a planning/prompt issue — the
  registry build happens before the plan's operations are even inspected,
  so no query, however well-formed, could avoid it in an environment
  without `rasterio` installed. `orchestrator/service.py`'s own
  `OrchestratorService` builds the exact same registry with
  `tolerant=True` for exactly this reason (documented in `pyproject.toml`
  itself); `s3geo.query()` — new in 0.3.0 — just didn't follow that
  established pattern, and doesn't expose a way for a caller to pass
  `tolerant=True` through.
- **Fixed upstream in `smart_spatial_system` 0.4.1** (2026-09-19),
  confirmed by cloning the tag and reading the diff directly, not taken
  on the fixer's word: `s3geo.query()` now defaults to `tolerant=True`
  (and exposes it as a real parameter), and `ndvi_analysis.py`'s
  `import rasterio` moved to a lazy import inside `process_ndvi()`. This
  repo's pin moved `smart-spatial-system==0.3.0` → `==0.4.1` (0.4.0 sits
  in between and is purely additive per its own `CHANGELOG.md`, so no
  intervening behavior change to worry about); `bugs/002`'s "Resolution"
  section has the full diff. The interim local mitigation
  (`pip install rasterio`) is superseded, not needed on a fresh install
  at the new pin. Waiting on the author to reinstall at `==0.4.1` and
  re-run the smoke test (§1-4) before Phase 4's N=20 batch runs.
- Symmetric to `bugs/001` in an interesting way for the paper's
  discussion: Arm 1 hit a crash Arm 2's code path avoids
  (`VectorOut.from_geopandas`'s datetime bug), and Arm 2 hit a crash
  Arm 1's code path avoids (this one — Arm 1 imports specific plugin
  functions directly and never calls `CapabilityRegistry.from_plugin_modules()`
  at all, so it was never exposed to this). Worth naming as evidence that
  "which arm breaks first" in this framework version has as much to do
  with which internal code path each arm happens to exercise as with the
  research question itself.

**Phase 4, re-attempted 2026-09-19 at `smart-spatial-system==0.4.1`, real
output — the `bugs/002` crash is gone; a different, real failure showed
up on the very first LLM call:**

- Smoke test result: `success: False`, `latency: 71.8s`,
  `error_stage: execution`, `error: "Node n4_filter_attribute failed:
  filter_features() got an unexpected keyword argument 'attribute'"`.
  The LLM's plan used a `filter_attribute` operation with an `attribute`
  parameter; the real parameter name (per `op_catalog.py`'s
  `param_map`) is `where` (a structured field/op/value condition, e.g.
  `{"field": "amenity", "op": "eq", "value": "hospital"}` or the shortcut
  `{"amenity": "hospital"}` — read directly from
  `plugins/spatial_query_filter.py`).
- Root-caused to real 0.4.1 source, not guessed — full writeup:
  `bugs/003-planner-silently-passes-through-unknown-op-params.md`. Two
  compounding gaps: (1) `PlannerConfig.strict_params` defaults to
  `False`, and `s3geo.query()` never overrides it, so an unrecognized
  parameter name is passed straight through to the plugin function
  instead of being rejected at planning time with a clear error — every
  test in the framework's own suite that builds a `PlannerConfig` passes
  `strict_params=True` explicitly, so this is the same "precedent exists
  elsewhere, `s3geo` just didn't follow it" shape as `bugs/002`. (2) the
  LLM prompt has an `_op_input_roles_reference()` that auto-generates the
  *input-role* section of the system prompt from `OP_CATALOG` — its own
  docstring explains it exists because a hand-written example
  (`distance_to`) once went undocumented and the LLM kept getting it
  wrong — but there is no equivalent auto-generated reference for
  *parameters*, so `filter_attribute`'s real param names are exactly as
  undocumented to the LLM today as `distance_to`'s inputs were before
  that fix was made.
- **Not treated as a Phase 4 blocker**, unlike `bugs/002`: this is one
  LLM plan choosing a wrong parameter name on one operation, not a crash
  on every call regardless of content, and `run_once()` already records
  it faithfully (`error_stage="execution"`) without crashing the
  notebook — exactly the kind of outcome the N=20 batch exists to
  characterize the rate of. No local mitigation is available (`s3geo.query()`
  doesn't expose `strict_params` the way it now exposes `tolerant`), so
  none was applied.
- **Fixed upstream in `smart_spatial_system` 0.4.2** (2026-09-19),
  confirmed by cloning the tag and reading the diff directly, not taken
  on the fixer's word: `s3geo.query()` now takes `strict_params: bool =
  True` and builds `DeterministicPlanner(PlannerConfig(strict_params=strict_params))`;
  `llm_spec_generator.py` gained `_op_param_reference()`, generated from
  `OP_CATALOG`'s `param_map` the same way `_op_input_roles_reference()`
  already was, wired into the system prompt. Both halves of the proposed
  fix, exactly as asked. This repo's pin moved `==0.4.1` → `==0.4.2`;
  `bugs/003`'s "Resolution" section has the full diff.
- **Re-run at 0.4.2, real output, confirms the `bugs/003` fix is
  genuinely working** — the error changed in kind, not just wording:
  `success: False`, `latency: 126.6s`, `error_stage: execution`,
  `error: "Node underserved_mahalle failed: where must be a dict/object
  or None."` The LLM's plan now uses the *correct* parameter name
  (`where`) for `filter_attribute` — proof `_op_param_reference()` is
  doing its job — but the *value* it supplied for `where` isn't a dict,
  which `plugins/spatial_query_filter.py::_eval_where` correctly
  rejects. New finding, `bugs/004-where-clause-shape-never-taught-to-llm.md`:
  `where`'s real shape (`{field, op, value}` conditions, `and`/`or`/`not`
  combinators, or a shortcut dict — all documented in `_eval_where`'s own
  docstring) is never surfaced to the LLM anywhere — `_op_param_reference()`
  lists only parameter *keys*, not *value shapes*, for any structured
  parameter. Same underlying class of gap as `bugs/003`, one level
  deeper. Not a blocker, same reasoning as `bugs/003` — this is exactly
  the kind of per-run outcome the N=20 batch exists to characterize.

**Performance finding (2026-09-19), separate from the correctness bugs
above — no `bugs/` report, since nothing was wrong, just slow:** the
first N=20 batch run took ~120-130s per `s3geo.query()` call. Checked
`OpenAICompatibleLLMClient.complete()` first (one HTTP call, no retries
— confirmed from source, ruling out the LLM client itself looping) before
suspecting the plan's likely `spatial_nearest` step. Read
`plugins/nearest_neighbor.py::find_nearest_neighbors` directly: a plain
nested loop, one `_calculate_distance()` call per source×target pair, no
spatial index anywhere in the file (grepped for `STRtree`/`cKDTree`/
`sjoin_nearest` — zero matches) — brute-force O(964 × 1020) ≈ 984,000
pairwise calls for this study's dataset alone. Reported directly
upstream (not as a `bugs/` file here, since it's a performance
characteristic, not a wrong or silently-wrong result) with the exact
line numbers and a proposed `STRtree`-based fix preserving every
existing parameter/output contract. **Fixed in `smart_spatial_system`
0.5.0**, confirmed by cloning the tag and reading the diff directly:
`_strtree_candidates()` added, shapely-only (the `engine="python"` path
and any shapely-unavailable case still use the untouched original nested
loop), a correctness-equivalence test
(`tests/test_nearest_neighbor_strtree_performance.py`) compares the two
paths on varied geometries and asserts identical output, and a
performance benchmark reports ~33s → ~0.2s (~150x) on a synthetic
1000×1000 dataset and ~32s → ~0.2s on this study's own 964×1020 shape
specifically. Pin bumped `==0.4.2` → `==0.5.0`. Worth noting for the
paper's methodology section: Arm 2's `latency_s` field (recorded per run
in `results/llm_runs/`) captures whichever pinned version was installed
at the time — runs from before this bump reflect the pre-STRtree
implementation, so latency shouldn't be compared across the pin bump
without noting it.

**Phase 4, real N=20 batch, executed 2026-09-19 at `smart-spatial-system==0.5.0`
(`notebooks/03_llm_arm.ipynb` §5, `results/llm_runs/manifest.csv` +
`run_00.json`–`run_19.json`) — this is the arm's actual headline result,
not a smoke test:**

- **Speed confirms the 0.5.0 fix empirically, independent of the
  benchmark claim above**: every one of the 20 real calls in this batch
  completed in 10–20s, versus ~120–130s per call in the pre-0.5.0 smoke
  tests above — the same dataset, the same kind of query, on the author's
  own machine, not a synthetic benchmark.
- **1 success / 19 failures across exactly three distinct causes** (every
  run classified by reading its `success`/`error_stage`/`error` fields
  directly, none left unclassified):
  - **15/20** — `Node underserved_mahalle failed: where must be a
    dict/object or None.` — `bugs/004` (`filter_attribute`'s `where`
    shape never taught to the LLM). By far the dominant failure mode.
  - **2/20** (`run_02`, `run_12`) — `Node mahalle_enriched failed:
    rules[0].target is required.` — a second, independent operation
    (`enrich_feature_properties`) hitting the identical class of gap as
    `bugs/004` (structured parameter *value* shape never surfaced to the
    LLM, only the key name is). Folded into `bugs/004` as corroborating
    evidence rather than filed separately, since it's the same root cause
    in a different operation, not a new mechanism.
  - **2/20** (`run_03`, `run_11`) — `Node underserved_mahalle failed:
    filter_features() got an unexpected keyword argument 'geometry_type'`.
    New bug, filed separately as `bugs/005-filter-attribute-geometry-type-param-map-typo.md`,
    because unlike every other Phase 4 bug this one is **not an LLM
    mistake**: `op_catalog.py`'s own `param_map` maps `filter_attribute`'s
    `geometry_type` param to a target name that doesn't exist on
    `filter_features()` (the real keyword is `geometry_types`, plural) —
    the LLM used exactly the parameter name `_op_param_reference()`
    advertised, and `strict_params=True` didn't catch it either, because
    `geometry_type` genuinely is a valid key in the (internally wrong)
    catalog. A perfectly-behaving model fails 100% of the time it touches
    this parameter. **Fixed upstream in `smart_spatial_system` 0.5.1**
    (2026-09-19, commit `c97929c`, confirmed by cloning tag `v0.5.1` and
    reading the diff directly): the one-line `param_map` correction plus
    a new `inspect.signature()`-based test
    (`tests/test_op_catalog_param_map_signatures.py`) that checks the
    same class of drift across the entire catalog at once — which, run
    against the rest of `OP_CATALOG`, immediately found and fixed four
    more dead/broken params on other operations (`query_database`/
    `load_postgis_layer`, `inspect_vector`, `summarize_vector`,
    `display_vector` — see `bugs/005`'s "Resolution" for the full list).
    Pin bumped `==0.5.0` → `==0.5.1`; full detail in
    `bugs/005-filter-attribute-geometry-type-param-map-typo.md`. Not yet
    re-run against the N=20 batch — the breakdown above is still the
    0.5.0 batch's real numbers.
  - **1/20** (`run_06`) — the only `success: True`.
- **The one success is not a real answer to this study's research
  question**, on two independent grounds — worth foregrounding in the
  paper's discussion as the most important Phase 4 finding, more than the
  bug count itself:
  1. **Wrong CRS.** The LLM's plan reprojected both layers to
     `EPSG:31256` before computing nearest-neighbor distances.
     `EPSG:31256` is **"MGI / Austria GK East"** — an Austrian/Vienna-area
     projected CRS (confirmed via web search, not assumed), geographically
     meaningless for İstanbul. The framework's own generation-time
     validator, `_validate_distance_op_crs_symmetry`, passed — because it
     only checks that *both* layers are transformed to the *same* target
     CRS (symmetric), never that the CRS is geographically appropriate for
     the data's actual location. So `run_06`'s reported distances (min
     0.0, max 4982.82, over 912 matched mahalle out of 964, 52 unmatched
     at `max_distance=5000.0`) are internally consistent, pass every
     existing framework check, and are still not meters-in-İstanbul —
     they're whatever Austria GK East's linear unit means applied to
     İstanbul coordinates. A silent-wrong-answer failure mode, not a
     crash — worse for a research pipeline than any of the loud failures
     above, and notably: this study's own Vienna-sibling-repo history
     (referenced elsewhere in this repo's STUDY_LOG.md) was specifically
     about catching CRS mismatches, and this is a new variant of exactly
     that risk class showing up in the LLM arm.
  2. **Incomplete task even on its own terms.** `run_06`'s plan has only
     three operations — `crs_transform`, `crs_transform`, `spatial_nearest`
     — and stops there. It computes nearest-hospital distance per mahalle
     and nothing else: no `filter_attribute`/threshold step, no
     underserved/served classification. The LLM's one "successful" plan
     never actually answers "which mahalle are underserved" — it answers
     a strict subset of that question (distances only) and calls it done.
  - **Net effect: Arm 2's real success rate at fully and correctly
    answering this study's research question over this N=20 batch is
    0/20**, not 1/20 — the nominal success is both geographically wrong
    and functionally incomplete. This is itself a legitimate, reportable
    result for the paper's comparison against Arm 1's clean 207/964 at
    the 2000 m threshold (Phase 3 above): it characterizes how an
    LLM-planned pipeline fails, not just how often.
- **Practical implication for Phase 4's remaining scope**: the planned
  `04_comparison_metric.ipynb` numeric comparison against Arm 1 needs
  rethinking in light of this — there is no Arm-2 "underserved mahalle"
  list to compare yet at this pin. Options to weigh before Phase 5: (a)
  re-run the N=20 batch once `bugs/004`/`bugs/005` are fixed upstream and
  see whether the success rate and task-completeness improve; (b) treat
  the 0/20 full-success rate itself as Arm 2's headline result and design
  the comparison metric around failure-mode characterization rather than
  a mahalle-list diff; (c) some combination — report both the
  current-pin failure characterization and a later re-run once the
  upstream fixes land. Not decided yet; revisit when starting
  `04_comparison_metric.ipynb`.

**Phase 4, N=20 batch re-run 2026-09-20 at `smart-spatial-system==0.5.1`
(after `bugs/005` was fixed upstream) — went from 1/20 nominal success to
0/20, and surfaced a new bug:**

- All 20 real calls still completed fast (10-13s each), consistent with
  the 0.5.0 speed fix holding at 0.5.1.
- `bugs/005`'s fix worked as intended — **zero** runs hit the
  `geometry_type`/`geometry_types` `TypeError` this time (was 2/20 at
  0.5.0). But the batch still produced **0 successes / 20 failures**,
  across only two causes this time:
  - **12/20** — `bugs/004` (`where must be a dict/object or None.`),
    still the dominant failure mode. **Fixed upstream in
    `smart_spatial_system` 0.5.3** (2026-09-23, commit `078d552`,
    confirmed by cloning tag `v0.5.3` and reading the actual diff, plus
    independently executing every `where`/`rules` example the fix adds
    against the real plugins in this session): a new maintained table
    (`orchestrator/planning/op_param_shapes.py`) adds worked JSON
    examples for every structured `OP_CATALOG` param — not just
    `where`/`rules` — to the system prompt, with a test that runs each
    example through the real plugins so a wrong example can't teach the
    model something false; plus a generation-time validator that rejects
    a mis-shaped `where`/`rules` with a clear error instead of failing at
    execution. Upstream flags this was **not spot-checked against a live
    LLM** — a fresh N=20 batch at 0.5.3 is the real measure of whether
    the model's success rate actually improves. Pin bumped `==0.5.2` →
    `==0.5.3`; full detail in `bugs/004`'s "Resolution". With this, all
    three Phase 4 bugs found in this study (`bugs/004`, `005`, `006`)
    are fixed upstream — not yet re-run against a fresh N=20 batch.
  - **8/20**, plus the smoke test — a **new** error, never seen in the
    0.5.0 batch: `Node underserved_mahalle failed: sort_order must be a
    non-empty string.` Filed as
    `bugs/006-filter-attribute-sort-order-none-not-defaulted.md`. Root
    cause, read directly from `plugins/spatial_query_filter.py`:
    `filter_features`'s `sort_order` parameter has a real Python default
    (`"asc"`), but the LLM's plan (almost certainly) includes an explicit
    `"sort_order": null` for an operation that never asked to sort at
    all — and neither `planner.py::_map_params` nor
    `dag_executor.py::_build_kwargs` filters `None`-valued params before
    the plugin call, so the explicit `None` overrides the real default
    instead of triggering it. The sibling parameter `bbox_mode` has the
    identical shape and *does* tolerate an explicit `None` (via a
    `pick_first()` fallback already in the code) — `sort_order` skips
    that guard entirely, which reads as a plain oversight rather than
    deliberate strictness. Also affects `sort_limit`, a second
    `OP_CATALOG` operation bound to the same `filter_features`
    capability. **Fixed upstream in `smart_spatial_system` 0.5.2**
    (2026-09-23, commits `2c1cf2b` + a same-release follow-up `922383a`,
    confirmed by cloning tag `v0.5.2` and reading both diffs directly,
    plus re-executing this report's repro against the fixed source): the
    one-line `pick_first()` fix requested, plus the requested systematic
    fix at `dag_executor.py::_build_kwargs` — which, audited across the
    rest of `OP_CATALOG`, turned up several more instances of the same
    gap, some *silently* wrong rather than loud (`rank_features.descending`,
    `filter_points_in_polygon.drop_outside`, and others), all fixed in
    the same change. The follow-up commit caught and corrected an
    overreach in the first pass that would have silently broken
    `calculate_attribute_statistics(precision=None)`. Pin bumped
    `==0.5.1` → `==0.5.2`; full detail in `bugs/006`'s "Resolution". Not
    yet re-run against a fresh N=20 batch — the breakdown above is still
    the 0.5.1 batch's real numbers.
  - This is **not evidence of a 0.5.1 regression** — nothing in the
    0.5.1 diff touches `sort_order` (checked directly against the
    commit). It's a pre-existing bug this run's particular sample of LLM
    plans happened to trigger where the 0.5.0 batch's sample didn't
    (same non-zero-temperature model, no fixed seed, so plan-to-plan
    variation across otherwise-identical calls is expected) — worth
    remembering for the paper's methodology section: **a single N=20
    batch is a sample, not the full error-rate distribution**; a re-run
    can look meaningfully different from the last one even at a fixed
    pin, simply from which specific plans the model happens to produce.
- **Net effect on the study**: between `bugs/004` and `bugs/006`, this
  batch's real full-and-correct success rate is 0/20 — worse on paper
  than the 0.5.0 batch's nominal 1/20, but the two numbers aren't really
  comparable, since that one "success" was already established above as
  not a valid answer either (wrong CRS, incomplete task). Both batches
  ultimately support the same underlying finding: this study's query
  reliably fails to produce a valid Arm-2 answer at every pin tried so
  far. Not yet decided whether to keep both batches' raw data for the
  paper's failure-mode characterization or treat only the most recent
  one as canonical — revisit alongside the `04_comparison_metric.ipynb`
  decision above.

### N=20 batch re-run at 0.5.3 (2026-09-23) — structural success rate jumps from 0/20 to 18/20, but the research question still isn't validly answered even once

All three Phase 4 bugs (`bugs/004`, `005`, `006`) are fixed upstream at
this pin. This is the first batch to test that in bulk, and the
structural picture genuinely transformed:

- **18/20 runs now complete execution successfully** (`run_00`, `run_03`
  through `run_19` except `run_01`/`run_02`), up from 0/20 at 0.5.1/0.5.2
  and 1/20 (itself invalid) at 0.5.0. Latencies 10.9-15.3s/call, in line
  with the 0.5.0 STRtree fix still holding — no regression there.
- **2/20 failures (`run_01`, `run_02`) are not a framework bug at all.**
  Both fail at `error_stage="generation"` with `LLM HTTP error 401:
  Incorrect API key provided` from the external LLM provider
  (`avalai.ir`) — an authentication/billing issue on the study's own API
  key, unrelated to `smart_spatial_system`. No `bugs/` report; flagged to
  the study author to check the AvalAI key/billing before the next batch,
  so the next run can be a cleaner N=20 (or N=22, re-including these two).
- **Every one of the 18 successful runs now includes a `filter_attribute`
  threshold step** (`crs_transform` × 2, `spatial_nearest`,
  `filter_attribute`, and `run_13` additionally inserts
  `enrich_feature_properties`) — a qualitative change from every prior
  batch, where the one 0.5.0 "success" stopped after `spatial_nearest`
  and never filtered at all. The `where`-shape guidance from `bugs/004`'s
  fix is doing exactly what it was meant to: the model now reliably
  reaches and completes the filter step instead of crashing on it.
- **The CRS problem is still open and still 100% present.** All 18
  directly-checkable successful runs reproject to **`EPSG:31256`**
  ("MGI / Austria GK East") — the same geographically-wrong CRS as the
  single 0.5.0 "success," now the model's unanimous choice across every
  batch run so far, at every pin. None of the three fixed bugs touch this
  — it's a planning-choice the framework's own
  `_validate_distance_op_crs_symmetry` validator was never designed to
  catch (it only checks that both layers share the *same* target CRS,
  never that the CRS is geographically appropriate for the data). This
  remains a paper Finding, not a `bugs/` report: the framework behaves
  exactly as documented — nothing rejects a syntactically valid but
  geographically nonsensical CRS choice, and that's arguably correct
  framework behavior, not a bug, for a general-purpose spatial tool.
- **New pattern found, only visible now that runs get this far: a
  self-defeating `max_distance` + `where` combination silently zeroes
  out 8 of the 18 successful runs.** Cross-referencing each run's
  `nearest_neighbor.max_distance` parameter against its
  `filter_attribute` step's match count, re-verified directly against
  the raw JSON (not just the earlier quick pass):

  | pattern | runs | `nearest_neighbor.max_distance` | mahalle reaching the filter step | filter result |
  |---|---|---|---|---|
  | self-defeating | `run_00`,`03`,`06`,`08`,`12`,`14`,`15` (7, confirmed) + `run_13` (1, strongly implied — see note) | `5000.0` | 912 of 964 | **0 matches, every time** |
  | real result | `run_04`,`05`,`07`,`09`,`10`,`11`,`16`,`17`,`18`,`19` (10) | uncapped (`None`) | 964 of 964 | 128-166 matches |

  `run_13` is the one exception where `max_distance` can't be read
  directly: it's the single run whose plan inserts an
  `enrich_feature_properties` step between `spatial_nearest` and
  `filter_attribute`, and that step's metadata doesn't forward the
  `nearest_neighbor` metadata chain, so the value itself isn't visible
  in the output JSON. Circumstantial evidence still points the same way:
  `run_13`'s filter step receives exactly 912 candidate mahalle — the
  same count as every one of the 7 confirmed-capped runs, versus 964 for
  every uncapped run — which is hard to explain any other way than the
  same `max_distance=5000.0` cap. Treat it as "very likely the 8th
  self-defeating run" rather than fully confirmed when writing this up.

  The 7-8 self-defeating runs set `nearest_neighbor.max_distance=5000.0`
  in the neighbor-search step itself, which discards any mahalle whose
  nearest hospital is farther than 5000m **before** the plan's own
  downstream distance threshold (via `filter_attribute.where`) ever gets
  a chance to run. The exact logical mechanism by which this yields
  *zero* rather than *fewer* matches (rather than, say, everything
  within 5000m still passing a `> 1000m` threshold) needs a closer read
  of each run's actual `where` clause text before the paper states it
  with full confidence — recorded here as an open detail, not yet
  resolved — but the *symptom* is unambiguous and reproduced 7-8/7-8
  times: those runs produce zero underserved mahalle, silently, with no
  error. The other 10/18 runs leave `max_distance` uncapped and produce
  genuinely non-trivial result sets: `run_04` matches 166 mahalle with
  `distance_to_hospital` ranging 1025.5-14076.9m (implying an effective
  ~1000m threshold), `run_11` matches 128 with range 2027.0-14076.9m
  (implying ~2000m) — different runs land on different implicit
  thresholds, unlike Arm 1's three fixed, reproducible cutoffs
  (1000/1500/2000m exactly).

  Like the CRS issue, this is a **paper Finding, not a `bugs/` report**:
  both `nearest_neighbor.max_distance` and `filter_attribute.where` work
  exactly as documented individually; the failure is an LLM planning
  choice combining two individually-correct parameters into a plan that
  can never produce a match, and the framework has no way to know a
  given combination is self-defeating without understanding the
  semantics of both parameters together — reasonably out of scope for a
  generic capability-composition validator.
- **Net effect on the study, honestly stated**: this batch is real,
  substantial structural progress — all three known Phase 4 bugs are
  confirmed fixed in bulk (18/20 structural completions, up from 0/20),
  and the model now reliably reaches and completes a full plan including
  the filter step. But **the count of fully valid, research-question-
  answering Arm-2 runs in this batch is still 0/20** — every single
  successful run uses the wrong CRS, and 8 of the 18 additionally produce
  a silently empty (zero-match) result from the max_distance/where
  interaction. This is worth stating plainly in the paper's limitations:
  fixing every reproducible framework bug this study found did not, by
  itself, produce a single valid answer to the study's own question —
  the remaining gap is in LLM planning-choice quality, not framework
  correctness, and that distinction is itself one of this paper's
  findings about LLM-orchestrated geospatial pipelines.
- **Response to the two open findings above (decided 2026-09-23, both
  in parallel):**
  - An **upstream enhancement request** was drafted and is ready to send:
    `enhancements/001-crs-appropriateness-and-max-distance-where-
    composition-warnings.md`. Framed explicitly as opt-in warnings, not
    behavior changes, and grounded in code that already does something
    structurally identical — `nearest_neighbor.py`'s existing
    `warn_if_geographic_crs` check populates a `warning` metadata field
    when a layer is still in a geographic CRS; the request asks for that
    same field to also fire when a *projected* CRS's own `area_of_use`
    (a real `pyproj` property, no hardcoded region list) doesn't cover
    the input data, and for the `unmatched_source_count`/
    `dropped_unmatched_count` fields `nearest_neighbor` already computes
    to populate that field when `max_distance` discards enough sources to
    make a downstream filter look suspicious. Not yet sent to the
    upstream-write session; not yet reflected in any pin.
  - A **local mitigation** was added: `notebooks/03_llm_arm.ipynb`
    §7 ("Mitigated comparison batch") gives `run_once()` an optional
    `system_hints` pass-through (the mechanism `s3geo.query()` already
    exposes for exactly this — confirmed in `v0.5.3` source, and the
    same one `examples/urmia_real_estate_ranking.py` uses upstream) and
    runs a second N=20 batch with two general hints (the correct
    projected CRS for the study's region, and a general warning against
    letting `max_distance` pre-empt a downstream distance filter) written
    to `results/llm_runs_mitigated/` — kept **alongside** the canonical
    unmitigated `results/llm_runs/`, not replacing it. The gap between
    the two batches (how much of Arm 2's failure a few sentences of
    domain grounding closes, versus how much needs an actual framework
    change) becomes its own paper finding once both batches exist.
    §7's own markdown repeats the staleness caveat `bugs/004`'s "Local
    mitigation" section first raised: a hand-written hint is only as
    good as it is current, and needs re-checking against every future
    pin bump. **Not yet run** — requires a real LLM batch on the study
    author's own machine; this session only edited the notebook.

### `enhancements/001` landed same-day — pin bumped to 0.5.4 (2026-09-23), and the actual root cause of the CRS problem is now fixed, not just warned about

Confirmed for real: cloned `github.com/arazshah/smart_spatial_system`
fresh, checked out tag `v0.5.4`, read `CHANGELOG.md` and the actual diff
directly, and independently executed the parts of the fix that don't
need `pyproj` (unavailable in this session's own sandbox — PyPI stays
blocked here, the same standing constraint noted since Phase 4 began)
against the real cloned source. Full detail in `enhancements/001`'s own
"Resolution" section; summarized here for the paper's narrative:

- **Both requested warnings shipped**, wired into `nearest_neighbor.py`'s
  existing `warning` metadata field (multiple warnings now join with
  `" | "`): a CRS-area-of-use check (`pyproj.CRS(...).area_of_use`
  against the data's recovered centroid, no hardcoded region list) and a
  `max_distance`-exclusion check (a new `max_distance_excluded_count`
  field, deliberately narrower than `unmatched_source_count` so a
  null-geometry source is never blamed on `max_distance` — the changelog
  notes an earlier, broader version of this check was "caught in review
  before merge"). The `max_distance` half was independently executed
  here against the real cloned source, reproducing all three of
  upstream's own new test cases exactly with their own fixtures; the CRS
  half could only be verified by diff-reading and upstream's own new test
  (which uses this study's exact scenario — real Istanbul coordinates
  reprojected to `EPSG:31256` — almost verbatim), since this sandbox
  can't install `pyproj`.
- **The more important find, which neither this study nor the original
  enhancement request had located**: upstream traced *why* every single
  successful run in every batch of this study — 0.5.0 through 0.5.3, 100%
  of the time — picked `EPSG:31256` specifically, rather than just "some
  wrong CRS." `_domain_guidance()` in `llm_spec_generator.py` used
  `EPSG:31256` as its own literal worked example for the `crs_transform`
  → distance-op pattern, with generic, city-unlabeled `sites`/`metro`
  placeholders. A model handed a different city's data had no signal
  telling it not to copy the example verbatim — which is exactly what it
  did, every time. This reframes the CRS finding for the paper: it was
  never really "the LLM makes poor geographic judgment calls" so much as
  "the LLM followed its instructions very precisely, and its instructions
  contained an un-labeled, uncaveated wrong answer." Fixed by replacing
  the literal code with a `<PROJECTED_CRS>` placeholder plus an explicit
  instruction never to reuse a CRS code from the prompt or a prior
  answer.
- **Explicitly not landed** (upstream's own "Not landed" note): the
  systematic generation-time validator that would catch a self-defeating
  `max_distance`+`where` combination *before* execution — only the
  after-the-fact metadata warning landed for that half. The LLM can still
  generate the pattern at 0.5.4; it's now loud instead of silent, but not
  prevented, and `s3geo.query()` has no multi-turn repair loop that would
  let the model see and react to its own warning before the call returns.

**Consequence for the next batch, methodologically important**: because
this is a fix to the actual root cause (not just an added warning), the
next N=20 batch should run the **canonical, unmitigated §5 first** —
before touching §7's `system_hints` mitigation — since a clean CRS result
with *no* local mitigation at all would mean the upstream fix alone
closed that half of the paper's open gap, which is the real test of
whether `enhancements/001` worked. The `max_distance` `system_hints` text
in §7 keeps its own value regardless (no preventive validator landed for
that half); its CRS-hint text may now be partly redundant given the
`_domain_guidance()` fix, which is itself worth a sentence in the paper —
a documented local mitigation whose motivating condition upstream removed
out from under it days later is a nice concrete illustration of exactly
the staleness risk `bugs/004`'s "Local mitigation" section first flagged.
Neither batch has been run yet at this pin.

### Both batches run at 0.5.4 (2026-09-23) — the mitigated batch is Arm 2's first genuinely valid result in this study; the unmitigated batch got structurally worse, for an explainable reason

Both `notebooks/03_llm_arm.ipynb` §5 (canonical, unmitigated) and §7
(`system_hints`-mitigated) ran a full N=20 at `smart-spatial-system==0.5.4`.
Analyzed directly from the staged raw JSON (not the manifest summary
alone, same discipline as every prior batch).

**Canonical §5 (no `system_hints`): 8/20 structural success — down from
18/20 at 0.5.3.** All 12 failures are a **new** failure mode, not seen at
any prior pin: `error_stage="execution"`, `"Invalid CRS transformation
EPSG:4326 -> <PROJECTED_CRS>: Invalid projection"` (9 runs) or `"->
EPSG:XXXX: Invalid projection"` (3 runs). This is the flip side of
`enhancements/001`'s CRS root-cause fix: `_domain_guidance()`'s worked
example now uses a `<PROJECTED_CRS>` placeholder instead of the literal
(wrong) `EPSG:31256`, and roughly 12/20 times the model copies the
placeholder token itself — or a lookalike `EPSG:XXXX` — instead of
substituting a real code. `crs_transform` rejects it loudly and
correctly; the gap is that this only surfaces one node into execution
instead of at generation time like every other "model produced something
structurally wrong" case. Drafted as `enhancements/002` §1.

- **Of the 8 that did succeed, 100% chose `EPSG:3857` (Web Mercator)** —
  not `EPSG:31256` anymore, so the literal copy-paste really is fixed.
  But `EPSG:3857`'s published area of use is essentially worldwide, so
  the brand-new `_crs_area_of_use_warning()` correctly stays silent for
  it — while Web Mercator is a well-known poor choice for real metric
  distance away from the equator: at Istanbul's ~41°N, its scale factor
  is `1/cos(41°) ≈ 1.325`, a **~32.5% distance inflation** versus true
  ground distance. One of the 8 (`run_16`) produced a non-empty result —
  56 "underserved" mahalle at reported distances 2013.6-4800.1m, which
  corrected for this distortion correspond to a real ~1519-3623m range.
  A new, subtler instance of the same underlying gap (CRS chosen for
  convenience, not appropriateness) that now **passes every warning
  currently shipped**. Drafted as `enhancements/002` §2.
- The other 7/8 successes produced **zero** results — 3 (`run_01/02/03`)
  via `max_distance=500.0` excluding 28% of sources (correctly triggers
  the new exclusion warning — a real case of the warning working exactly
  as designed, just not preventing the empty result), 4 + the smoke test
  via `max_distance=5000.0` excluding only 8.8% (under the 20% default,
  no warning, still zero results) — the self-defeating `max_distance`+
  `where` pattern from the 0.5.3 batch, still fully reproducible at 0.5.4.
- **Net: 1 of 20 canonical runs executed successfully AND produced a
  non-empty result — and even that one is on a distorted CRS.** Worse
  than 0.5.3's structural numbers (18/20 executed), better in spirit (12
  of the 19 non-ideal outcomes are now loud, specific execution failures
  instead of a single silently-wrong answer repeated 18 times) — worth
  stating plainly in the paper as a case where a real upstream fix,
  measured only by "did the plan finish executing," looks like a
  regression, and the raw success-rate number alone would mislead a
  reader who didn't look at *why* each run failed.

**Mitigated §7 (`system_hints`): 20/20 structural success, 20/20 correct
CRS, 18/20 identical reproducible answer — the first batch in this study
where Arm 2 produces something worth comparing to Arm 1.**

- **Every one of 20 runs chose `EPSG:32635`** — the exact UTM zone Arm 1
  itself uses — with `warning: null` on every one. The `system_hints`
  text (stating the correct CRS explicitly) fully closed the gap that
  `enhancements/001`'s upstream fix alone did not.
- **18/20 produced an identical, reproducible result**: 166 underserved
  mahalle, `distance_to_nearest_hospital` ranging 1010.0-13895.6m on
  every checked run (`run_00`, `run_05`, `run_18` — byte-identical
  ranges). A min just above 1000m strongly implies the model
  consistently derived roughly Arm 1's own 1000m-tier threshold from the
  raw query's "too far away" phrasing — genuinely comparable to Arm 1 for
  the first time in this study, though the count itself (166) differs
  from Arm 1's 325-at-1000m (different distance methodology —
  centroid/boundary choice, rounding, or the 85 mahalle this run's
  `nearest_neighbor` step excludes for other reasons — worth a real
  investigation in `04_comparison_metric.ipynb`, not resolved here).
  **Resolved in the 0.5.5 entry below:** it is the distance definition.
  Arm 1's headline counts use centroid distance, while Arm 2 measures
  polygon-to-point distance. Against Arm 1's own `dist_boundary_m` column,
  166 is exactly Arm 1's count above 1000 m.
- **The other 2/20 (`run_08`, `run_19`) reproduce the exact
  self-defeating `max_distance`+`where` pattern — precisely at the new
  warning's blind spot.** Both set `max_distance=1000.0`, which excludes
  **exactly 166 of 964 sources (17.2%)** — under the shipped
  `max_distance_warning_fraction` default of `0.2`, so no warning fires
  — while the downstream filter needed exactly those 166 excluded
  sources to produce any result. Zero output, silently. This is sharp,
  concrete evidence for a point `enhancements/001` raised more abstractly
  when its systematic validator wasn't landed: **a fraction-based
  threshold is structurally the wrong lens for this problem.** Whenever
  `max_distance` on a field and a downstream filter's numeric threshold
  on the *same* field satisfy `max_distance <= threshold`, the exclusion
  is *by construction* exactly the population the filter would have
  selected — no fraction can distinguish "some exclusions" from "the
  exact exclusions that mattered" without inspecting the downstream op.
  Filed as `enhancements/002` §3, explicitly asking upstream to
  reconsider the declined systematic check now that there's a concrete
  repro rather than a hypothetical.

**Net effect on the study, stated plainly**: for the first time since
Phase 4 began, this repository has an Arm 2 result — the 18/20 `§7`
outcomes — that is genuinely usable for comparison against Arm 1: correct
CRS, a real (if not yet reconciled) mahalle count, a threshold that looks
like it tracks the raw query's intent. It required a documented local
mitigation to get there, not the upstream fix alone — worth being exactly
that honest in the paper: `smart_spatial_system`'s own fixes closed the
*bug* class of failures (004/005/006) and materially improved the
*framework's* behavior (0.5.4's warnings correctly fire wherever they're
designed to), but a fully naive, hint-free prompt at the best available
pin still does not reliably produce a valid answer to this study's
question — `system_hints`, a few sentences of domain grounding this study
had to write itself, was still the deciding factor. `enhancements/002`
(finalized 2026-09-23 and handed to the author to send upstream) asks
upstream to close the two newest gaps (placeholder-copy crashes,
Web-Mercator-passes-every-check) — and, as its main ask, to remove the
reason both happen. Checked in `v0.5.4` source before writing it:
`s3geo.query()` holds the input layers (`initial_inputs`) before it calls
the LLM, yet never tells the LLM where the data is; the model infers the
location from the user's wording alone and then has to recall a suitable
CRS from memory. The request is for the framework to compute a suitable
projected CRS from the data's own extent and state it to the planner —
deterministic, per query, no fixed example to copy. For the paper this is
the cleanest statement of the whole CRS thread: three rounds of
prompt-level fixes (a wrong literal example, then a placeholder, then
local `system_hints`) versus one data-level fix that makes the correct
choice a property of the framework rather than of the prompt. The
request deliberately contains no example CRS codes, for two reasons this
study's own data supplies: an example code was 0.5.4's root cause, and
an Istanbul code in the framework's generic prompt would leak this
study's answer into its unmitigated arm.

### `enhancements/002` landed in full (0.5.5, 2026-09-23): the CRS now comes from the data, not the prompt

All four items shipped same-day in `smart-spatial-system==0.5.5` and were
confirmed against the real `v0.5.4..v0.5.5` diff. Details and the exact
verification are in `enhancements/002`'s "Resolution". What matters for
the paper:

- **The CRS thread's resolution is structural, not a better prompt.**
  `s3geo.query()` now computes the input layers' extent and the UTM zone
  of its centroid (only when one zone is accurate within 1% across the
  whole extent) and states it to the planner as a measured fact. On this
  study's own data the computed value is `EPSG:32635`. That is identical
  to Arm 1's CRS, which was chosen independently by hand in Phase 3.
  Verified by executing the module's pyproj-free logic on the real
  `data/raw/` files: both bbox edges fall in zone 35, max scale error
  ~0.04%. This is the cleanest data point in the study for the paper's
  central argument. Three rounds of prompt-level fixes each moved the
  failure somewhere else:
  1. a literal wrong example: 100% `EPSG:31256`;
  2. a placeholder: 60% crashes, 100% Web Mercator among the rest;
  3. local `system_hints`: correct, but only because this study wrote
     the answer in.

  The first fix that removes the model's need to *know* the answer
  deterministically hands it a fact the framework already had.
- **The self-defeating `max_distance` pattern is now rejected before
  execution** (`_validate_max_distance_filter_composition`). It inspects
  the downstream filter, which is exactly what a fraction threshold could
  not do, as this study's 17.2% repro showed.
- **Methodological note for the next batch's numbers.** `s3geo.query()`
  still has no retry. Plans the new generation-time validators reject
  are therefore still failed runs. They are now reported at the right
  stage with a precise reason. Before, they failed during execution
  (placeholder CRS) or counted as successes with zero features
  (self-defeating `max_distance`). A lower raw success count at 0.5.5
  would therefore not mean a regression. Classify every run by *why* it
  failed, as every batch here has been.
- **What the next batch is for.** §5 at 0.5.5 is the first unmitigated
  batch where the correct CRS reaches the planner from the framework, not
  from this study. If §5 now approaches §7, the local mitigation is no
  longer doing the work, and the framework is. That would be the result
  the author set out to test ("the framework must do its job correctly
  whatever the prompt"). The notebook now records the pin and the
  framework's computed extent on every run, and archives prior batches
  before overwriting them. Earlier batches had to be matched to a pin by
  file timestamps.

### Both batches run at 0.5.5 (2026-09-23): with no hints, 9/20 runs reproduce Arm 1 exactly; the remaining failures are all one pattern, `max_distance`

Both batches were run at `smart-spatial-system==0.5.5`. Every record
carries `smart_spatial_system_version: "0.5.5"`. §3b's pre-check, run on
the author's machine with real `pyproj`, printed the facts the framework
gives the planner: `EPSG:32635 (WGS 84 / UTM zone 35N)`, with a max scale
error of 0.0367%. That matches the 0.0364% this study predicted
analytically before the run, so `enhancements/002` item 1 is now
confirmed by execution, not only by reading the diff.

Each run was classified from its raw JSON. Each Arm 2 result was also
compared **set for set** against Arm 1's `results/rule_based_underserved.csv`
(by `osm_id` and distance).

**§5, no `system_hints` — the test this thread was aiming at:**

| outcome | runs | detail |
|---|---|---|
| **identical to Arm 1** (boundary distance > 1000 m) | 5 (`06, 09, 11, 14, 18`) + smoke | same 166 `osm_id`s as Arm 1, distances equal to 0.0 m |
| **identical to Arm 1** (boundary distance > 2000 m) | 4 (`01, 05, 08, 13`) | same 127 `osm_id`s as Arm 1, distances equal to 0.0 m |
| silently truncated | 1 (`07`) | `max_distance=5000` with a > ~3000 m filter kept 52 of Arm 1's 103, dropping the 51 **farthest** (up to 13.9 km) |
| rejected at generation (`enhancements/002` item 4) | 10 | `max_distance_m` of 5000 (8 runs), 1000 (1) or 500 (1) with a filter no feature could pass |

- **CRS: 11/11 executed plans used `EPSG:32635`, and 0/21 plans contained
  an unresolvable CRS.** At 0.5.4 the same no-hints arm had 12/20
  placeholder crashes and 8/8 Web Mercator. The CRS problem that ran
  through this whole study is gone in the unmitigated arm, and the
  framework itself is what removed it. `_crs_scale_distortion_warning`
  and the area-of-use warning stayed silent on every run, which is
  correct for this CRS (no false positives on real data).
- **"166 vs Arm 1's 325" was never a disagreement. It is a definitional
  difference.** Every Arm 2 plan in every batch measured distance from
  the mahalle *polygon* (the nearest-neighbor op's default on polygon
  input) and never from its centroid. Arm 1 computes both, and its
  headline numbers (325 / 251 / 207) use the centroid. Against Arm 1's
  boundary column the two arms agree exactly. The raw query deliberately
  leaves this choice open (see "Arm 2" above). For the paper this is a
  finding: across all runs the LLM arm never chose the centroid
  definition that Arm 1's headline uses. `04_comparison_metric.ipynb`
  must compare like with like (Arm 2 vs `dist_boundary_m`) and report
  the definitional gap separately.
- **The threshold is the LLM's own choice**, since the query gives none:
  1000 m in 5 runs and 2000 m in 4. Both happen to be Arm 1 tiers. This
  run-to-run variation is real Arm 2 behaviour for the comparison
  metric to measure. It is not an error.

**§7, with `system_hints` — now worse than §5.** All 20 plans plus the
smoke test set a `max_distance` cap, against 2/20 at 0.5.4.
- 10 were rejected at generation (`max_distance_m=1000` with a > 1000 m
  filter).
- The 10 that executed were all silently truncated:
  - 7 used a cap of 10,000 m with a > 5000 m filter. They kept 44 of
    Arm 1's 51, dropping the 7 farthest.
  - 3 used a cap of 5000 m with a > 3000 m filter, as in §5 run 07.
    They kept 52 of 103.

None matches Arm 1. The hint text tells the model *not* to cap
`max_distance` below the threshold. The data are consistent with that
sentence priming the model to use the parameter, but the records don't
contain the plans, so this cannot be shown directly. Either way, the
framework now supplies the CRS itself and rejects provably-empty
compositions, so §7's local mitigation no longer helps and at this pin
it measurably hurts. **Recommendation: retire §7 from future batches**
and keep the 0.5.4 §7 batch (archived) as the historical data point.

**What remains, and why it's a framework question rather than a prompt
question:**
1. **No repair step.** Half of each batch is lost to item 4
   rejections. Each rejection message already states the exact fix
   ("Remove max_distance ... and let the filter apply the threshold"),
   but `s3geo.query()` has no retry that would give the message back to
   the planner.
2. **Truncation is not caught.** `_validate_max_distance_filter_composition`
   rejects only *provably empty* plans. A cap above the threshold, with a
   downstream "farther than T" filter, returns {T < d ≤ M} instead of
   {d > T}. That silently drops exactly the farthest, most underserved
   mahalle, the ones this study's question is about. The 0.5.4 fraction
   warning doesn't fire either: the caps excluded 5.3% and 0.7% of
   sources, both under its 20% default.
3. **Plans aren't recorded.** `S3GeoResult` returns operation names but
   not the plan's params, so thresholds and where clauses have had to be
   inferred from output distances in five successive batches.

All three were drafted as `enhancements/003`. One observation is included
there as a question, not a claim: every rejected plan used the key
`max_distance_m`. Apart from the parameter list, that key appears in the
prompt only in the `filter_by_distance` worked example.

**Net for the study.** At 0.5.5, a plain natural-language question with
no study-supplied hints produces, in 9 of 20 runs, the same answer as
the hand-written rule-based pipeline, feature for feature, under the
same distance definition. Every remaining failure traces to a single
parameter pattern, and the framework already recognises it (10 of 11
cases), but it doesn't yet recover from it.

### `enhancements/003` landed (0.5.6, 2026-09-23) — both batches re-run, and for the first time every successful run matches Arm 1 exactly

All three items shipped in `smart-spatial-system==0.5.6`: one repair
attempt after a generation-time rejection (`max_repair_attempts=1`,
default on), a truncation check in
`_validate_max_distance_filter_composition()` (now *rejects* a capped
plan whose downstream filter would be silently truncated, not just the
provably-empty case 0.5.5 already caught), and `S3GeoResult` gaining
`query_spec`/`plan`/`generation_attempts` (with `.attempt_count`/
`.repaired` convenience properties) so a batch's records finally carry
the plan itself instead of requiring inference from output distances.
`notebooks/03_llm_arm.ipynb`'s `run_once()` was updated to capture all
three new fields before this batch ran (the pre-update run that same day
had already reached 20/20 on §5 but couldn't say whether any of them
needed the new repair step — recorded, not thrown away, in
`results/archive/`). Both §5 and §7 were then re-run at 0.5.6 and
analyzed from the raw JSON directly (`query_spec.operations`, not
inferred parameters) — first time this study has had the real plan to
read instead of reverse-engineering it from output distances.

**§5, no `system_hints`: 20/20 executed, and 20/20 are an exact set
match to Arm 1's boundary-distance set at the run's own chosen
threshold.** Every single successful run — not "structurally succeeded",
the actual `osm_id` set — is identical to Arm 1's `dist_boundary_m > T`
set, where `T` is whatever that run's own `filter_attribute` chose (19/20
chose 1000 m; one, `run_00`, chose 800 m and matches Arm 1's
boundary-800m set of 183 exactly). Every one of the 20 also has
`attempt_count=1, repaired=False` — the new repair mechanism was never
invoked, because every plan passed generation-time validation on the
first try. `max_distance` is `None` (uncapped) on all 20 — the
self-defeating-cap pattern that dominated every prior batch (0.5.3
through 0.5.5) does not appear once. All 20 used `EPSG:32635`. PAR =
20/20 = 1.0.

**§7, with `system_hints`: 18/20 executed — but the 2 "failures" are not
planning failures.** `run_14` and `run_18` both raised
`LLMSpecGenerationError` wrapping a raw `HTTP 401 "Incorrect API key
provided"` from `avalai.ir`, 47 seconds apart, `attempt_count=0` on both
(zero parseable LLM responses, not a rejected plan) — a transient
provider-side auth blip, unrelated to `system_hints` content or to
anything `smart_spatial_system` does. **Of the 18 that actually reached
the LLM, all 18 are an exact match to Arm 1's boundary-1000m set**,
`attempt_count=1, repaired=False` on every one, `max_distance=None` on
every one, `EPSG:32635` on every one — the same clean result as §5, not a
different one. PAR = 18/20 = 0.900 (18/18 = 1.0 among runs that actually
got an LLM response).

**The headline result for the paper: as of 0.5.6, `system_hints` no
longer measurably helps.** §5 (no hints) and §7 (with hints) now produce
statistically the same outcome — both effectively 100% exact-match once
the unmitigated batch's own first-attempt cleanliness and the mitigated
batch's unrelated auth blip are accounted for. This is exactly what the
0.5.5 entry's "what the next batch is for" note predicted ("if §5 now
approaches §7, the local mitigation is no longer doing the work, and the
framework is") — now measured, not projected. **`system_hints` should be
retired from future batches for real** (the 0.5.5 entry recommended
retiring it already on weaker grounds; this batch removes the remaining
justification for keeping it as anything but a historical/ablation data
point).

- **Byte-identical distances across every run, in both batches, whatever
  property name the LLM gave the distance field.** Confirmed directly:
  `run_01`'s `distance_to_hospital` values equal `run_15`'s
  `distance_to_hospital` values equal §7 `run_05`'s, feature for feature,
  and equal `run_00`'s default-named `_nearest_distance` values on their
  166-mahalle overlap. 19/20 canonical runs named the field
  `distance_to_hospital`; `run_00` left it at the framework default. Pure
  LLM-side naming variance with zero effect on the actual computation —
  worth a sentence in the paper's Method as a concrete example of "plan
  agreement" surviving cosmetic parameter variation.
- **`04_comparison_metric.ipynb` (Phase 5) now exists**, built and
  executed against these two real batches plus
  `results/rule_based_underserved.csv`. Confirms the above independently
  through `paper/comparison_metric.md`'s own layers: PAR 1.0 / 0.9, Set
  Stability 0.9907±0.0279 (canonical — pulled down only by `run_00`'s
  different self-chosen threshold, not disagreement) / 1.0000±0.0000
  (mitigated), Jaccard vs. Arm 1 = 1.0000 for both, threshold 990±44 /
  1000±0. One real gap found while building it, not glossed over:
  **Layer 3b (Rank Stability) can only be computed over each pair's
  shared *output* osm_ids, not the full 964 mahalle the metric spec
  calls for** — `s3geo.query()`'s single `result.output` never surfaces
  the unfiltered per-mahalle distance node every plan's `QuerySpec` also
  declares (`mahalle_with_distance`), only the final filtered one. The
  partial ρ = 1.0000 on the shared subset is a real number, just not the
  full-population one; see the notebook's own "Limitations" section
  (§10) and the "Open decisions" / "Findings" items below.

**Net for the study.** The thread that ran through every batch since
0.5.3 — LLM-chosen CRS, then the self-defeating `max_distance` pattern —
is now closed on both counts, by the framework alone, without this
study's own `system_hints`. This is the first pin at which Arm 2, asked
the bare research question with no study-supplied hints, reproduces Arm
1's answer exactly, every time. That is a genuinely strong, directly
citable Results-section finding — worth being equally direct in the
paper about the two things this success does *not* yet cover: the
boundary-vs-centroid distance definition (Arm 2 never chose the centroid
definition Arm 1's headline numbers use, across every batch in this
study — a live finding, not resolved by 0.5.6) and the still-partial
Rank Stability measurement above.

### Phase 6 landed (2026-09-24) — map, figures, and the study closed out

**`notebooks/05_results.ipynb` built, tested against real committed data,
and committed** — the last notebook `paper/PLAN.md`'s Phases table names.
Reads only already-committed files (`results/rule_based_underserved.csv`,
`results/underserved_regions.geojson`, `data/processed/mahalle.geojson`,
`data/processed/hospitals.geojson`, `results/llm_runs*/`,
`results/metrics.csv`); calls neither the LLM nor `s3geo.query()` again.
Validated the same way `04_comparison_metric.ipynb` was: executed via
`jupyter nbconvert --execute` against the real staged repo data in an
isolated sandbox before committing (un-executed, per this repo's own
convention that each notebook is run for real by the paper author).

Three figures, each written to `results/figures/`, and each cross-checked
against numbers already committed elsewhere in this file and in
`paper/paper.md` — no new number appears here that wasn't already derived
from a committed result file:

- **`fig1_underserved_map.png`** — Arm 1's own headline answer: 207 of 964
  mahalle (21.5%) underserved at the 2,000 m centroid-distance threshold,
  the 6-piece dissolved region outlined, 1,020 hospitals/clinics plotted
  for context. This is the map for the paper's core answer, not a
  comparison figure.
- **`fig2_threshold_sensitivity.png`** — underserved count vs. threshold
  (800/1000/1500/2000 m) for both of Arm 1's distance definitions
  (boundary: 183/166/142/127; centroid: 389/325/251/207 — the 800 m and
  1500 m boundary/centroid figures hadn't been stated together in one
  place before this notebook), with every successful Arm 2 run (20
  canonical + 18 mitigated) overlaid as a scatter point at its own
  self-chosen (threshold, output-set-size) pair. Every single point lands
  exactly on the boundary-distance line, not scattered near it — a visual
  restatement of the exact-match finding, derived independently of the
  comparison-metric notebook's own Jaccard computation.
- **`fig3_arm_disagreement.png`** — deliberately *not* an Arm 1 vs. Arm 2
  disagreement map, despite the filename `paper/PLAN.md`'s original
  "Expected outputs" section gave it (kept for continuity with that
  section rather than renamed). At Arm 2's modal 1,000 m threshold: 166
  mahalle boundary-underserved (exactly Arm 2's own output set), 159
  centroid-only-underserved (flagged by Arm 1's headline definition but
  not by Arm 2's), 639 served under both. This is the "166 vs 325" gap
  named in the 0.5.5 and 0.5.6 entries above, mapped for the first time —
  166 + 159 = 325, confirming the two counts are nested, not merely
  similar in size (boundary distance ≤ centroid distance for every
  mahalle, by construction, so the boundary-underserved set is always a
  subset of the centroid-underserved set at the same threshold — verified
  here, not assumed).

**`paper/paper.md` folded all three figures into a new Results
subsection** (referencing them by their committed `results/figures/`
paths) and the paper's "Status" line now reads "Phases 0–7 complete" —
no open placeholder remains. `paper/PLAN.md`'s own Phases table (above)
and `STUDY_LOG.md`'s "Current state" were updated in the same pass, and
`requirements.txt` finally got its "BUMPED 0.5.5 -> 0.5.6" comment block
(present at every earlier bump, missing since the 0.5.6 pin itself
landed) — closing the last of the small housekeeping gaps this study's
own tracking had flagged.

**What is genuinely still open, not closed by this phase**: the partial
Rank Stability measurement (needs a dedicated batch built around
`s3geo.query()`'s single-output limitation — not attempted here) and the
hospital-only re-run (still just a named, undecided item below). Neither
blocks the paper; both are named in Limitations as exactly that — open,
not silently dropped.

