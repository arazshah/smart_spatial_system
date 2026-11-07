#!/usr/bin/env python3
"""Compute the rule-based reference answer for every question.

The reference is the ground truth the LLM-planned answers are scored against.
It uses plain geopandas/shapely in a metric CRS (EPSG:32639, UTM 39N, which
covers Tehran) - no LLM, no planner, no s3geo code - so it is independent of
the system being evaluated.

    python scripts/reference.py            # writes results/reference.json

Semantics: "within d" means distance <= d to the NEAREST feature of the other
layer; "farther than d" means distance > d. Distance is planar, measured
geometry-to-geometry in metres.
"""
import json
import pathlib
import sys

import geopandas as gpd
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
METRIC_CRS = "EPSG:32639"


def load_layers(data_dir=ROOT / "data"):
    layers = {}
    for p in sorted(pathlib.Path(data_dir).glob("*.geojson")):
        g = gpd.read_file(p).set_crs("EPSG:4326", allow_override=True)
        layers[p.stem] = g.to_crs(METRIC_CRS)
    return layers


def _union(g):
    return g.geometry.union_all() if hasattr(g.geometry, "union_all") else g.geometry.unary_union


def _dist(targets, other):
    return targets.geometry.distance(_union(other))


def evaluate(q, layers):
    """Return the ordered list of osm_ids that answer question `q`."""
    ref = q["ref"]
    t = layers[q["target"]]
    masks = []
    for cond in ref["conds"]:
        d = _dist(t, layers[cond["layer"]])
        masks.append(d <= cond["max_m"] if "max_m" in cond else d > cond["min_m"])
    if masks:
        mask = pd.concat(masks, axis=1)
        mask = mask.any(axis=1) if ref.get("combine") == "any" else mask.all(axis=1)
        sel = t[mask]
    else:
        sel = t
    tk = ref.get("topk")
    if tk and len(sel):
        d = _dist(sel, layers[tk["layer"]])
        sel = sel.loc[d.sort_values(ascending=(tk["order"] == "nearest")).index[: tk["k"]]]
    return list(sel["osm_id"])


def load_questions(path=ROOT / "questions.jsonl"):
    return [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    layers = load_layers()
    out, warnings = {}, []
    for q in load_questions():
        ids = evaluate(q, layers)
        n_target = len(layers[q["target"]])
        out[q["id"]] = {"ids": ids, "n": len(ids), "n_target": n_target}
        if len(ids) == 0:
            warnings.append(f'{q["id"]}: EMPTY reference set - adjust the distance in questions.jsonl')
        elif len(ids) == n_target and not q["ref"].get("topk"):
            warnings.append(f'{q["id"]}: reference = ALL {n_target} features (question does not discriminate)')
        print(f'{q["id"]} L{q["level"]}: {len(ids):>4} of {n_target}')
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "reference.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    if warnings:
        print("\nWARNINGS (fix the question's distance or drop it, then re-run):", *warnings, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
