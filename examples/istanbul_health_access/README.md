# Istanbul health access: a completed case study

**Which İstanbul *mahalle* (neighbourhoods) are underserved by hospitals and
clinics?** A finished, end-to-end study built on Smart Spatial System. It
answers the question twice and compares the two answers:

1. **Arm 1, rule-based.** The operation plan is written by hand: reproject to
   EPSG:32635, nearest facility per mahalle, threshold, dissolve.
2. **Arm 2, LLM-planned.** The same question goes to `s3geo.query()` in plain
   language, with no threshold, CRS or operation list. The planner chooses
   those itself. This arm ran N=20 times.

Real OpenStreetMap data: 964 mahalle and 1,020 hospitals/clinics.

The study was developed in its own repository,
[`arazshah/smart-spatial-istanbul-health-access`](https://github.com/arazshah/smart-spatial-istanbul-health-access),
and is copied here as a worked example of the whole system on real data.

## Result

At `smart-spatial-system==0.5.6`, **all 20 of 20 unhinted LLM-planned runs
return exactly the same set of mahalle as the rule-based arm**, measured by
the same distance definition and at the threshold each run chose. All 20
picked EPSG:32635, which the framework derived from the data's extent.

| | Rule-based (Arm 1) | LLM, no hints (Arm 2) | LLM, with hints (Arm 2) |
|---|---|---|---|
| Runs | 1 | 20 | 20 |
| Executed | 1.00 | 1.00 | 0.90 (2 × HTTP 401 from the provider) |
| Threshold | 2000 m (by design) | 990 ± 44 m (19 × 1000, 1 × 800) | 1000 ± 0 m |
| Jaccard vs. Arm 1 | 1.0 | 1.0000 | 1.0000 |
| Set stability | 1.0 | 0.9907 | 1.0000 |
| Median latency | – | 14.4 s | 14.3 s |

Source: `results/metrics.csv` and `paper/paper.md` §4.

Arm 1's headline is **207 of 964 mahalle underserved** at 2,000 m (251 at
1,500 m, 325 at 1,000 m). These counts use distance from the mahalle
centroid. The LLM plans measure distance from the polygon boundary, and they
find 166 at 1,000 m. That set matches Arm 1's boundary-distance column
exactly. The gap is a difference in definition, not a disagreement between
the arms. It is shown in `fig3_arm_disagreement.png`.

<p>
  <img src="results/figures/fig1_underserved_map.png" width="32%" alt="Underserved mahalle at 2000 m">
  <img src="results/figures/fig2_threshold_sensitivity.png" width="32%" alt="Underserved count vs. threshold">
  <img src="results/figures/fig3_arm_disagreement.png" width="32%" alt="Boundary vs. centroid distance at 1000 m">
</p>

## What it changed upstream

Arm 2 did not start out correct. Without hints, it failed to give the
right answer at every earlier pin, from 0.3.0 to 0.5.5. Each failure was
traced to a cause in this package. The study reported each one in `bugs/`
or `enhancements/`, and each was fixed here:

| Report | Problem | Fixed in |
|---|---|---|
| `bugs/001` | `VectorOut.from_geopandas()` crashed on OSM timestamp columns | `geochat-sdk` 1.0.1 |
| `bugs/002` | `s3geo.query()` crashed without raster extras (`rasterio`) installed | 0.4.1 |
| `bugs/003` | Planner silently passed through unknown op params | 0.4.2 |
| – | `nearest_neighbor` was O(n×m); now uses an STRtree (~150× faster) | 0.5.0 |
| `bugs/005` | `filter_attribute` `geometry_type` param_map typo | 0.5.1 |
| `bugs/006` | Explicit `None` overrode plugin defaults (`sort_order` and others) | 0.5.2 |
| `bugs/004` | The shape of the `where` and `rules` values was never shown to the LLM | 0.5.3 |
| `enhancements/001` | Prompt example leaked `EPSG:31256`; CRS area-of-use and `max_distance` warnings | 0.5.4 |
| `enhancements/002` | Projected CRS derived from the input data; target-CRS and distance-fidelity checks | 0.5.5 |
| `enhancements/003` | One repair retry, truncation check, plan returned in the result | 0.5.6 |

`STUDY_LOG.md` records each pin bump and each re-run batch in order.
`paper/PLAN.md` has the full "Findings" log.

## Run it

Run everything from this folder. The notebooks use paths relative to
`notebooks/`.

