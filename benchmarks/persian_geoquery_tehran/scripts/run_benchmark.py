#!/usr/bin/env python3
"""Run the Persian questions through s3geo.query() and score them.

    python scripts/run_benchmark.py                       # all 30 questions x 3 runs
    python scripts/run_benchmark.py --runs 1 --only q01 q11
    python scripts/run_benchmark.py --dry-run             # no LLM: scores the reference itself (must be 1.0)

LLM settings are read from the environment or a `.env` file (repo root or
this folder): LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. Cost: 30 questions x N
runs x 1-2 LLM calls (one repair retry is allowed by s3geo.query()).

Writes results/runs_<model>_<timestamp>.jsonl, one line per (question, run).
Prerequisites: scripts/fetch_tehran_osm.py and scripts/reference.py ran first.
"""
import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reference import ROOT, load_questions  # noqa: E402


def load_env():
    for p in (ROOT / ".env", ROOT.parent.parent / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def extract_ids(output):
    """osm_ids of the plan's final output, or None if it has no such property."""
    feats = None
    if isinstance(output, dict):
        feats = output.get("features")
    elif hasattr(output, "features"):
        feats = output.features
    elif hasattr(output, "to_json"):  # GeoDataFrame
        feats = json.loads(output.to_json()).get("features")
    if feats is None:
        return None
    ids = [(f.get("properties") or {}).get("osm_id") for f in feats]
    return None if any(i is None for i in ids) else ids


def jaccard(a, b):
    a, b = set(a), set(b)
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", nargs="*", help="question ids, e.g. q01 q11")
    ap.add_argument("--model", help="overrides LLM_MODEL")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    ref_path = ROOT / "results" / "reference.json"
    if not ref_path.exists():
        sys.exit("results/reference.json missing - run scripts/reference.py first")
    reference = json.loads(ref_path.read_text(encoding="utf-8"))
    questions = [q for q in load_questions() if not args.only or q["id"] in args.only]
    geo = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "data").glob("*.geojson")}

    if not args.dry_run:
        import s3geo
        if not os.environ.get("LLM_API_KEY"):
            sys.exit("LLM_API_KEY is not set (environment or .env)")

    out = ROOT / "results" / f"runs_{model.replace('/', '_')}_{time.strftime('%Y%m%d-%H%M%S')}{'_dry' if args.dry_run else ''}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for q in questions:
            ref_ids = reference[q["id"]]["ids"]
            for run in range(args.runs):
                rec = {"id": q["id"], "level": q["level"], "run": run, "model": model, "n_ref": len(ref_ids)}
                t0 = time.time()
                try:
                    if args.dry_run:
                        ids, ops = list(ref_ids), ["dry_run"]
                        rec["spec"] = None
                    else:
                        res = s3geo.query(q["question_fa"], layers={n: geo[n] for n in q["layers"]})
                        ids, ops = extract_ids(res.output), list(res.operations)
                        rec["spec"] = res.query_spec
                        rec["attempts"] = getattr(res, "attempt_count", None)
                    rec.update(executed=True, ops=ops, ids_available=ids is not None)
                    if ids is not None:
                        rec.update(n_out=len(ids), jaccard=jaccard(ids, ref_ids), exact=set(ids) == set(ref_ids), ids=ids)
                except Exception as e:  # noqa: BLE001 - record every failure as data
                    rec.update(executed=False, error=f"{type(e).__name__}: {str(e)[:400]}")
                rec["secs"] = round(time.time() - t0, 2)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                flag = "ok " if rec.get("exact") else ("ERR" if not rec["executed"] else "!= ")
                print(f'{q["id"]} run{run} {flag} J={rec.get("jaccard", "-")} {rec["secs"]}s')
    print("wrote", out)


if __name__ == "__main__":
    main()
