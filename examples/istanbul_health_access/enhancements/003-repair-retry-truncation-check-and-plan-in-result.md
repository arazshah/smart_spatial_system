# 003 — Repair retry on validator rejection, truncation check, plan returned in the result

- **Status:** proposed — drafted 2026-09-23, not yet sent
- **Proposed:** 2026-09-23, in response to the first N=20 batches at `smart-spatial-system==0.5.5` (`notebooks/03_llm_arm.ipynb` §5 and §7)
- **Motivating finding(s):** `paper/PLAN.md` "Findings", the 0.5.5 batch entry
- **Request:** the prompt below, ready to paste into the session with write access to `smart_spatial_system`
- **Resolution:** _pending_

---

## Why this isn't a `bugs/` report

Nothing here contradicts the framework's documentation. The generation-time
validators reject exactly what they say they reject. `max_distance` and
`where` each do exactly what they document. `S3GeoResult` returns exactly
the fields it lists. These are gaps between "the framework behaves as
documented" and "a plain question reliably gets a correct answer", the
same category as `enhancements/001` and `002`.

## Checked before writing this

- `v0.5.5` `s3geo/__init__.py::query()` calls `generate()` once. A grep
  of `s3geo/__init__.py` and `llm_spec_generator.py` for retry or repair
  logic finds none.
- `S3GeoResult` has `goal`, `operations` (names only), `output` and
  `input_data_extent`. Plan params are not returned, and
  `LLMSpecGenerationError` carries only a message.
- The truncation numbers below were computed by comparing each Arm 2
  output set for set (by `osm_id` and distance) against this study's
  rule-based `results/rule_based_underserved.csv`.

## The ready-to-paste prompt

```
Hi - 0.5.5 results are in, and they're the best yet. With NO system_hints
(plain question only), 9 of 20 runs returned exactly the same answer as
my hand-written rule-based pipeline: the same osm_ids, and distances
identical to 0.0 m, at 1000 m in 5 runs and 2000 m in 4. Every executed
plan used EPSG:32635, the CRS your new input-data-facts section computed.
Across 21 plans there was not a single placeholder or unresolvable CRS.
enhancements/002 did exactly what I hoped: the framework now gets the CRS
right regardless of how the question is phrased. Thank you.

Every remaining failure traces to one parameter: nearest-neighbor
max_distance. Three asks, most important first.

## 1. One repair attempt after a structural validator rejects a plan

10 of 20 runs (and 10 of 20 in my comparison batch) were rejected by
_validate_max_distance_filter_composition(). That is correct. But the
rejection message already states the exact fix ("Remove max_distance_m
from 'spatial_nearest' and let the filter apply the threshold"), and
s3geo.query() calls generate() once with no retry. So the validator turns
a silent empty result into a loud failure, but never into an answer.

Requested:
- When generate() raises LLMSpecGenerationError from one of the
  structural validators (not for an HTTP/auth error or invalid JSON),
  re-prompt once with the rejected plan and the validator's message
  appended, e.g. "Your previous plan was rejected: <message>. Return a
  corrected plan." Validate the new plan the same way. If it fails again,
  raise the second error.
- Make it configurable (e.g. max_repair_attempts, default 1, 0 disables)
  and record what happened in S3GeoResult: the number of attempts and
  each rejected attempt's message. Studies like mine need to report
  first-attempt and after-repair rates separately. A silent retry would
  hide the very thing being measured.

## 2. Truncation: a max_distance cap ABOVE the threshold still drops the farthest features

_validate_max_distance_filter_composition() only rejects provably EMPTY
plans (threshold >= cap). A cap above the threshold is accepted. But with
a downstream open-ended "farther than T" filter (gt/gte T), the output is
{T < d <= M} instead of {d > T}. Every feature beyond the cap loses its
distance and fails the filter. For "which areas are underserved because
the nearest facility is too far", those are the most underserved areas.
The answer is not empty, just silently missing its most important part.

Measured against my rule-based ground truth:
- plain question, 1 run: max_distance=5000, keep > ~3000 m. It kept 52
  of the true 103 and dropped the 51 farthest (up to 13.9 km).
- comparison batch, 10 runs, all truncated:
  - 7 runs: cap 10000, keep > ~5000. Kept 44 of 51, dropped the 7
    farthest.
  - 3 runs: cap 5000, keep > ~3000. Kept 52 of 103.
- Your 0.5.4 fraction warning didn't fire on any of them. The caps
  excluded 5.3% and 0.7% of sources, both under its 20% default.

Requested - you know better than I do whether this should reject or warn:
- In the same validator, flag the case where a nearest-neighbor op has a
  cap M and a downstream filter on its distance field keeps gt/gte T with
  T < M and no upper bound. The plan says "farther than T" but will only
  return "between T and M". Keep allowing an explicit two-sided filter
  (between [T, U] with U <= M, or gt T plus lt/lte U <= M), because then
  the band is clearly intended.
- If you'd rather not reject at generation (a band can in principle be
  intended), a generation-time warning surfaced in S3GeoResult would
  still stop it from being silent. With item 1, a reject gets repaired
  anyway.

## 3. Return the plan in the result (and in the error)

For five batches I have had to infer each plan's threshold and where
clause from the output distances, because S3GeoResult exposes only
operation names, and LLMSpecGenerationError only a message.

Requested:
- Add the validated QuerySpec (or its JSON) to S3GeoResult.
- Attach the rejected plan (spec or raw LLM JSON) to
  LLMSpecGenerationError as an attribute.
- Together with item 1's attempt record, that makes every run fully
  auditable without re-running it.

## A question, not a claim

Every one of the 21 rejected plans used the key "max_distance_m", not
"max_distance". Apart from the parameter list, that key appears in the
prompt only in _domain_guidance()'s filter_by_distance worked example
("nearer than X meters to POI": params={"max_distance_m": X, "k": 1,
"drop_unmatched": true}). I can't show causation, because my records
don't include the plans (hence item 3). But given the EPSG:31256 history,
it may be worth checking whether that example leaks into spatial_nearest
plans, where a cap is almost never needed to find "the nearest".

## What I need back

Same as always: confirm via CHANGELOG.md what landed, what didn't, and
why, then tag the release, so I can verify against the real diff before
bumping my pin.
```
