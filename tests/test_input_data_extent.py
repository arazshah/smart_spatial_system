"""
Tests for orchestrator.planning.input_data_extent and its use by
LLMQuerySpecGenerator / s3geo.query() (enhancements/002 items 1, 2, 4).

Item 1: the planner is told a projected CRS computed from the query's own
input layers, instead of guessing one from the query text.
Item 2: a source_crs/target_crs that doesn't resolve ("<PROJECTED_CRS>",
"EPSG:XXXX") fails at generation time, not one node into DAG execution.
Item 4: a nearest-neighbor max_distance that makes a downstream filter on
the same distance field unsatisfiable fails at generation time.

Run:
    pytest tests/test_input_data_extent.py -v
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("pyproj", reason="pyproj not installed")

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

import s3geo  # noqa: E402
from orchestrator.planning.input_data_extent import (  # noqa: E402
    InputDataExtent,
    derive_input_data_extent,
    render_input_data_facts,
    utm_epsg_for_lonlat,
)
from orchestrator.planning.llm_spec_generator import (  # noqa: E402
    LLMQuerySpecGenerator,
    LLMSpecGenerationError,
    StaticLLMClient,
    _domain_guidance,
    build_llm_messages,
)


def _fc(*points: tuple[float, float], crs_name: str | None = None) -> dict:
    layer = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": i},
                "geometry": {"type": "Point", "coordinates": [x, y]},
            }
            for i, (x, y) in enumerate(points)
        ],
    }
    if crs_name is not None:
        layer["crs"] = {"type": "name", "properties": {"name": crs_name}}
    return layer


# Istanbul, roughly the extent of the enhancements/002 study data.
ISTANBUL_AREAS = _fc((28.80, 40.95), (29.30, 41.20))
ISTANBUL_CLINICS = _fc((28.95, 41.00), (29.05, 41.08))


# --------------------------------------------------------------------- #
# derive_input_data_extent
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (29.0, 41.0, "EPSG:32635"),  # Istanbul
        (-58.4, -34.6, "EPSG:32721"),  # Buenos Aires (southern hemisphere)
        (-180.0, 10.0, "EPSG:32601"),
        (180.0, 10.0, "EPSG:32660"),  # clamped, not zone 61
        (0.0, 0.0, "EPSG:32631"),
    ],
)
def test_utm_rule(lon, lat, expected):
    assert utm_epsg_for_lonlat(lon, lat) == expected


def test_combined_extent_and_utm_suggestion_for_istanbul():
    extent = derive_input_data_extent({"areas": ISTANBUL_AREAS, "clinics": ISTANBUL_CLINICS})

    assert extent is not None
    assert extent.bbox == (28.80, 40.95, 29.30, 41.20)
    assert extent.centroid == pytest.approx((29.05, 41.075))
    assert extent.layer_crs == (("areas", "EPSG:4326"), ("clinics", "EPSG:4326"))
    assert extent.suggested_crs == "EPSG:32635"
    assert extent.suggested_crs_name == "WGS 84 / UTM zone 35N"
    assert extent.max_scale_error < 0.001
    assert extent.notes == ()


def test_southern_hemisphere_uses_327xx():
    extent = derive_input_data_extent({"x": _fc((-58.5, -34.7), (-58.3, -34.5))})
    assert extent.suggested_crs == "EPSG:32721"


def test_extent_straddling_two_zones_still_suggests_centroid_zone_with_note():
    # 30E is the zone 35/36 boundary.
    extent = derive_input_data_extent({"x": _fc((29.9, 40.9), (30.2, 41.2))})

    assert extent.suggested_crs == "EPSG:32636"
    assert any("spans UTM zones 35-36" in note for note in extent.notes)
    assert any("stays within" in note for note in extent.notes)


def test_extent_too_wide_for_one_zone_suggests_nothing():
    # Equatorial, 40 degrees wide: no single UTM zone is within 1%.
    extent = derive_input_data_extent({"x": _fc((0.0, -5.0), (40.0, 5.0))})

    assert extent is not None
    assert extent.suggested_crs is None
    assert extent.max_scale_error > 0.01
    assert any("too wide" in note for note in extent.notes)
    rendered = render_input_data_facts(extent)
    assert "No single projected CRS is suggested" in rendered


def test_antimeridian_scale_extent_suggests_nothing():
    extent = derive_input_data_extent({"x": _fc((-179.5, -17.0), (179.5, -16.0))})
    assert extent.suggested_crs is None
    assert any("180 degrees of longitude" in note for note in extent.notes)


def test_polar_data_uses_ups():
    extent = derive_input_data_extent({"x": _fc((10.0, 85.0), (20.0, 86.0))})
    assert extent.suggested_crs == "EPSG:32661"


def test_projected_geodataframe_extent_is_reprojected_first():
    gdf = gpd.GeoDataFrame(
        geometry=[Point(28.9, 41.0), Point(29.1, 41.1)], crs="EPSG:4326"
    ).to_crs("EPSG:3857")

    extent = derive_input_data_extent({"g": gdf})

    assert extent.layer_crs == (("g", "EPSG:3857"),)
    assert extent.bbox == pytest.approx((28.9, 41.0, 29.1, 41.1), abs=1e-6)
    assert extent.suggested_crs == "EPSG:32635"


def test_geojson_with_legacy_crs_member_is_reprojected_first():
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True)
    layer = _fc(t.transform(28.9, 41.0), t.transform(29.1, 41.1), crs_name="EPSG:32635")

    extent = derive_input_data_extent({"x": layer})

    assert extent.layer_crs == (("x", "EPSG:32635"),)
    # A UTM-aligned bbox covers slightly more lon/lat than the points
    # themselves (grid convergence), hence the looser tolerance.
    assert extent.bbox == pytest.approx((28.9, 41.0, 29.1, 41.1), abs=0.01)
    assert extent.suggested_crs == "EPSG:32635"


@pytest.mark.parametrize(
    "layers",
    [
        # GeoDataFrame with no CRS at all.
        {"g": gpd.GeoDataFrame(geometry=[Point(29.0, 41.0)])},
        # Plain GeoJSON (defaults to EPSG:4326) but metre coordinates.
        {"x": _fc((3226000.0, 5012000.0))},
        # Unresolvable legacy crs member.
        {"x": _fc((29.0, 41.0), crs_name="not-a-crs")},
        # One good layer does not rescue a bad one.
        {"good": ISTANBUL_AREAS, "bad": _fc((3226000.0, 5012000.0))},
    ],
)
def test_unknown_crs_skips_the_suggestion(layers):
    assert derive_input_data_extent(layers) is None


def test_no_geometry_or_non_vector_values_give_none():
    assert derive_input_data_extent({}) is None
    assert derive_input_data_extent({"x": {"type": "FeatureCollection", "features": []}}) is None
    assert derive_input_data_extent({"threshold": 5, "name": "abc"}) is None


def test_non_vector_values_are_ignored_alongside_vector_layers():
    extent = derive_input_data_extent({"areas": ISTANBUL_AREAS, "threshold": 5})
    assert extent.suggested_crs == "EPSG:32635"


def test_missing_pyproj_skips_silently(monkeypatch):
    real_import = builtins.__import__

    def _no_pyproj(name, *args, **kwargs):
        if name == "pyproj" or name.startswith("pyproj."):
            raise ImportError("no pyproj")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pyproj)
    assert derive_input_data_extent({"areas": ISTANBUL_AREAS}) is None


# --------------------------------------------------------------------- #
# Prompt rendering (item 1)
# --------------------------------------------------------------------- #


def test_facts_are_rendered_into_the_system_prompt_before_system_hints():
    extent = derive_input_data_extent({"areas": ISTANBUL_AREAS})

    messages = build_llm_messages(
        "find underserved areas", input_data_extent=extent, system_hints="HINT-MARKER"
    )
    system = messages[0]["content"]

    assert "Input data facts" in system
    assert "Suitable projected CRS for metric work on this data: EPSG:32635" in system
    assert "min_lon=28.800000" in system
    assert system.index("Input data facts") < system.index("HINT-MARKER")


def test_no_facts_section_without_an_extent():
    system = build_llm_messages("find underserved areas")[0]["content"]
    assert "Input data facts (computed" not in system


def test_generic_prompt_contains_no_real_projected_crs_code():
    """
    0.5.3's root cause was a literal CRS code in the generic prompt being
    copied for a city it didn't belong to. The only codes allowed in the
    data-independent prompt are EPSG:4326 (the upload default it explains).
    """
    import re

    codes = set(re.findall(r"EPSG:\d+", _domain_guidance()))
    assert codes <= {"EPSG:4326"}


# --------------------------------------------------------------------- #
# Generation-time CRS resolution (item 2)
# --------------------------------------------------------------------- #


def _nearest_plan(target_crs: str, *, where: dict | None = None, nn_params: dict | None = None) -> dict:
    operations = [
        {
            "op": "crs_transform",
            "inputs": {"vector": "areas"},
            "params": {"source_crs": "EPSG:4326", "target_crs": target_crs},
            "output": "areas_m",
        },
        {
            "op": "crs_transform",
            "inputs": {"vector": "clinics"},
            "params": {"source_crs": "EPSG:4326", "target_crs": target_crs},
            "output": "clinics_m",
        },
        {
            "op": "spatial_nearest",
            "inputs": {"source": "areas_m", "target": "clinics_m"},
            "params": {"k": 1, "source_crs": target_crs, "target_crs": target_crs, **(nn_params or {})},
            "output": "nearest",
        },
    ]
    if where is not None:
        operations.append(
            {
                "op": "filter_attribute",
                "inputs": {"vector": "nearest"},
                "params": {"where": where},
                "output": "underserved",
            }
        )
    return {
        "goal": "underserved_areas",
        "entities": [{"ref": "areas", "kind": "vector"}, {"ref": "clinics", "kind": "vector"}],
        "operations": operations,
        "outputs": [{"kind": "vector_layer", "source": operations[-1]["output"]}],
    }


def _generate(llm_json: dict, **kwargs):
    client = StaticLLMClient(json.dumps(llm_json))
    return LLMQuerySpecGenerator(client).generate("find underserved areas", **kwargs)


@pytest.mark.parametrize("bad", ["<PROJECTED_CRS>", "EPSG:XXXX", "EPSG:999999"])
def test_unresolvable_crs_fails_at_generation_with_the_computed_crs(bad):
    extent = derive_input_data_extent({"areas": ISTANBUL_AREAS, "clinics": ISTANBUL_CLINICS})

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        _generate(_nearest_plan(bad), input_data_extent=extent)

    message = str(excinfo.value)
    assert "not a resolvable CRS" in message
    assert repr(bad) in message
    assert "EPSG:32635 - use that value" in message


def test_unresolvable_crs_without_extent_gives_the_utm_rule_not_an_example():
    import re

    with pytest.raises(LLMSpecGenerationError) as excinfo:
        _generate(_nearest_plan("<PROJECTED_CRS>"))

    message = str(excinfo.value)
    assert "floor((lon + 180) / 6) + 1" in message
    assert re.findall(r"EPSG:\d+", message) == []


def test_resolvable_crs_passes_even_if_it_differs_from_the_suggestion():
    extent = derive_input_data_extent({"areas": ISTANBUL_AREAS})
    spec = _generate(_nearest_plan("EPSG:3857"), input_data_extent=extent)
    assert [op.op for op in spec.operations] == ["crs_transform", "crs_transform", "spatial_nearest"]


def test_integer_and_digit_string_crs_values_resolve():
    llm_json = _nearest_plan("EPSG:32635")
    llm_json["operations"][0]["params"]["target_crs"] = 32635
    llm_json["operations"][1]["params"]["target_crs"] = 32635
    llm_json["operations"][2]["params"]["source_crs"] = "32635"
    llm_json["operations"][2]["params"]["target_crs"] = "32635"
    _generate(llm_json)


# --------------------------------------------------------------------- #
# max_distance / downstream filter composition (item 4)
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "where",
    [
        {"field": "_nearest_distance", "op": "gt", "value": 1000},
        {"field": "_nearest_distance", "op": "gt", "value": 1500},
        {"field": "_nearest_distance", "op": "gte", "value": 1000.5},
        {"_nearest_distance": {"gt": 1000}},
        {"and": [{"field": "name", "op": "exists", "value": True}, {"_nearest_distance": {"gte": 2000}}]},
        {"field": "_nearest_distance", "op": "between", "value": [1200, 5000]},
    ],
)
def test_max_distance_that_empties_a_downstream_filter_fails_at_generation(where):
    """The enhancements/002 item 4 repro: max_distance=1000 then keep > 1000 m."""
    with pytest.raises(LLMSpecGenerationError) as excinfo:
        _generate(_nearest_plan("EPSG:32635", where=where, nn_params={"max_distance": 1000.0}))

    message = str(excinfo.value)
    assert "max_distance=1000.0" in message
    assert "always returns zero features" in message
    assert "'underserved'" in message


@pytest.mark.parametrize(
    "where",
    [
        # An explicit band inside the cap is clearly intended (0.5.6).
        {"and": [{"_nearest_distance": {"gt": 800}}, {"_nearest_distance": {"lte": 1000}}]},
        {"field": "_nearest_distance", "op": "between", "value": [800, 900]},
        # gte exactly at max_distance can still keep features at exactly it.
        {"field": "_nearest_distance", "op": "gte", "value": 1000},
        # Upper bounds are compatible with max_distance.
        {"field": "_nearest_distance", "op": "lt", "value": 5000},
        # Conditions under "or"/"not" aren't required on their own.
        {"or": [{"_nearest_distance": {"gt": 5000}}, {"name": "x"}]},
        {"not": {"_nearest_distance": {"lte": 5000}}},
        # A different field.
        {"field": "population", "op": "gt", "value": 5000},
    ],
)
def test_compatible_max_distance_and_filter_pass(where):
    _generate(_nearest_plan("EPSG:32635", where=where, nn_params={"max_distance": 1000.0}))


def test_filter_without_max_distance_passes():
    _generate(_nearest_plan("EPSG:32635", where={"_nearest_distance": {"gt": 1000}}))


def test_max_distance_m_alias_and_custom_distance_field_are_checked():
    with pytest.raises(LLMSpecGenerationError, match="max_distance_m=500"):
        _generate(
            _nearest_plan(
                "EPSG:32635",
                where={"field": "clinic_dist", "op": "gt", "value": 500},
                nn_params={"max_distance_m": 500, "distance_field": "clinic_dist"},
            )
        )

    # Filtering the default field while the op wrote a custom one: no conflict.
    _generate(
        _nearest_plan(
            "EPSG:32635",
            where={"field": "_nearest_distance", "op": "gt", "value": 500},
            nn_params={"max_distance_m": 500, "distance_field": "clinic_dist"},
        )
    )


def test_conflict_is_found_through_a_chain_of_filters():
    llm_json = _nearest_plan(
        "EPSG:32635",
        where={"field": "_nearest_distance", "op": "lt", "value": 99999},
        nn_params={"max_distance": 1000},
    )
    llm_json["operations"].append(
        {
            "op": "filter_attribute",
            "inputs": {"vector": "underserved"},
            "params": {"where": {"_nearest_distance": {"gt": 1000}}},
            "output": "really_underserved",
        }
    )
    llm_json["outputs"] = [{"kind": "vector_layer", "source": "really_underserved"}]

    with pytest.raises(LLMSpecGenerationError, match="'really_underserved'"):
        _generate(llm_json)


# --------------------------------------------------------------------- #
# s3geo.query() wiring (item 1)
# --------------------------------------------------------------------- #


def test_query_passes_computed_facts_to_the_planner_and_returns_them(monkeypatch):
    llm_json = {
        "goal": "buffer_areas",
        "entities": [{"ref": "areas", "kind": "vector"}],
        "operations": [
            {
                "op": "buffer",
                "inputs": {"vector": "areas"},
                "params": {"distance": 100, "engine": "python"},
                "output": "buffered",
            }
        ],
        "outputs": [{"kind": "vector_layer", "source": "buffered", "config": {}}],
    }
    clients: list[StaticLLMClient] = []

    def _factory(*args, **kwargs):
        client = StaticLLMClient(json.dumps(llm_json))
        clients.append(client)
        return client

    monkeypatch.setattr(s3geo, "OpenAICompatibleLLMClient", _factory)

    result = s3geo.query("buffer the areas", layers={"areas": ISTANBUL_AREAS})

    assert isinstance(result.input_data_extent, InputDataExtent)
    assert result.input_data_extent.suggested_crs == "EPSG:32635"
    system = clients[0].last_messages[0]["content"]
    assert "Suitable projected CRS for metric work on this data: EPSG:32635" in system


def test_query_uses_the_geodataframe_crs_not_its_json(monkeypatch):
    llm_json = {
        "goal": "buffer_areas",
        "entities": [{"ref": "areas", "kind": "vector"}],
        "operations": [
            {
                "op": "buffer",
                "inputs": {"vector": "areas"},
                "params": {"distance": 100, "engine": "python"},
                "output": "buffered",
            }
        ],
        "outputs": [{"kind": "vector_layer", "source": "buffered", "config": {}}],
    }
    monkeypatch.setattr(
        s3geo, "OpenAICompatibleLLMClient", lambda *a, **k: StaticLLMClient(json.dumps(llm_json))
    )
    gdf = gpd.GeoDataFrame(
        geometry=[Point(28.9, 41.0), Point(29.1, 41.1)], crs="EPSG:4326"
    ).to_crs("EPSG:32635")

    result = s3geo.query("buffer the areas", layers={"areas": gdf})

    assert result.input_data_extent.layer_crs == (("areas", "EPSG:32635"),)
    assert result.input_data_extent.suggested_crs == "EPSG:32635"


# --------------------------------------------------------------------- #
# PR #54 review follow-ups
# --------------------------------------------------------------------- #


def test_proj_string_that_crs_transform_would_mangle_fails_at_generation():
    """
    pyproj accepts "+proj=utm +zone=35 ..." as-is, but crs_transform
    upper-cases it and strips spaces before executing it, which PROJ
    rejects - so the validator must check the normalized value.
    """
    proj_string = "+proj=utm +zone=35 +datum=WGS84 +units=m"

    with pytest.raises(LLMSpecGenerationError, match="upper-cased, spaces removed"):
        _generate(_nearest_plan(proj_string))


def test_lowercase_epsg_code_still_resolves_after_normalization():
    _generate(_nearest_plan("epsg:32635"))


def test_extent_crossing_utm_northern_limit_uses_ups_not_utm():
    # Centroid 84.0N is inside UTM's band, but the extent reaches 84.5N.
    extent = derive_input_data_extent({"x": _fc((10.0, 83.5), (12.0, 84.5))})
    assert extent.suggested_crs == "EPSG:32661"


def test_extent_crossing_utm_southern_limit_uses_ups_not_utm():
    extent = derive_input_data_extent({"x": _fc((10.0, -80.5), (12.0, -79.5))})
    assert extent.suggested_crs == "EPSG:32761"


def test_extent_crossing_utm_limit_far_from_pole_suggests_nothing():
    extent = derive_input_data_extent({"x": _fc((10.0, 50.0), (12.0, 85.0))})
    assert extent.suggested_crs is None
    assert any("latitude limit" in note for note in extent.notes)


# --------------------------------------------------------------------- #
# enhancements/003 item 2: a cap ABOVE the threshold truncates the result
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nn_params", "where", "expected"),
    [
        # The reported cases: cap 5000 / keep > ~3000, cap 10000 / keep > ~5000.
        ({"max_distance": 5000}, {"field": "_nearest_distance", "op": "gt", "value": 3000}, "> 3000"),
        ({"max_distance_m": 10000}, {"_nearest_distance": {"gte": 5000}}, "> 5000"),
        # An upper bound ABOVE the cap still loses (cap, upper].
        (
            {"max_distance": 5000},
            {"and": [{"_nearest_distance": {"gt": 3000}}, {"_nearest_distance": {"lt": 8000}}]},
            "upper limit of 8000",
        ),
        ({"max_distance": 5000}, {"field": "_nearest_distance", "op": "between", "value": [3000, 9000]}, "upper limit of 9000"),
    ],
)
def test_cap_above_open_ended_threshold_is_rejected_as_truncation(nn_params, where, expected):
    with pytest.raises(LLMSpecGenerationError) as excinfo:
        _generate(_nearest_plan("EPSG:32635", where=where, nn_params=nn_params))

    message = str(excinfo.value)
    assert expected in message
    assert "miss every feature beyond it" in message
    assert "'underserved'" in message


def test_farthest_first_sort_under_a_cap_is_rejected_as_truncation():
    llm_json = _nearest_plan("EPSG:32635", nn_params={"max_distance": 5000})
    llm_json["operations"].append(
        {
            "op": "sort_limit",
            "inputs": {"vector": "nearest"},
            "params": {"sort_by": "_nearest_distance", "sort_order": "desc", "limit": 10},
            "output": "farthest",
        }
    )
    llm_json["outputs"] = [{"kind": "vector_layer", "source": "farthest"}]

    with pytest.raises(LLMSpecGenerationError, match="descending"):
        _generate(llm_json)


def test_nearest_first_sort_under_a_cap_passes():
    llm_json = _nearest_plan("EPSG:32635", nn_params={"max_distance": 5000})
    llm_json["operations"].append(
        {
            "op": "sort_limit",
            "inputs": {"vector": "nearest"},
            "params": {"sort_by": "_nearest_distance", "sort_order": "asc", "limit": 10},
            "output": "closest",
        }
    )
    llm_json["outputs"] = [{"kind": "vector_layer", "source": "closest"}]
    _generate(llm_json)


def test_filter_by_distance_band_is_not_truncation():
    """filter_by_distance's cap IS its purpose ("nearer than X")."""
    llm_json = _nearest_plan(
        "EPSG:32635",
        where={"field": "_nearest_distance", "op": "gt", "value": 3000},
        nn_params={"max_distance_m": 5000, "drop_unmatched": True},
    )
    nearest = llm_json["operations"][2]
    nearest["op"] = "filter_by_distance"
    nearest["inputs"] = {"vector": "areas_m", "reference": "clinics_m"}
    _generate(llm_json)


def test_prompt_contrasts_within_x_and_farther_than_x():
    """
    enhancements/003 question: the only max_distance_m worked example was
    "nearer than X"; the prompt now shows the opposite case without a cap.
    """
    guidance = _domain_guidance()
    assert '"nearer than X meters to POI" (keep only features WITHIN X)' in guidance
    assert '"farther than X meters from the nearest POI"' in guidance
    assert "never set max_distance/max_distance_m on spatial_nearest" in guidance
