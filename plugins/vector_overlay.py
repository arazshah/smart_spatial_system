"""
vector_overlay.py

GeoChat SDK Plugin
==================

Plugin ID:
    vector_overlay

Purpose:
    Polygon/geometry overlay operations that complete the overlay family next
    to intersect_features and dissolve_features.

Capabilities:
    - clip_features:
        Cut features to the area covered by a mask layer (geometry and
        attributes of the input are kept, only the part inside the mask stays).
    - erase_features:
        Remove the area covered by an erase layer from the input features
        ("difference").
    - union_features:
        Planar union overlay of two polygon layers: every resulting piece is
        covered by source only, target only, or both, and carries the
        attributes of whichever layers cover it.

Config-aware behavior:
    Reads config/plugins/vector_overlay.yaml.

Important:
    GeoJSON coordinates are treated as planar coordinates; no CRS
    transformation is performed. Inputs larger than max_features or results
    larger than max_output_features are refused rather than truncated.
"""

from __future__ import annotations

from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    get_shapely,
    load_config,
    make_vector_out,
    to_int,
)

PLUGIN_ID = "vector_overlay"

DEFAULT_MAX_FEATURES = 100_000
DEFAULT_MAX_OUTPUT_FEATURES = 200_000
_POLYGONAL = {"Polygon", "MultiPolygon"}
_DIMENSION = {"Point": 0, "MultiPoint": 0, "LineString": 1, "MultiLineString": 1, "Polygon": 2,
              "MultiPolygon": 2}


def _limits(config: dict[str, Any]) -> tuple[int, int]:
    return (
        to_int(config.get("max_features", DEFAULT_MAX_FEATURES), "max_features"),
        to_int(config.get("max_output_features", DEFAULT_MAX_OUTPUT_FEATURES), "max_output_features"),
    )


def _repair(geom: Any) -> Any:
    """Return a valid geometry (shapely make_valid, buffer(0) as a fallback)."""
    if geom.is_valid:
        return geom
    try:
        from shapely import make_valid

        return make_valid(geom)
    except ImportError:  # pragma: no cover - shapely < 1.8
        return geom.buffer(0)


def _shapes(features: list[dict[str, Any]], repair: bool) -> list[tuple[int, Any]]:
    shape, _, _ = get_shapely(PLUGIN_ID)
    out: list[tuple[int, Any]] = []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        out.append((index, _repair(geom) if repair else geom))
    return out


def _keep_dimension(geom: Any, dimension: int) -> Any | None:
    """Keep only parts of ``geom`` with the wanted dimension (drops slivers of lower
    dimension that intersection() often produces, e.g. a shared edge)."""
    from shapely.geometry import (
        GeometryCollection,
        MultiLineString,
        MultiPoint,
        MultiPolygon,
    )

    if geom.is_empty:
        return None
    parts = list(geom.geoms) if isinstance(geom, GeometryCollection) or hasattr(geom, "geoms") else [geom]
    kept = [p for p in parts if _DIMENSION.get(p.geom_type, -1) == dimension]
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    flat: list[Any] = []
    for part in kept:
        flat.extend(part.geoms if hasattr(part, "geoms") else [part])
    return {0: MultiPoint, 1: MultiLineString, 2: MultiPolygon}[dimension](flat)


def _mask_union(mask_features: list[dict[str, Any]], repair: bool) -> Any | None:
    _, _, unary_union = get_shapely(PLUGIN_ID)
    geoms = [g for _, g in _shapes(mask_features, repair)]
    return unary_union(geoms) if geoms else None


