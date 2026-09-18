#!/usr/bin/env python3
"""
Ask a natural-language spatial question in one call, via the s3geo module.

Run it from a clone of the repository (the sample data lives next to this
file), against either an installed package or the checkout itself:

    export LLM_API_KEY="..."          # or AVALAI_API_KEY / OPENAI_API_KEY
    python examples/s3geo_quickstart.py

Unlike examples/accessibility_analysis.py, this DOES need a real LLM key
and makes a real network call - s3geo.query() plans the operation chain
from the natural-language question itself, it does not build the QuerySpec
by rule. See README.md's "As a library" section for the rule-based,
no-LLM-needed path if you want a fully offline example instead.

The data is the same five candidate sites plus metro stations used by
examples/accessibility_analysis.py, in examples/accessibility/.
"""

from __future__ import annotations

import json
from pathlib import Path

import s3geo

DATA_DIR = Path(__file__).parent / "accessibility"


def load(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.geojson").read_text())


def main() -> None:
    result = s3geo.query(
        "Find sites that are within 500 meters of a metro station, "
        "reprojecting to EPSG:31256 first so the distance is in metres.",
        layers={
            "sites": load("sites"),
            "metro": load("metro"),
        },
    )

    print(f"Goal: {result.goal}")
    print(f"Operations ({len(result.operations)}): {', '.join(result.operations)}")

    output = result.output
    features = getattr(output, "features", None)
    if features is not None:
        print(f"Output: {len(features)} feature(s)")
    else:
        print(f"Output: {output!r}")


if __name__ == "__main__":
    main()
