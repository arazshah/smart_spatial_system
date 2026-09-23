# Comparison metric

How similar two runs of the planner are is measured at three layers, because
each answers a different question. All three are computed over the same N
repeated runs of each arm, on the same question and the same input data.

This follows `smart-spatial-vienna-accessibility`'s metric deliberately, so
the two case studies are comparable — with one change forced by the output
shape. Vienna's primary output was a **ranking** of 23 districts; this
study's primary output is a **set** (the underserved mahalle). Layer 3 is
therefore set-valued here, with rank stability kept as a secondary measure
over the continuous distance scores. Don't re-derive the rest; it was
designed, not improvised.

## Layer 1 — Structural agreement: Plan Agreement Rate (PAR)

Does the planner build the same *shape* of plan every time?

For a run *r*, its **operation sequence** is the ordered list of operation
names in its `QuerySpec` (`crs_transform`, `nearest_neighbor`,
`zonal_statistics`, …), stripped of node ids and parameter values — only the
operation *names* and their input/output wiring topology.

1. Take the mode (most frequent) operation sequence across all N runs of an
   arm; call it the reference sequence.
2. `PAR = (runs whose sequence exactly equals the reference) / N`

The rule-based arm has `PAR = 1.0` by construction. That is the baseline the
LLM arm is measured against, not a result to compute.

## Layer 2 — Parametric agreement

Among runs sharing the reference sequence, how much do their *parameters*
vary? Report mean and standard deviation across runs for every value the LLM
had to invent — above all the **distance threshold** (the rule-based arm is
simply given 2000 m), plus any `max_distance_m`, buffer radius or weight it
introduces, and the **CRS** it chooses.

A structurally identical plan with high parameter variance is still an
unreliable plan; this layer catches what PAR alone would miss. The CRS is
worth singling out: a plan that picks a geographic CRS for a metric
threshold is not a parameter choice, it is a wrong answer, and it should be
counted separately rather than averaged in.

## Layer 3 — Outcome agreement

### 3a. Set overlap (primary)

The answer a reader sees is *which mahalle are underserved*. For each run,
take that set U_r. For every pair of runs within an arm compute the
**Jaccard index** |U_a ∩ U_b| / |U_a ∪ U_b|; **Set Stability** is the mean
Jaccard across all pairs (± sd). Also report Jaccard between each LLM run
and the rule-based reference set.

Report alongside it the **size** of each run's underserved set — two runs
can have a respectable Jaccard while disagreeing about whether 40 or 400
mahalle are underserved, and that difference is the whole finding.

Because the set is threshold-dependent, compute 3a at each of the three
reported thresholds (1000 / 1500 / 2000 m) for the rule-based arm, and at
whatever threshold each LLM run chose for itself.

### 3b. Rank stability (secondary)

Independently of any threshold, each run also yields a continuous
per-mahalle nearest-facility distance. Rank the mahalle by it and compute
pairwise **Spearman's ρ**; Rank Stability is the mean ρ (± sd). This
separates "the two arms measure distance the same way" from "the two arms
draw the line in the same place" — a pair of runs can score ρ ≈ 1.0 and
still produce very different sets if their thresholds differ.

Both are `1.0` for the rule-based arm by construction.

## Degeneracy check (runs before any metric)

A run is **degenerate** if every mahalle gets the same distance, every
distance is `0.0`, or the underserved set is empty or everything. Record it
as its own column, separate from "execution succeeded". A degenerate batch
must never be averaged into a stability number — three of Vienna's batches
looked like a 100% success rate while being entirely meaningless.

## Reliability metrics (reported alongside)

- **Success rate**: fraction of the N runs that executed without error
  (`DagExecutionResult.success`).
- **Degenerate rate**: fraction that executed *and* failed the check above.
- **Latency**: median and IQR of wall-clock time per run.

## Reporting format

One row per arm (and per LLM configuration, if anything is swept):

| Arm | N | PAR | Threshold (mean ± sd) | Set Stability (Jaccard, mean ± sd) | Jaccard vs. rule-based | \|U\| (mean ± sd) | Rank Stability (ρ) | Success | Degenerate | Median latency |
|---|---|---|---|---|---|---|---|---|---|---|

## Implementation notes

- Plan comparison operates on `QuerySpec.operations`, not on the executed
  `DagPlan` — the QuerySpec is the more direct output of the planning step
  being compared.
- Spearman via `scipy.stats.spearmanr`. Watch for **ties**: mahalle
  containing a facility all sit at distance `0.0`, and how ties are broken
  changes ρ. Use the average-rank convention consistently in both arms and
  say so in the paper — Vienna's only ρ gap turned out to be a rounding
  artifact of exactly this.
- All raw per-run QuerySpecs, distances and underserved sets are kept in
  `results/llm_runs/` so the metric notebook is reproducible from stored
  data without re-calling the LLM.