def _apply(
    features: Any,
    other: Any,
    *,
    operation: str,
    keep_geom_type: bool,
    repair_invalid: bool,
    metadata: dict[str, Any] | None,
) -> VectorOut:
    _, mapping, _ = get_shapely(PLUGIN_ID)
    config = load_config(PLUGIN_ID)
    max_features, max_output = _limits(config)

    input_features, source_info = extract_features(features)
    other_features, _ = extract_features(other)
    check_limit(len(input_features), max_features, "input feature count")
    check_limit(len(other_features), max_features, "mask/erase feature count")

    other_union = _mask_union(other_features, bool(repair_invalid))
    results: list[dict[str, Any]] = []

    for index, geom in _shapes(input_features, bool(repair_invalid)):
        if other_union is None:
            # nothing to clip with -> clip keeps nothing, erase keeps everything
            result = None if operation == "clip" else geom
        elif operation == "clip":
            result = geom.intersection(other_union)
        else:
            result = geom.difference(other_union)

        if result is not None and keep_geom_type:
            result = _keep_dimension(result, _DIMENSION.get(geom.geom_type, 2))
        if result is None or result.is_empty:
            continue

        properties = dict(input_features[index].get("properties") or {})
        properties["source_feature_index"] = index
        results.append({"type": "Feature", "geometry": dict(mapping(result)), "properties": properties})
        check_limit(len(results), max_output, "output feature count")

    return make_vector_out(
        results,
        plugin_id=PLUGIN_ID,
        operation=operation,
        source_info=source_info,
        extra={
            "input_feature_count": len(input_features),
            "other_feature_count": len(other_features),
            "keep_geom_type": bool(keep_geom_type),
        },
        user_metadata=metadata,
    )


