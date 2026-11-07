---
license: odbl
language:
  - fa
pretty_name: Persian GeoQuery (Tehran)
size_categories:
  - n<1K
task_categories:
  - text-generation
tags:
  - geospatial
  - geoai
  - persian
  - farsi
  - gis
  - benchmark
  - openstreetmap
  - tehran
---

# Persian GeoQuery (Tehran)

**A small benchmark for natural-language spatial questions asked in Persian.**
30 Persian questions about Tehran (metro stations, hospitals, malls, parks, main
roads), each with a rule-based reference answer computed independently of any
LLM. Built to evaluate [s3geo](https://s3geo.com) / Smart Spatial System
([GitHub](https://github.com/arazshah/smart_spatial_system)), but the
questions and references are usable with any system that answers spatial
questions over GeoJSON.

> **Status: v0.1, questions and tooling released; results not yet published.**
> No score is claimed here. Run the benchmark (below) and publish what you get.

## Why this exists

Spatial question answering is evaluated almost only in English. This benchmark
asks one narrow, checkable thing: *given a Persian question and the layers, does
the system return exactly the set of features a hand-written GIS computation
returns?* Answers are sets of OSM ids, so scoring needs no judgement.

## Contents

| Path | What |
|---|---|
| `questions.jsonl` | 30 questions: `id`, `level` (1-3), `question_fa`, `question_en`, `target`, `layers`, `ref` (machine-readable definition of the correct answer) |
| `scripts/fetch_tehran_osm.py` | Downloads the five layers from OpenStreetMap (Overpass, fixed bounding box) |
| `scripts/reference.py` | Computes the reference answers (geopandas, EPSG:32639) |
| `scripts/run_benchmark.py` | Runs the questions through `s3geo.query()` and scores them |
| `scripts/summarize.py` | Aggregates runs into the metrics below |

The OSM layers are **not** stored in git; fetch them with the script (the
Hugging Face dataset repo can carry the exact snapshot you used).

## Questions

| Level | n | What it tests |
|---|---|---|
| 1 | 10 | One distance condition ("within 500 m of metro", "farther than 2 km") |
| 2 | 12 | Two conditions (AND / OR / NOT) and top-k ("the 10 nearest") |
| 3 | 8 | Three conditions, filter-then-top-k, numbers written in words, Latin digits, km units |

Every distance is stated **in the question**, so the correct answer is
unambiguous. "Within d" means planar distance <= d to the nearest feature of the
other layer; "farther than d" means > d.

## Metrics

Same design as `examples/istanbul_health_access/paper/comparison_metric.md` in
the repository:

- **executed**: share of runs that returned an output without error
- **exact**: share of runs whose answer set equals the reference
- **jaccard**: mean Jaccard similarity to the reference set
- **PAR** (Plan Agreement Rate): per question, share of runs with the modal operation sequence
- **stability**: mean pairwise Jaccard between repeated runs of one question

## Reproduce

```bash
git clone https://github.com/arazshah/smart_spatial_system && cd smart_spatial_system
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
cd benchmarks/persian_geoquery_tehran

python scripts/fetch_tehran_osm.py          # needs internet access to overpass-api.de
python scripts/reference.py                 # read its warnings!
python scripts/run_benchmark.py --dry-run   # must print J=1.0 everywhere (checks the scoring)

export LLM_BASE_URL=https://api.avalai.ir/v1 LLM_API_KEY=... LLM_MODEL=gpt-4o-mini
python scripts/run_benchmark.py --runs 3
python scripts/summarize.py results/runs_*.jsonl
```

## Limitations (please read)

- **30 questions** is a pilot, not a leaderboard. Differences of a few points are noise.
- Questions were written **before** looking at the data; `reference.py` warns when a question
  returns an empty set or the whole layer. Such questions do not discriminate and should be
  adjusted, and the adjustment recorded.
- Parks, malls and hospitals are OSM **centroids** (`out center`); roads are lines. Results
  depend on OSM completeness for Tehran on the download date. Record that date with your results.
- Only vector, distance-based questions. No raster, routing, temporal or aggregate queries yet.
- Persian only, one phrasing per question (plus three deliberate variants, q28-q30).
  Paraphrase robustness is not measured.
- The reference is planar distance in UTM 39N; geodesic distance would differ by well under a metre per kilometre at this scale.

## License and attribution

Map data (c) [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under
the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Derived layers and the reference
answers are shared under the same license. Code in `scripts/` is MIT, as in the parent repository.
