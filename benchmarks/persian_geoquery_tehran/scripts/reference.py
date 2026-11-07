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


def marginals(q, layers):
    """Per-condition pass counts: [(label, n_pass, n_target)]. Shows which conditions actually filter."""
    t = layers[q["target"]]
    out = []
    for cond in q["ref"]["conds"]:
        d = _dist(t, layers[cond["layer"]])
        ok = d <= cond["max_m"] if "max_m" in cond else d > cond["min_m"]
        label = f'{cond["layer"]} ' + (f'<= {cond["max_m"]}' if "max_m" in cond else f'> {cond["min_m"]}')
        out.append((label, int(ok.sum()), len(t)))
    return out


def near_duplicates(layers, radius_m=30):
    """Features whose nearest *other* feature is within radius_m: likely the same object mapped twice."""
    out = {}
    for name, g in layers.items():
        if len(g) < 2 or g.geom_type.iloc[0] not in ("Point", "MultiPoint"):
            continue
        _, dist = g.sindex.nearest(g.geometry, return_all=False, exclusive=True, return_distance=True)
        out[name] = int((dist <= radius_m).sum())
    return out


def check_provenance(layers, data_dir=ROOT / "data"):
    """Warnings if layers lack a provenance record or come from different sources."""
    path = pathlib.Path(data_dir) / "provenance.json"
    prov = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = []
    for name in layers:
        if name not in prov:
            out.append(f"layer {name}: NO provenance record - this file came from an unknown source/date "
                       f"(delete data/{name}.geojson and fetch it again)")
    sources = {v["source"].split(":")[0] for v in prov.values()}
    if len(sources) > 1:
        out.append(f"layers come from different sources {sorted(sources)} - answers are not comparable; fetch everything from one source")
    return out


def load_questions(path=ROOT / "questions.jsonl"):
    return [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    layers = load_layers()
    out, warnings = {}, []
    dups = near_duplicates(layers)
    print("layer sizes:", {k: len(v) for k, v in layers.items()})
    print("features with another feature within 30 m (possible duplicates):", dups)
    for name, n in dups.items():
        if n > 0.05 * len(layers[name]):
            warnings.append(f"layer {name}: {n} of {len(layers[name])} features have a neighbour within 30 m - possible double counting (e.g. same hospital as point and building)")
    warnings.extend(check_provenance(layers))
    print()
    for q in load_questions():
        ids = evaluate(q, layers)
        n_target = len(layers[q["target"]])
        out[q["id"]] = {"ids": ids, "n": len(ids), "n_target": n_target}
        if len(ids) == 0:
            warnings.append(f'{q["id"]}: EMPTY reference set - adjust the distance in questions.jsonl')
        elif len(ids) == n_target and not q["ref"].get("topk"):
            warnings.append(f'{q["id"]}: reference = ALL {n_target} features (question does not discriminate)')
        print(f'{q["id"]} L{q["level"]}: {len(ids):>4} of {n_target}')
        conds = marginals(q, layers)
        if len(conds) > 1:
            print("      " + " | ".join(f"{lab}: {n}/{tot}" for lab, n, tot in conds))
            for lab, n, tot in conds:
                if n == tot:
                    warnings.append(f'{q["id"]}: condition "{lab}" passes ALL {tot} targets - it filters nothing, tighten it')
                elif n == 0:
                    warnings.append(f'{q["id"]}: condition "{lab}" passes NO target')
        if not q["ref"].get("topk") and len(ids) > 0.6 * n_target:
            warnings.append(f'{q["id"]}: reference is {len(ids)}/{n_target} ({len(ids) / n_target:.0%}) - too loose, a lazy "return everything" answer scores high')
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "reference.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    if warnings:
        print("\nWARNINGS (fix the question's distance or drop it, then re-run):", *warnings, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
