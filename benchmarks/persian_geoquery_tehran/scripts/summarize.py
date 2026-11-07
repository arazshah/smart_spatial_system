#!/usr/bin/env python3
"""Summarise one or more results/runs_*.jsonl files.

    python scripts/summarize.py results/runs_gpt-4o-mini_*.jsonl

Metrics (same design as examples/istanbul_health_access/paper/comparison_metric.md):
  executed   share of runs that produced an output without error
  exact      share of runs whose answer set equals the rule-based reference
  jaccard    mean Jaccard vs. the reference, over runs that returned osm_ids
  PAR        Plan Agreement Rate: per question, share of runs using the modal
             operation sequence; reported as the mean over questions
  stability  mean pairwise Jaccard between runs of the same question
"""
import collections
import itertools
import json
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_benchmark import jaccard  # noqa: E402


def mean(xs):
    xs = list(xs)
    return round(st.mean(xs), 4) if xs else None


def block(sub):
    scored = [r for r in sub if "jaccard" in r]
    pars, stabs = [], []
    by_q = collections.defaultdict(list)
    for r in sub:
        by_q[r["id"]].append(r)
    for rs in by_q.values():
        seqs = [tuple(r["ops"]) for r in rs if r["executed"]]
        if seqs:
            pars.append(collections.Counter(seqs).most_common(1)[0][1] / len(rs))
        sets = [r["ids"] for r in rs if "ids" in r]
        pairs = list(itertools.combinations(sets, 2))
        if pairs:
            stabs.append(mean(jaccard(a, b) for a, b in pairs))
    return {
        "runs": len(sub),
        "executed": mean(r["executed"] for r in sub),
        "exact": mean(r.get("exact", False) for r in sub),
        "jaccard": mean(r["jaccard"] for r in scored),
        "PAR": mean(pars),
        "stability": mean(stabs),
        "median_secs": round(st.median(r["secs"] for r in sub), 1),
    }


def main(paths):
    rows = [json.loads(line) for p in paths for line in pathlib.Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = {"overall": block(rows)}
    for lv in sorted({r["level"] for r in rows}):
        summary[f"level_{lv}"] = block([r for r in rows if r["level"] == lv])
    summary["per_question"] = {q: block([r for r in rows if r["id"] == q]) for q in sorted({r["id"] for r in rows})}
    print(json.dumps({k: v for k, v in summary.items() if k != "per_question"}, indent=2))
    (pathlib.Path(paths[0]).parent / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    fails = [q for q, b in summary["per_question"].items() if (b["exact"] or 0) < 1]
    print("\nquestions not always exact:", ", ".join(fails) or "none")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
