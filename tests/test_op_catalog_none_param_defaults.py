"""
Explicit-None handling for OP_CATALOG params with non-None Python defaults.

LLM-generated plans often include every param _op_param_reference() advertises,
using null for the ones they don't want to set. _map_params() and
_build_kwargs() used to pass that None straight through, and an explicit None
overrides a Python default instead of triggering it. So
filter_features(sort_order=None) raised "sort_order must be a non-empty
string." (filter_attribute / sort_limit), even though sort_order has a real
default of "asc" and the sibling param bbox_mode already tolerated None via
pick_first().

Fixed twice over:
- spatial_query_filter.filter_features resolves sort_order via pick_first(),
  like bbox_mode.
- DagExecutor drops None-valued static params whose target keyword has a
  non-None signature default and an annotation that doesn't accept None
  (e.g. sort_order: str = "asc"), so the capability's real default applies.
  Params typed to accept None (precision: int | None = 6) keep it. This
  closes the same gap for every other operation too (e.g. rank_features
  descending=None used to silently sort ascending; filter_points_in_polygon
  predicate=None raised).
"""

from __future__ import annotations

import inspect
import typing

import pytest

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.dag import DagNode, DagPlan
from orchestrator.planning.dag_executor import DagExecutor, _annotation_allows_none, _build_kwargs
from orchestrator.planning.op_catalog import OP_CATALOG
from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES
from plugins.spatial_query_filter import filter_features

EMPTY_FC = {"type": "FeatureCollection", "features": []}


@pytest.fixture(scope="module")
def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_plugin_modules(DEFAULT_SAFE_PLUGIN_MODULES)


def _points_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(i), 0.0]},
                "properties": {"name": name, "score": score},
            }
            for i, (name, score) in enumerate([("a", 1.0), ("b", 3.0), ("c", 2.0)])
        ],
    }


def test_filter_features_tolerates_explicit_none_sort_order() -> None:
    result = filter_features(features=EMPTY_FC, sort_order=None)
    assert result.metadata["sort_order"] == "asc"

    # Consistent with the sibling param that already tolerated None.
    result = filter_features(features=EMPTY_FC, bbox_mode=None)
    assert result.metadata["bbox_mode"] == "intersects"


def test_filter_features_still_rejects_invalid_sort_order() -> None:
    with pytest.raises(ValueError):
        filter_features(features=EMPTY_FC, sort_order="sideways")


@pytest.mark.parametrize("op_name", ["filter_attribute", "sort_limit"])
def test_filter_ops_execute_with_null_sort_order(registry: CapabilityRegistry, op_name: str) -> None:
    descriptor = OP_CATALOG[op_name]
    assert descriptor.param_map.get("sort_order") == "sort_order"

    plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name=descriptor.capability_name,
                inputs={"features": "$inputs.layer"},
                static_params={"sort_by": None, "sort_order": None, "limit": None},
                produces="vector",
            )
        ],
        output_nodes=["n1"],
    )

    result = DagExecutor(RegistryCapabilityResolver(registry)).execute(
        plan, initial_inputs={"layer": _points_fc()}
    )

    assert result.success is True, result.error
    assert len(result.output_nodes["n1"].features) == 3


def test_every_non_none_default_catalog_param_drops_explicit_none(registry: CapabilityRegistry) -> None:
    """
    For every OP_CATALOG param with a non-None default, an explicit None must
    either be dropped (so the default applies) or be something the
    capability's annotation declares it accepts (e.g. `str | None`), in which
    case the capability handles None itself.
    """
    checked = 0
    leaks: list[str] = []

    for op_name, descriptor in OP_CATALOG.items():
        try:
            binding = registry.resolve(descriptor.capability_name)
        except ValueError:
            continue

        params = inspect.signature(binding.callable).parameters
        hints = typing.get_type_hints(binding.callable)
        for source_key, target_key in descriptor.param_map.items():
            param = params.get(target_key)
            if param is None or param.default is inspect.Parameter.empty or param.default is None:
                continue

            node = DagNode(id="n", capability_name=descriptor.capability_name, static_params={target_key: None})
            kwargs = _build_kwargs(node, initial_inputs={}, state={}, capability_fn=binding.callable)
            checked += 1
            annotation = hints.get(target_key, param.annotation)
            if target_key in kwargs and not _annotation_allows_none(annotation):
                leaks.append(
                    f"op '{op_name}' -> {descriptor.capability_name}({target_key}=None) "
                    f"would override its default {param.default!r} (annotation {annotation!r})"
                )

    assert checked > 0
    assert not leaks, "Explicit None overrides a real default:\n" + "\n".join(leaks)


def test_none_is_kept_unless_default_is_real_and_annotation_excludes_none() -> None:
    def capability(
        features,
        limit=None,
        mode: str = "a",
        precision: int | None = 6,
        **extra,
    ):
        return None

    node = DagNode(
        id="n",
        capability_name="capability",
        static_params={"limit": None, "mode": None, "precision": None, "unknown": None, "other": 1},
    )
    kwargs = _build_kwargs(node, initial_inputs={}, state={}, capability_fn=capability)

    assert kwargs == {"limit": None, "precision": None, "unknown": None, "other": 1}


def test_explicit_none_is_kept_where_the_capability_gives_it_meaning() -> None:
    # calculate_attribute_statistics(precision: int | None = 6): None means
    # "don't round". The executor must not rewrite that to the default 6.
    from plugins.attribute_statistics import calculate_attribute_statistics

    node = DagNode(id="n", capability_name="calculate_attribute_statistics", static_params={"precision": None})
    kwargs = _build_kwargs(node, initial_inputs={}, state={}, capability_fn=calculate_attribute_statistics)

    assert kwargs == {"precision": None}


def test_rank_features_descending_null_keeps_real_default(registry: CapabilityRegistry) -> None:
    plan = DagPlan(
        nodes=[
            DagNode(
                id="n1",
                capability_name="rank_features",
                inputs={"features": "$inputs.layer"},
                static_params={"descending": None},
                produces="vector",
            )
        ],
        output_nodes=["n1"],
    )

    result = DagExecutor(RegistryCapabilityResolver(registry)).execute(
        plan, initial_inputs={"layer": _points_fc()}
    )

    assert result.success is True, result.error
    names = [f["properties"]["name"] for f in result.output_nodes["n1"].features]
    assert names == ["b", "c", "a"]
