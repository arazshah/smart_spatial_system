#!/usr/bin/env python3
"""
The same Vienna accessibility ranking as accessibility_analysis.py -
five candidate sites scored by distance to metro, schools and parks -
but wired up entirely through `s3geo`, not `orchestrator.*` directly.

This is the source recording for docs/assets/demo.gif: real, unedited
terminal output, no server, no LLM, deterministic by construction.

    python examples/s3geo_accessibility_demo.py

Every planning-pipeline class here (`s3geo.DeterministicPlanner`,
`s3geo.registry`, `s3geo.DagExecutor`, `s3geo.RegistryCapabilityResolver`)
is a direct re-export of the exact same class `orchestrator.planning` /
`orchestrator.capability_registry` define (see s3geo/__init__.py) - this
script proves `import s3geo` alone is enough to reach them, with no
knowledge of where they actually live required.
"""

from __future__ import annotations

import json
from pathlib import Path

import s3geo
from smart_spatial_system.application.services.query_execution.accessibility_query_spec import (
    AmenitySpec,
    build_accessibility_initial_inputs,
    build_accessibility_query_spec,
)

DATA_DIR = Path(__file__).parent / "accessibility"

# EPSG:31256 (MGI / Austria GK East) is the local projected CRS for Vienna -
# see the note at the end of accessibility_analysis.py for why this matters.
TARGET_CRS = "EPSG:31256"


def load(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.geojson").read_text())


def main() -> None:
    print("import s3geo")
    print("=" * 60)

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

    query_spec = build_accessibility_query_spec(
        "Rank candidate sites in Vienna by access to metro, schools and parks",
        amenities,
        target_crs=TARGET_CRS,
    )

    # Every name below is s3geo.<Name> - the one-call s3geo.query() wires
    # these up internally; here they are used directly for full control.
    plan = s3geo.DeterministicPlanner().build(query_spec)
    registry = s3geo.registry()
    result = s3geo.DagExecutor(s3geo.RegistryCapabilityResolver(registry)).execute(
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

    print(f"\nPlan: {len(plan.nodes)} operations (s3geo.DeterministicPlanner)")
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
    print("\nRan entirely through s3geo.* - see README.md for s3geo.query(),")
    print("the one-call path this same pipeline also powers.")


if __name__ == "__main__":
    main()
