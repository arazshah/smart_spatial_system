#!/usr/bin/env python3
"""
Score and rank sites by how close they are to public amenities.

Run it from a clone of the repository (the sample data lives next to this
file), against either an installed package or the checkout itself:

    python examples/accessibility_analysis.py

No API key, no server and no LLM are involved. This is the rule-based
path: the operation chain follows mechanically from the amenity list, so
the same inputs always produce the same plan and the same numbers.

The data is five candidate sites in Vienna plus three amenity layers
(metro stations, schools, parks) in examples/accessibility/. Swap in your
own GeoJSON and amenity list to analyse somewhere else - nothing below is
specific to Vienna beyond the files and the CRS.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.planner import DeterministicPlanner
from smart_spatial_system.application.services.query_execution.accessibility_query_spec import (
    AmenitySpec,
    build_accessibility_initial_inputs,
    build_accessibility_query_spec,
)

DATA_DIR = Path(__file__).parent / "accessibility"

# EPSG:31256 (MGI / Austria GK East) is the local projected CRS for Vienna.
# Distances must be computed in a projected CRS - see the note at the end
# of this file for why EPSG:4326 or even EPSG:3857 would be wrong here.
TARGET_CRS = "EPSG:31256"


def load(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.geojson").read_text())


def main() -> None:
    # 1. Describe what "accessible" means for this analysis. Each amenity
    #    gets its own distance field, a cutoff beyond which it stops
    #    contributing, and a weight.
    amenities = [
        AmenitySpec(
            ref="metro",
            distance_field="distance_to_metro_m",
            max_distance_m=800.0,
            weight=3.0,
            label="Metro (m)",
        ),
        AmenitySpec(
            ref="schools",
            distance_field="distance_to_school_m",
            max_distance_m=1200.0,
            weight=2.0,
            label="School (m)",
        ),
        AmenitySpec(
            ref="parks",
            distance_field="distance_to_park_m",
            max_distance_m=1000.0,
            weight=1.0,
            label="Park (m)",
        ),
    ]

    # 2. Turn that into a QuerySpec. This is the same structure the LLM
    #    path produces from a natural-language question - here it is built
    #    by rule, so it is reproducible.
    query_spec = build_accessibility_query_spec(
        "Rank candidate sites in Vienna by access to metro, schools and parks",
        amenities,
        target_crs=TARGET_CRS,
    )

    # 3. Plan it as a DAG and execute it against the registered plugins.
    plan = DeterministicPlanner().build(query_spec)
    registry = CapabilityRegistry.from_plugin_modules(tolerant=True)
    result = DagExecutor(lambda name: registry.resolve(name).callable).execute(
        plan,
        initial_inputs=build_accessibility_initial_inputs(
            sites=load("sites"),
            amenity_layers={
                "metro": load("metro"),
                "schools": load("schools"),
                "parks": load("parks"),
            },
        ),
    )

    if not result.success:
        raise SystemExit(f"Execution failed: {result.errors}")

    print(f"Plan: {len(plan.nodes)} operations")
    for node in plan.nodes:
        print(f"  {node.id:28s} {node.capability_name}")

    report = result.outputs["sites_report"]
    report = report if isinstance(report, dict) else report.__dict__

    table = report["table"]
    headers = [column["label"] for column in table["columns"]]
    fields = [column["field"] for column in table["columns"]]

    rows = [[str(row.get(field, "")) for field in fields] for row in table["rows"]]
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(headers)
    ]

    print(f"\n{table['title']}")
    print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)))

    summary = report["summary"]
    print(
        f"\nBest site: {summary['top_name']} "
        f"(score {summary['top_score']}, average {summary['avg_score']})"
    )


if __name__ == "__main__":
    main()


# A note on the CRS, since it is the easiest thing to get wrong here:
#
# nearest_neighbor measures planar distance in whatever units its input
# CRS uses, and does not reproject. Feed it the EPSG:4326 GeoJSON
# directly and every "distance" above comes back in DEGREES - small,
# plausible-looking numbers that are not metres and cannot be compared to
# an 800 m cutoff. The generator therefore reprojects every layer first.
#
# EPSG:3857 (the default) is a safe global fallback, but its metres are
# inflated by 1/cos(latitude) - roughly 1.5x at Vienna's 48 degrees north.
# Fine for ranking, wrong for any distance you actually report. Hence
# EPSG:31256 here; use your own local projected CRS, or the relevant UTM
# zone, for other study areas.
