#!/usr/bin/env python3
"""Show what share of TARGET features lie within each distance of the nearest OTHER feature.

Used to choose question thresholds that discriminate (aim for roughly 10-50% of the target):

    python scripts/sweep.py hospitals parks
    python scripts/sweep.py parks main_roads --dists 50,100,200,400
"""
import argparse
import sys

from reference import _dist, load_layers

ap = argparse.ArgumentParser()
ap.add_argument("target")
ap.add_argument("other")
ap.add_argument("--dists", default="50,100,150,200,300,400,500,700,1000,1500,2000")
a = ap.parse_args()

layers = load_layers()
for k in (a.target, a.other):
    if k not in layers:
        sys.exit(f"unknown layer {k}; have: {', '.join(layers)}")
d = _dist(layers[a.target], layers[a.other])
n = len(d)
print(f"{a.target} within d of nearest {a.other}  (n={n}, median distance {d.median():.0f} m)")
print("  " + "  ".join(f"{int(x):>5}m" for x in a.dists.split(",")))
print("  " + "  ".join(f"{(d <= float(x)).mean():>6.0%}" for x in a.dists.split(",")))