```bash
cd examples/istanbul_health_access
pip install -r requirements.txt     # pins smart-spatial-system==0.5.6
cp .env.example .env                # only notebook 03 (the LLM arm) needs a key
jupyter lab                         # open notebooks/01_… to 05_… and run them in order
```

- **Notebooks 04 and 05 need no downloads.** They read only the committed
  `results/` and `data/processed/`, so you can reproduce the metrics and
  figures without any data download or LLM key.
- **Notebooks 01–03 need `data/raw/`.** That is the raw Overpass output in
  EPSG:4326. It is not committed, because it can be downloaded again and is
  licensed under ODbL. Build it like this:

  ```bash
  python scripts/fetch_overpass.py
  python scripts/overpass_to_geojson.py centers   data/raw/hospitals.json          data/raw/hospitals.geojson
  python scripts/overpass_to_geojson.py relations data/raw/mahalle_boundaries.json data/raw/mahalle_boundaries.geojson
  ```

  `data/README.md` has the exact queries. If Overpass is blocked,
  `scripts/download_istanbul_data.md` has routes that avoid it. OSM is live
  data, so a new fetch can differ slightly from the study's (1,020
  facilities, 964 mahalle).
- **Notebook 03 calls a real LLM,** 2 × 20 times plus a smoke test. Running
  it overwrites `results/llm_runs*/`. Before overwriting, it copies the
  previous batch to `results/archive/`, which is git-ignored here.

## Layout

| Path | What |
|---|---|
| `notebooks/01_data_and_problem.ipynb` | Phase 2: load, clean and reproject; writes `data/processed/` |
| `notebooks/02_rule_based_arm.ipynb` | Phase 3: Arm 1, a hand-written plan through the plugins |
| `notebooks/03_llm_arm.ipynb` | Phase 4: Arm 2 with `s3geo.query()`, N=20 unhinted plus N=20 with `system_hints` |
| `notebooks/04_comparison_metric.ipynb` | Phase 5: PAR, set and rank stability, Jaccard vs. Arm 1; writes `results/metrics.csv` |
| `notebooks/05_results.ipynb` | Phase 6: the three figures |
| `paper/paper.md` | The write-up |
| `paper/PLAN.md` | Research question, phases, and the full findings log |
| `paper/comparison_metric.md` | Definition of the three-layer comparison metric |
| `bugs/`, `enhancements/` | The upstream reports listed above, each with a "Resolution" section |
| `STUDY_LOG.md` | The study's working log and analysis conventions (CRS, thresholds, distance semantics) |
| `data/README.md` | Area of interest, Overpass queries, CRS choice, licensing |
| `data/processed/` | `hospitals.geojson`, `mahalle.geojson` (EPSG:32635) |
| `scripts/` | Overpass fetch, and a converter to GeoJSON that needs only the standard library |
| `results/` | Arm 1 outputs, per-run Arm 2 records and manifests, `metrics.csv`, figures |

## What was left out, and what was trimmed

This folder holds about 15 MB. The study repository holds about 600 MB. The
difference:

- **`results/llm_runs*/run_*.json` are slimmed copies.** In each run's
  `output`, feature geometry is set to `null`. `_target_properties` (the
  nearest facility's full OSM tag set) is reduced to `osm_id`, `osm_type`,
  `name`, `amenity` and `healthcare`. Everything else is unchanged: the
  query spec, plan, generation attempts, latency, and every mahalle
  property, including `osm_id` and `_nearest_distance`. Every record says
  this in a `_slimmed` field. Notebooks 04 and 05 read only those fields,
  and give the same result on the slimmed files as on the originals. To get
  geometry, join on `osm_id` to `data/processed/mahalle.geojson`.
- **`results/archive/` is not included.** It holds earlier batches (0.5.4 and
  0.5.5) that were kept for the historical record. Their numbers are in
  `paper/PLAN.md` and `STUDY_LOG.md`, and the original files are in the
  study repository.
- **Agent scratch folders are not included** (`Claude outputs/`,
  `claude-notes/`).

Paths in the study's own documents, such as "this repo" or `results/archive/`,
refer to the original study repository.

## Sibling study

[`smart-spatial-vienna-accessibility`](https://github.com/arazshah/smart-spatial-vienna-accessibility)
uses the same two-arm design and comparison metric on a different city and
domain. See also the "Case Studies" section of the top-level `README.md`.

## License

Code: MIT (see `LICENSE`). Data: © OpenStreetMap contributors,
[ODbL](https://www.openstreetmap.org/copyright). See `data/README.md`.