@capability(
    name="clip_features",
    keywords=[
        "clip", "clip features", "cut to boundary", "crop vector", "within boundary",
        "برش", "برش لایه", "کلیپ", "برش با محدوده", "محدود کردن به محدوده",
    ],
    description=(
        "Clip vector features to the area covered by a mask layer: geometries are cut at the "
        "mask boundary and attributes are kept."
    ),
    required_inputs=["features", "mask"],
    optional_inputs=["keep_geom_type", "repair_invalid", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "clip",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def clip_features(
    features: Any,
    mask: Any,
    keep_geom_type: bool = True,
    repair_invalid: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Clip ``features`` to the union of ``mask`` geometries.

    Args:
        features: Features to clip (VectorOut, FeatureCollection, Feature or list).
        mask: Mask layer, normally polygons. Union of all its geometries is used.
        keep_geom_type: Drop lower-dimension leftovers (e.g. a polygon touching the
            mask only along an edge) so polygons stay polygons.
        repair_invalid: Repair invalid geometries before the overlay.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the clipped features (empty results are dropped).
    """
    return _apply(
        features,
        mask,
        operation="clip",
        keep_geom_type=keep_geom_type,
        repair_invalid=repair_invalid,
        metadata=metadata,
    )


@capability(
    name="erase_features",
    keywords=[
        "erase", "difference", "subtract", "remove area", "cut out", "exclude area",
        "پاک کردن", "تفاضل", "حذف محدوده", "کم کردن", "خارج کردن ناحیه",
    ],
    description=(
        "Erase (difference): remove the area covered by an erase layer from the input features."
    ),
    required_inputs=["features", "erase"],
    optional_inputs=["keep_geom_type", "repair_invalid", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "erase",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def erase_features(
    features: Any,
    erase: Any,
    keep_geom_type: bool = True,
    repair_invalid: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Subtract the union of ``erase`` geometries from ``features``.

    Args:
        features: Features to erase from.
        erase: Layer whose area is removed.
        keep_geom_type: Keep only parts with the input geometry dimension.
        repair_invalid: Repair invalid geometries before the overlay.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the remaining parts (fully erased features are dropped).
    """
    return _apply(
        features,
        erase,
        operation="erase",
        keep_geom_type=keep_geom_type,
        repair_invalid=repair_invalid,
        metadata=metadata,
    )


def _polygon_layer(features: list[dict[str, Any]], name: str, repair: bool) -> list[tuple[int, Any]]:
    layer = _shapes(features, repair)
    for _, geom in layer:
        if geom.geom_type not in _POLYGONAL:
            raise ValueError(f"{name} must contain only Polygon/MultiPolygon geometries.")
    return layer


def _prefixed(properties: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in (properties or {}).items()}


@capability(
    name="union_features",
    keywords=[
        "union", "union overlay", "merge layers", "combine polygons", "overlay union",
        "اتحاد", "ادغام لایه ها", "ترکیب پلی گون", "اجتماع دو لایه", "یونیون",
    ],
    description=(
        "Union overlay of two polygon layers: splits both layers into non-overlapping pieces "
        "and keeps the attributes of the source and/or target polygon covering each piece."
    ),
    required_inputs=["source_features", "target_features"],
    optional_inputs=["source_prefix", "target_prefix", "repair_invalid", "metadata"],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "union",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def union_features(
    source_features: Any,
    target_features: Any,
    source_prefix: str = "source_",
    target_prefix: str = "target_",
    repair_invalid: bool = True,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Union overlay of two polygon layers.

    Every output piece gets ``overlay_origin`` = ``both`` | ``source`` | ``target``. Its
    properties are the covering source feature's (prefixed with ``source_prefix``) and the
    covering target feature's (``target_prefix``). If several features of one layer cover the
    same piece, the first one provides the attributes and ``source_overlap_count`` /
    ``target_overlap_count`` tell how many there were.

    Args:
        source_features: First polygon layer.
        target_features: Second polygon layer.
        source_prefix: Prefix for source attribute names.
        target_prefix: Prefix for target attribute names.
        repair_invalid: Repair invalid geometries before the overlay.
        metadata: Extra output metadata.

    Returns:
        VectorOut with the pieces of the union (holes of the union are not output).
    """
    if source_prefix == target_prefix:
        raise ValueError("source_prefix and target_prefix must differ.")

    _, mapping, unary_union = get_shapely(PLUGIN_ID)
    from shapely import STRtree
    from shapely.ops import polygonize

    config = load_config(PLUGIN_ID)
    max_features, max_output = _limits(config)

    source_in, source_info = extract_features(source_features)
    target_in, _ = extract_features(target_features)
    check_limit(len(source_in), max_features, "source feature count")
    check_limit(len(target_in), max_features, "target feature count")

    layers = {
        "source": _polygon_layer(source_in, "source_features", bool(repair_invalid)),
        "target": _polygon_layer(target_in, "target_features", bool(repair_invalid)),
    }
    all_geoms = [g for layer in layers.values() for _, g in layer]
    if not all_geoms:
        return make_vector_out(
            [], plugin_id=PLUGIN_ID, operation="union", source_info=source_info,
            user_metadata=metadata,
        )

    trees = {name: STRtree([g for _, g in layer]) for name, layer in layers.items()}
    pieces = polygonize(unary_union([g.boundary for g in all_geoms]))

    results: list[dict[str, Any]] = []
    for piece in pieces:
        probe = piece.representative_point()
        covering: dict[str, list[int]] = {}
        for name, layer in layers.items():
            hits = trees[name].query(probe, predicate="intersects")
            covering[name] = sorted(int(layer[i][0]) for i in hits)
        if not covering["source"] and not covering["target"]:
            continue  # a hole of the union

        properties: dict[str, Any] = {}
        if covering["source"]:
            properties.update(_prefixed(source_in[covering["source"][0]].get("properties"), source_prefix))
        if covering["target"]:
            properties.update(_prefixed(target_in[covering["target"][0]].get("properties"), target_prefix))
        properties["overlay_origin"] = (
            "both" if covering["source"] and covering["target"]
            else "source" if covering["source"] else "target"
        )
        properties["source_overlap_count"] = len(covering["source"])
        properties["target_overlap_count"] = len(covering["target"])
        results.append({"type": "Feature", "geometry": dict(mapping(piece)), "properties": properties})
        check_limit(len(results), max_output, "output feature count")

    return make_vector_out(
        results,
        plugin_id=PLUGIN_ID,
        operation="union",
        source_info=source_info,
        extra={
            "source_feature_count": len(source_in),
            "target_feature_count": len(target_in),
        },
        user_metadata=metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Vector Overlay",
    description="Clip, erase (difference) and union overlay operations for vector layers.",
    author="GeoChat Platform Team",
    permissions=[],
)
