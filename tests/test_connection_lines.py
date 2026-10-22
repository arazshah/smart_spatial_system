import math

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag_executor import DagExecutor
from orchestrator.planning.llm_spec_generator import query_spec_from_dict
from orchestrator.planning.planner import DeterministicPlanner, PlannerConfig
from plugins.connection_lines import build_connection_lines

BRANCHES = [
    ("شعبه ونک", 51.4095, 35.7575),
    ("شعبه تجریش", 51.432, 35.804),
    ("شعبه نیاوران", 51.47, 35.81),
    ("شعبه یافت‌آباد", 51.295, 35.645),
    ("شعبه ولیعصر", 51.409, 35.711),
]
CANDIDATES = [
    ("محل پیشنهادی نارمک", 51.501, 35.734),
    ("محل پیشنهادی خاوران", 51.465, 35.65),
]


def _fc(rows):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"name": name},
            }
            for name, lon, lat in rows
        ],
    }


def test_build_connection_lines_unit():
    nearest = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {
                    "name": "A",
                    "_nearest_distance": 5.0,
                    "_target_properties": {"name": "B"},
                    "_target_geometry": {"type": "Point", "coordinates": [3, 4]},
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [9, 9]},
                "properties": {"name": "no target"},
            },
        ],
    }

    out = build_connection_lines(
        nearest, source_name_field="name", target_name_field="name"
    )

    assert len(out.features) == 1
    feature = out.features[0]
    assert feature["geometry"] == {"type": "LineString", "coordinates": [[0.0, 0.0], [3.0, 4.0]]}
    assert feature["properties"] == {"source_name": "A", "target_name": "B", "distance_m": 5.0}
    assert out.metadata["skipped_count"] == 1


def test_nearest_branch_lines_pipeline_executes_with_real_plugins():
    crs = "EPSG:32639"
    spec = query_spec_from_dict(
        {
            "raw_query": "nearest branch line for each candidate",
            "goal": "connect",
            "entities": [
                {"ref": "candidates", "kind": "vector"},
                {"ref": "branches", "kind": "vector"},
            ],
            "operations": [
                {"op": "crs_transform", "inputs": {"vector": "candidates"},
                 "params": {"source_crs": "EPSG:4326", "target_crs": crs}, "output": "cand_m"},
                {"op": "crs_transform", "inputs": {"vector": "branches"},
                 "params": {"source_crs": "EPSG:4326", "target_crs": crs}, "output": "br_m"},
                {"op": "spatial_nearest", "inputs": {"source": "cand_m", "target": "br_m"},
                 "params": {"k": 1, "include_target_geometry": True,
                            "source_crs": crs, "target_crs": crs}, "output": "nearest"},
                {"op": "connect_nearest_lines", "inputs": {"vector": "nearest"},
                 "params": {"source_name_field": "name", "target_name_field": "name",
                            "source_crs": crs, "output_crs": "EPSG:4326"},
                 "output": "lines"},
            ],
            "outputs": [{"kind": "vector", "source": "lines", "format": "geojson", "config": {}}],
        },
        raw_query_fallback="x",
    )
    plan = DeterministicPlanner(PlannerConfig(strict_params=True)).build(spec)
    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    result = DagExecutor(RegistryCapabilityResolver(registry)).execute(
        plan,
        initial_inputs={"candidates": _fc(CANDIDATES), "branches": _fc(BRANCHES)},
    )

    assert result.success is True, result.error
    assert result.trace[-1].capability_name == "build_connection_lines"

    lines = result.output_nodes["lines"].features
    assert len(lines) == 2
    assert {f["properties"]["source_name"] for f in lines} == {n for n, _, _ in CANDIDATES}
    assert {f["properties"]["target_name"] for f in lines} <= {n for n, _, _ in BRANCHES}

    for feature in lines:
        assert set(feature["properties"]) == {"source_name", "target_name", "distance_m"}
        assert feature["geometry"]["type"] == "LineString"
        (x1, y1), (x2, y2) = feature["geometry"]["coordinates"]
        # reprojected back to lon/lat, not left in metric UTM
        assert 50 < x1 < 53 and 35 < y1 < 36
        # distance is metres, comparable to the real great-circle length
        approx_m = math.hypot((x2 - x1) * 91000, (y2 - y1) * 111000)
        assert math.isclose(feature["properties"]["distance_m"], approx_m, rel_tol=0.05)
