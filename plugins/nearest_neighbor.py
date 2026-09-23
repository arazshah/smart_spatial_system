"""
nearest_neighbor.py

GeoChat SDK Plugin
==================

Plugin ID:
    nearest_neighbor

Purpose:
    Find k nearest target features for each source feature using planar distance.

Capability:
    - find_nearest_neighbors

Engines:
    - auto:
        Use shapely if available, otherwise pure-python fallback from distance_calculator.
    - shapely:
        Robust geometry distance through shapely. Candidate search is
        accelerated with an STRtree (shapely.strtree) instead of a
        brute-force scan of every target per source, whenever shapely is
        importable and every target geometry parses cleanly - see
        _prepare_target_index()/_strtree_candidates() below. Falls back
        to the original per-pair nested loop otherwise (shapely missing,
        or a target geometry that doesn't parse), so behavior on that
        rare path stays exactly what it always was.
    - python:
        Pure-python planar distance fallback. Always uses the original
        per-pair nested loop - the indexed path is shapely-only by
        design, since it relies on shapely's own distance() and STRtree.

Important:
    Calculations are planar, not geodesic. Reproject geographic data first
    using crs_transformer for reliable meter-based distances.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect
from geochat_sdk.types.vector import VectorOut

from plugins._shared.plugin_config import (
    load_plugin_config,
    pick_first,
    resolve_env_refs,
)
from plugins.distance_calculator import (
    _calculate_distance,
    _geometry_bbox,
    _is_geographic_crs,
    _raise_if_crs_mismatch,
    _validate_engine,
)

_WGS84 = "EPSG:4326"

PLUGIN_ID = "nearest_neighbor"


def _load_nearest_config() -> dict[str, Any]:
    """
    Load config/plugins/nearest_neighbor.yaml if available.
    """
    config = load_plugin_config(PLUGIN_ID, required=False)
    if not config:
        return {}
    return resolve_env_refs(config)


def _utc_now_iso() -> str:
    """
    Return current UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _configured_precision(config: dict[str, Any]) -> int | None:
    """
    Return coordinate/distance precision.
    """
    value = config.get("coordinate_precision", 6)

    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("coordinate_precision must be an integer or null.")

    try:
        precision = int(value)
    except Exception as exc:
        raise ValueError("coordinate_precision must be an integer or null.") from exc

    if precision < 0:
        raise ValueError("coordinate_precision must be >= 0.")

    if precision > 15:
        raise ValueError("coordinate_precision is too large. Maximum allowed value is 15.")

    return precision


def _round_value(value: float | None, precision: int | None) -> float | None:
    """
    Round distance value.
    """
    if value is None:
        return None

    if precision is None:
        return float(value)

    return round(float(value), precision)


def _validate_k(value: Any, *, max_k: int | None = None) -> int:
    """
    Validate k nearest count.
    """
    if isinstance(value, bool):
        raise ValueError("k must be a positive integer.")

    try:
        k = int(value)
    except Exception as exc:
        raise ValueError("k must be a positive integer.") from exc

    if k <= 0:
        raise ValueError("k must be > 0.")

    if max_k is not None and k > max_k:
        raise ValueError(f"k is too large. Maximum allowed value is {max_k}.")

    return k


def _validate_max_distance(value: Any) -> float | None:
    """
    Validate max_distance.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("max_distance must be numeric or None.")

    try:
        distance = float(value)
    except Exception as exc:
        raise ValueError("max_distance must be numeric or None.") from exc

    if distance < 0:
        raise ValueError("max_distance must be >= 0.")

    return distance


def _configured_fields(config: dict[str, Any]) -> dict[str, str]:
    """
    Return output field names.
    """
    fields = config.get("fields") or {}

    if not isinstance(fields, dict):
        raise ValueError("fields in nearest_neighbor config must be a dict.")

    return {
        "distance_field": str(fields.get("distance_field", "_nearest_distance")),
        "rank_field": str(fields.get("rank_field", "_neighbor_rank")),
        "source_index_field": str(fields.get("source_index_field", "_source_index")),
        "target_index_field": str(fields.get("target_index_field", "_target_index")),
        "status_field": str(fields.get("status_field", "_nearest_status")),
        "engine_field": str(fields.get("engine_field", "_nearest_engine")),
        "target_properties_field": str(fields.get("target_properties_field", "_target_properties")),
        "target_geometry_field": str(fields.get("target_geometry_field", "_target_geometry")),
    }


def _normalize_feature(feature: dict[str, Any], index: int) -> dict[str, Any]:
    """
    Normalize GeoJSON Feature.
    """
    if not isinstance(feature, dict):
        raise ValueError(f"Feature at index {index} must be a dict/object.")

    if feature.get("type") != "Feature":
        raise ValueError(f"Item at index {index} is not a GeoJSON Feature.")

    properties = feature.get("properties")
    if properties is None:
        properties = {}

    if not isinstance(properties, dict):
        raise ValueError(f"Feature properties at index {index} must be dict/object or null.")

    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": dict(properties),
    }


def _extract_features(input_data: Any, label: str = "features") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract features from VectorOut, FeatureCollection, Feature, or list[Feature].
    """
    source_info: dict[str, Any] = {}

    if hasattr(input_data, "features") and not isinstance(input_data, (dict, list)):
        raw_features = getattr(input_data, "features")
        source_info[f"{label}_input_type"] = type(input_data).__name__

        source_metadata = getattr(input_data, "metadata", None)
        if isinstance(source_metadata, dict):
            source_info[f"{label}_input_metadata"] = source_metadata

    elif isinstance(input_data, dict):
        geojson_type = input_data.get("type")
        source_info[f"{label}_input_geojson_type"] = geojson_type

        if geojson_type == "FeatureCollection":
            raw_features = input_data.get("features", [])
            if not isinstance(raw_features, list):
                raise ValueError(f"{label}.features must be a list.")
        elif geojson_type == "Feature":
            raw_features = [input_data]
        else:
            raise ValueError(f"{label} dict must be GeoJSON FeatureCollection or Feature.")

    elif isinstance(input_data, list):
        raw_features = input_data
        source_info[f"{label}_input_geojson_type"] = "FeatureList"

    else:
        raise ValueError(f"{label} must be VectorOut, list, FeatureCollection dict or Feature dict.")

    if not isinstance(raw_features, list):
        raise ValueError(f"Extracted {label} must be a list.")

    features = [_normalize_feature(item, idx) for idx, item in enumerate(raw_features)]
    return features, source_info


def _make_neighbor_feature(
    *,
    source_feature: dict[str, Any],
    source_index: int,
    target_feature: dict[str, Any] | None,
    target_index: int | None,
    distance: float | None,
    rank: int | None,
    engine_used: str,
    fields: dict[str, str],
    precision: int | None,
    preserve_properties: bool,
    include_target_geometry: bool,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Build output nearest-neighbor feature.
    """
    properties = deepcopy(source_feature.get("properties") or {}) if preserve_properties else {}

    properties[fields["distance_field"]] = _round_value(distance, precision)
    properties[fields["rank_field"]] = rank
    properties[fields["source_index_field"]] = source_index
    properties[fields["target_index_field"]] = target_index
    properties[fields["status_field"]] = status
    properties[fields["engine_field"]] = engine_used

    if target_feature is not None:
        properties[fields["target_properties_field"]] = deepcopy(target_feature.get("properties") or {})

        if include_target_geometry:
            properties[fields["target_geometry_field"]] = deepcopy(target_feature.get("geometry"))

    if reason:
        properties["_nearest_reason"] = reason

    return {
        "type": "Feature",
        "geometry": source_feature.get("geometry"),
        "properties": properties,
    }


def _merge_bbox_arrays(bboxes: list[list[float]]) -> dict[str, float] | None:
    """
    Merge bbox arrays.
    """
    valid = [b for b in bboxes if b and len(b) == 4]

    if not valid:
        return None

    return {
        "minx": min(b[0] for b in valid),
        "miny": min(b[1] for b in valid),
        "maxx": max(b[2] for b in valid),
        "maxy": max(b[3] for b in valid),
    }


def _build_vector_metadata(features: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build VectorOut metadata.
    """
    geometry_types: dict[str, int] = {}
    bboxes: list[list[float]] = []

    for feature in features:
        geometry = feature.get("geometry")

        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "Unknown")
            try:
                bbox = _geometry_bbox(geometry)
                if bbox is not None:
                    bboxes.append(bbox)
            except Exception:
                pass
        elif geometry is None:
            gtype = "Null"
        else:
            gtype = "Invalid"

        geometry_types[gtype] = geometry_types.get(gtype, 0) + 1

    return {
        "feature_count": len(features),
        "geometry_types": geometry_types,
        "bounds": _merge_bbox_arrays(bboxes),
    }


def _strtree_query_indices(tree: Any, geometry: Any, geometries: list[Any]) -> list[int]:
    """
    Query an STRtree and return integer indices into `geometries`.

    Handles both STRtree.query() return shapes across shapely versions,
    same approach as plugins/spatial_join.py's helper of the same name:
        - shapely>=2.0: an array-like of integer positions into the list
          the tree was built from.
        - shapely<2.0: a list of the matched geometry objects themselves,
          not indices - resolved back to positions by identity, since the
          tree stores references to the exact objects it was built from.
    """
    result = tree.query(geometry)

    if len(result) == 0:
        return []

    first = result[0]

    if hasattr(first, "geom_type"):
        id_to_index = {id(g): i for i, g in enumerate(geometries)}
        return [id_to_index[id(g)] for g in result if id(g) in id_to_index]

    return [int(i) for i in result]


def _prepare_target_index(
    target_items: list[dict[str, Any]],
    final_engine: str,
) -> tuple[Any, Any, list[Any], list[int]]:
    """
    Try to build an STRtree over target_items' geometries, for the
    STRtree-accelerated candidate search in find_nearest_neighbors().

    Returns (shape_fn, tree, tree_geoms, tree_indices). shape_fn is None
    (and the accelerated path unavailable) whenever it can't be used
    safely, and the caller must fall back to the original per-pair
    nested loop entirely for every source:
        - engine is explicitly "python" - the accelerated path is
          shapely-only by design (see the module docstring).
        - shapely itself is not importable.
        - any target geometry fails to parse (rare malformed input;
          falling back keeps that case's error handling identical to
          the pre-existing per-pair try/except instead of approximating
          it for a build-once index).

    tree_geoms/tree_indices exclude targets with a missing (None) or
    empty geometry - shapely.distance()/STRtree can't index those, and
    the original code already treats them as "no distance" (see
    _shapely_distance_geometry), not a match candidate.
    """
    if final_engine == "python":
        return None, None, [], []

    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree
    except ImportError:
        return None, None, [], []

    tree_geoms: list[Any] = []
    tree_indices: list[int] = []

    for idx, item in enumerate(target_items):
        geometry = item.get("geometry")
        if geometry is None:
            continue
        try:
            geom = shape(geometry)
        except Exception:
            return None, None, [], []
        if geom.is_empty:
            continue
        tree_geoms.append(geom)
        tree_indices.append(idx)

    tree = STRtree(tree_geoms) if tree_geoms else None
    return shape, tree, tree_geoms, tree_indices


def _initial_radius_guess(tree_geoms: list[Any]) -> float:
    """
    Rough expected nearest-neighbor spacing, used only to pick a
    starting search radius for _strtree_candidates()'s growing search -
    a bad guess costs a few extra doublings, not correctness (see that
    function's docstring for the correctness argument, which does not
    depend on this estimate).
    """
    try:
        minx = min(g.bounds[0] for g in tree_geoms)
        miny = min(g.bounds[1] for g in tree_geoms)
        maxx = max(g.bounds[2] for g in tree_geoms)
        maxy = max(g.bounds[3] for g in tree_geoms)
    except Exception:
        return 1.0

    diagonal = math.hypot(maxx - minx, maxy - miny)
    n = len(tree_geoms)
    if diagonal <= 0 or n <= 0:
        return 1.0

    estimate = diagonal / math.sqrt(n)
    return estimate if estimate > 0 else 1.0


def _strtree_candidates(
    source_geom: Any,
    tree: Any,
    tree_geoms: list[Any],
    tree_indices: list[int],
    k: int,
    max_distance: float | None,
    initial_radius: float,
) -> list[tuple[float, int]]:
    """
    Return up to k (distance, target_index) pairs - targets from
    tree_geoms/tree_indices nearest to source_geom - sorted by
    (distance, target_index), the same order and tie-break a full
    pairwise scan followed by candidate_rows.sort() would produce.

    Correctness of the growing search below: querying
    tree.query(source_geom.buffer(r)) returns every target whose
    bounding box intersects source_geom's bounding box expanded by r in
    every direction. If a target's true distance to source_geom is <=
    r, some point of that target lies within r of source_geom, and
    that point lies within source_geom's expanded bounding box - so the
    target's own bounding box must intersect it too. A target NOT
    returned by that query is therefore guaranteed to have true
    distance > r. Growing r until at least k confirmed candidates are
    found with their k-th real distance <= r proves no closer,
    unqueried target exists - the same guarantee an exhaustive scan
    gives, just without computing an exact distance to every target.

    (This buffer+bbox-query approach, rather than shapely 2.x's
    query(..., predicate="dwithin", distance=r), matches
    _strtree_query_indices()'s existing shapely<2.0 compatibility
    handling elsewhere in this codebase - see plugins/spatial_join.py.)
    """
    n = len(tree_geoms)
    if n == 0 or tree is None:
        return []

    if max_distance is not None:
        hit_positions = _strtree_query_indices(tree, source_geom.buffer(max_distance), tree_geoms)
        candidates = [
            (float(source_geom.distance(tree_geoms[pos])), tree_indices[pos])
            for pos in hit_positions
        ]
        candidates = [item for item in candidates if item[0] <= max_distance]
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[:k]

    radius = initial_radius

    # Doubling from any positive start converges in O(log(true_radius /
    # start)) steps; 200 doublings covers any representable float range
    # many times over, so this cap only exists to guarantee termination,
    # not to affect correctness (the exhaustive fallback below always
    # returns the exact answer regardless of how the loop exits).
    for _ in range(200):
        hit_positions = _strtree_query_indices(tree, source_geom.buffer(radius), tree_geoms)
        exhaustive = len(hit_positions) >= n
        if exhaustive:
            hit_positions = range(n)

        candidates = [
            (float(source_geom.distance(tree_geoms[pos])), tree_indices[pos])
            for pos in hit_positions
        ]
        candidates.sort(key=lambda item: (item[0], item[1]))

        if exhaustive or (len(candidates) >= k and candidates[k - 1][0] <= radius):
            return candidates[:k]

        radius *= 2

    candidates = [
        (float(source_geom.distance(tree_geoms[pos])), tree_indices[pos]) for pos in range(n)
    ]
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[:k]


def _brute_force_candidates(
    source_feature: dict[str, Any],
    target_items: list[dict[str, Any]],
    final_engine: str,
    final_max_distance: float | None,
    engines_used: set[str],
) -> tuple[list[tuple[float, int, dict[str, Any], str]], int, int, str, str | None]:
    """
    Original per-pair nested-loop candidate search for one source
    feature against every target - unindexed, unchanged from before the
    STRtree-accelerated path existed. Used as the exact-fidelity
    fallback whenever that path can't be used: engine="python", shapely
    unavailable, or a target/source geometry that doesn't parse.

    Returns (candidate_rows, pair_count_delta, failed_pair_count_delta,
    last_engine_used, last_error) - candidate_rows sorted by
    (distance, target_index), same as the accelerated path returns.
    """
    candidate_rows: list[tuple[float, int, dict[str, Any], str]] = []
    last_engine_used = final_engine
    last_error: str | None = None
    pair_count_delta = 0
    failed_pair_count_delta = 0

    for target_index, target_feature in enumerate(target_items):
        pair_count_delta += 1

        try:
            distance, engine_used = _calculate_distance(
                source_geometry=source_feature.get("geometry"),
                target_geometry=target_feature.get("geometry"),
                engine=final_engine,
            )
            engines_used.add(engine_used)
            last_engine_used = engine_used

            if distance is None:
                failed_pair_count_delta += 1
                continue

            if final_max_distance is not None and distance > final_max_distance:
                continue

            candidate_rows.append((float(distance), target_index, target_feature, engine_used))

        except Exception as exc:
            failed_pair_count_delta += 1
            last_error = str(exc)
            engines_used.add(final_engine)

    candidate_rows.sort(key=lambda item: (item[0], item[1]))
    return candidate_rows, pair_count_delta, failed_pair_count_delta, last_engine_used, last_error


def _crs_area_of_use_warning(
    final_source_crs: str | None,
    final_target_crs: str | None,
    source_items: list[dict[str, Any]],
    target_items: list[dict[str, Any]],
) -> str | None:
    """
    Warn when the projected CRS this data is claimed to already be in
    (source_crs/target_crs - see find_nearest_neighbors' docstring; this
    function never reprojects anything itself) has a published
    area_of_use that does not actually cover where the data is.

    This catches a different failure than warn_if_geographic_crs above:
    a plan that dutifully reprojects both layers to the SAME projected
    CRS still produces geometrically self-consistent but physically
    meaningless distances if that CRS was never meant to cover this part
    of the planet (e.g. reprojecting Istanbul data to EPSG:31256, an
    Austrian grid) - warn_if_geographic_crs only catches "still in
    degrees", never "projected to the wrong part of the planet".

    Recovers where the data actually is by inverse-transforming the
    centroid of its (already-reprojected) coordinates from the claimed
    CRS back to EPSG:4326 - correct exactly when the source_crs/
    target_crs hint is accurate, the same trust assumption every other
    CRS check in this module already makes.

    Deliberately conservative: gives no warning (never raises) whenever
    pyproj is missing, the CRS string can't be resolved, the CRS is
    geographic, it has no published area_of_use, there is no finite
    input geometry to check, or the inverse transform fails/produces
    non-finite output. A missed warning here, not a false one.
    """
    crs_value = final_source_crs or final_target_crs
    if not crs_value:
        return None

    try:
        from pyproj import CRS, Transformer
    except ImportError:
        return None

    try:
        crs = CRS.from_user_input(crs_value)
    except Exception:
        return None

    if crs.is_geographic:
        return None

    area = crs.area_of_use
    if area is None:
        return None

    bboxes: list[list[float]] = []
    for item in (*source_items, *target_items):
        geometry = item.get("geometry")
        if not isinstance(geometry, dict):
            continue
        try:
            bbox = _geometry_bbox(geometry)
        except Exception:
            continue
        if bbox:
            bboxes.append(bbox)

    merged = _merge_bbox_arrays(bboxes)
    if merged is None:
        return None

    centroid_x = (merged["minx"] + merged["maxx"]) / 2
    centroid_y = (merged["miny"] + merged["maxy"]) / 2

    try:
        transformer = Transformer.from_crs(crs, _WGS84, always_xy=True)
        lon, lat = transformer.transform(centroid_x, centroid_y)
    except Exception:
        return None

    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None

    west, south, east, north = area.west, area.south, area.east, area.north
    lon_in_range = (west <= lon <= east) if west <= east else (lon >= west or lon <= east)

    if lon_in_range and south <= lat <= north:
        return None

    area_label = area.name or f"{west:.2f},{south:.2f} to {east:.2f},{north:.2f}"

    return (
        f"{crs_value} was used to calculate nearest-neighbor distance, but its "
        f"published area of use ({area_label}) does not appear to cover this "
        f"data's location (recovered centroid ~ lon={lon:.4f}, lat={lat:.4f} in "
        f"{_WGS84}). Both layers being consistently reprojected to the same CRS "
        "does not make that CRS geographically appropriate for this data - "
        "choose a projected CRS whose area of use actually contains it."
    )


def _max_distance_exclusion_warning(
    final_max_distance: float | None,
    unmatched_source_count: int,
    source_feature_count: int,
    warning_fraction: float,
) -> str | None:
    """
    Warn when max_distance silently discarded a large share of source
    features before ranking even started.

    max_distance behaves exactly as documented (drop out-of-range
    candidates before ranking) and a downstream filter/sort step behaves
    exactly as documented too - but nothing links the two, so a plan that
    sets max_distance low enough to exclude most sources and then filters
    on this op's own distance output can silently produce an
    (incorrectly) empty result with no signal anywhere that max_distance
    is why. unmatched_source_count is already computed regardless of
    drop_unmatched, so this is a read of existing state, not new work.

    Fires only when max_distance is actually set and the excluded share
    is at least warning_fraction of all source features - a source or
    two failing to find any target within range is normal and not worth
    flagging.
    """
    if final_max_distance is None or unmatched_source_count <= 0 or source_feature_count <= 0:
        return None

    fraction = unmatched_source_count / source_feature_count
    if fraction < warning_fraction:
        return None

    return (
        f"max_distance={final_max_distance} excluded {unmatched_source_count} of "
        f"{source_feature_count} source feature(s) from ranking "
        f"({fraction:.0%}). If a downstream step filters or sorts on this "
        "operation's distance output expecting results for these features, "
        "that step may unexpectedly return nothing - consider raising or "
        "removing max_distance."
    )


@capability(
    name="find_nearest_neighbors",
    keywords=[
        "nearest neighbor",
        "nearest neighbours",
        "nearest feature",
        "closest feature",
        "k nearest",
        "knn",
        "proximity",
        "find closest",
        "نزدیکترین همسایه",
        "نزدیک‌ترین همسایه",
        "نزدیکترین عارضه",
        "نزدیک‌ترین عارضه",
        "k نزدیکترین",
        "تحلیل مجاورت",
    ],
    description="Find k nearest target features for each source vector feature.",
    required_inputs=["source_features", "target_features"],
    optional_inputs=[
        "k",
        "max_distance",
        "engine",
        "precision",
        "drop_unmatched",
        "include_target_geometry",
        "source_crs",
        "target_crs",
        "metadata",
    ],
    output_kind="vector",
    permissions=[],
    metadata={
        "category": "analysis",
        "data_type": "vector",
        "operation": "nearest_neighbor",
        "returns": "VectorOut",
        "artifact_kind": "features",
        "access_scope": "spatial_analysis",
        "config_aware": True,
        "requires_shapely_for_robust_geometry": True,
        "planar_only": True,
        "routable": True,
    },
)
def find_nearest_neighbors(
    source_features: Any,
    target_features: Any,
    k: int | None = None,
    max_distance: float | None = None,
    engine: str | None = None,
    precision: int | None = None,
    drop_unmatched: bool | None = None,
    include_target_geometry: bool | None = None,
    source_crs: str | None = None,
    target_crs: str | None = None,
    distance_field: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VectorOut:
    """
    Find k nearest target features for each source feature.

    Args:
        source_features:
            Source VectorOut, FeatureCollection, Feature, or list[Feature].
        target_features:
            Target VectorOut, FeatureCollection, Feature, or list[Feature].
        k:
            Number of nearest targets per source.
        max_distance:
            Optional maximum accepted distance.
        engine:
            auto | shapely | python.
        precision:
            Rounding precision for distance.
        drop_unmatched:
            If True, unmatched source features are removed.
        include_target_geometry:
            If True, target geometry is copied into output properties.
        source_crs:
            CRS hint for source_features.
        target_crs:
            CRS hint for target_features.

            Distance here is computed from raw geometry coordinates with no
            CRS awareness at all - if source_features and target_features
            are actually in different CRSs (e.g. only one of them was
            reprojected with crs_transform before this call), the result
            is a number, not an error: meaningless, but indistinguishable
            from a real distance without inspecting it by hand. Supplying
            both source_crs and target_crs lets this function catch that
            case and raise instead of returning it - see the raise below.
            Neither hint changes how distance is computed; only
            crs_transform actually reprojects data.
        distance_field:
            Optional output field name for the computed distance,
            overriding the configured default (config/plugins/
            nearest_neighbor.yaml, normally "_nearest_distance").

            This exists so several nearest-neighbour steps can be chained
            over the same features, each writing its own field - e.g.
            distance_to_metro_m, then distance_to_school_m. Without it
            every call writes the same field and the previous distance is
            lost, which makes multi-amenity accessibility analysis
            impossible to express as a DAG. The other bookkeeping fields
            (_neighbor_rank, _nearest_status, _target_properties, ...) are
            not namespaced and stay last-write-wins when chaining.
        metadata:
            Optional metadata to merge.

    Returns:
        VectorOut containing one output feature per matched neighbor.
    """
    config = _load_nearest_config()

    final_engine = _validate_engine(
        str(pick_first(engine, config.get("default_engine"), default="auto"))
    )

    max_k_config = config.get("max_k", 100)
    max_k = None if max_k_config is None else int(max_k_config)

    final_k = _validate_k(
        pick_first(k, config.get("default_k"), default=1),
        max_k=max_k,
    )

    final_max_distance = _validate_max_distance(max_distance)

    final_precision = _configured_precision(config) if precision is None else precision
    if final_precision is not None:
        if isinstance(final_precision, bool):
            raise ValueError("precision must be an integer or None.")
        final_precision = int(final_precision)
        if final_precision < 0 or final_precision > 15:
            raise ValueError("precision must be between 0 and 15.")

    final_drop_unmatched = bool(
        pick_first(drop_unmatched, config.get("drop_unmatched"), default=False)
    )

    final_include_target_geometry = bool(
        pick_first(include_target_geometry, config.get("include_target_geometry"), default=False)
    )

    final_source_crs = pick_first(source_crs, config.get("source_crs"), default=None)
    final_target_crs = pick_first(target_crs, config.get("target_crs"), default=None)
    warn_if_geographic_crs = bool(config.get("warn_if_geographic_crs", True))
    warn_if_crs_area_mismatch = bool(config.get("warn_if_crs_area_mismatch", True))
    warn_if_max_distance_excludes = bool(config.get("warn_if_max_distance_excludes", True))
    max_distance_warning_fraction = float(config.get("max_distance_warning_fraction", 0.2))

    _raise_if_crs_mismatch(
        source_crs=final_source_crs,
        target_crs=final_target_crs,
        capability_name="find_nearest_neighbors",
    )

    preserve_properties = bool(config.get("preserve_properties", True))
    fields = _configured_fields(config)

    if distance_field is not None:
        requested_distance_field = str(distance_field).strip()
        if not requested_distance_field:
            raise ValueError("distance_field must be a non-empty string when provided.")
        fields["distance_field"] = requested_distance_field

    source_items, source_info = _extract_features(source_features, label="source")
    target_items, target_info = _extract_features(target_features, label="target")

    if not target_items:
        raise ValueError("target_features must contain at least one feature.")

    output_features: list[dict[str, Any]] = []
    engines_used: set[str] = set()

    pair_count = 0
    matched_source_count = 0
    unmatched_source_count = 0
    match_count = 0
    failed_pair_count = 0
    dropped_unmatched_count = 0

    shape_fn, tree, tree_geoms, tree_indices = _prepare_target_index(target_items, final_engine)
    indexed_available = shape_fn is not None
    # Targets with no usable geometry aren't in the tree, but the
    # original per-pair loop still "evaluates" them (getting a None
    # distance, i.e. a failed pair) for every source - match that here
    # so pair_count/failed_pair_count stay consistent between the two
    # paths for the same inputs.
    missing_target_count = len(target_items) - len(tree_geoms) if indexed_available else 0
    # Computed once for the whole call (it only depends on tree_geoms,
    # not on any one source) - recomputing it per source would itself be
    # an O(source * target) bounds scan, defeating the point of indexing.
    initial_radius = _initial_radius_guess(tree_geoms) if tree_geoms else 1.0

    for source_index, source_feature in enumerate(source_items):
        candidate_rows: list[tuple[float, int, dict[str, Any], str]] = []
        last_engine_used = final_engine
        last_error: str | None = None
        used_index_for_source = False

        if indexed_available:
            source_geometry = source_feature.get("geometry")
            source_geom = None
            geom_build_failed = False

            if source_geometry is not None:
                try:
                    source_geom = shape_fn(source_geometry)
                except Exception:
                    geom_build_failed = True

            if not geom_build_failed:
                used_index_for_source = True
                # _shapely_distance_geometry (what the brute-force path
                # calls for every pair) resolves engine_used to "shapely"
                # before checking whether either geometry is missing or
                # empty - so that's the engine_used here too, even for a
                # source/target pair that ends up with no distance.
                last_engine_used = "shapely"

                if source_geometry is None or source_geom is None or source_geom.is_empty:
                    pair_count += len(target_items)
                    failed_pair_count += len(target_items)
                else:
                    pair_count += missing_target_count
                    failed_pair_count += missing_target_count
                    pair_count += len(tree_geoms)

                    fast_candidates = _strtree_candidates(
                        source_geom,
                        tree,
                        tree_geoms,
                        tree_indices,
                        final_k,
                        final_max_distance,
                        initial_radius,
                    )
                    for distance, target_index in fast_candidates:
                        candidate_rows.append(
                            (distance, target_index, target_items[target_index], "shapely")
                        )

                engines_used.add("shapely")

        if not used_index_for_source:
            (
                candidate_rows,
                pair_delta,
                failed_delta,
                last_engine_used,
                last_error,
            ) = _brute_force_candidates(
                source_feature, target_items, final_engine, final_max_distance, engines_used
            )
            pair_count += pair_delta
            failed_pair_count += failed_delta

        candidate_rows.sort(key=lambda item: (item[0], item[1]))
        selected = candidate_rows[:final_k]

        if not selected:
            unmatched_source_count += 1

            if final_drop_unmatched:
                dropped_unmatched_count += 1
                continue

            output_features.append(
                _make_neighbor_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=None,
                    target_index=None,
                    distance=None,
                    rank=None,
                    engine_used=last_engine_used,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    include_target_geometry=final_include_target_geometry,
                    status="unmatched",
                    reason=last_error or "no target feature matched nearest-neighbor constraints",
                )
            )
            continue

        matched_source_count += 1

        for rank, (distance, target_index, target_feature, engine_used) in enumerate(selected, start=1):
            match_count += 1

            output_features.append(
                _make_neighbor_feature(
                    source_feature=source_feature,
                    source_index=source_index,
                    target_feature=target_feature,
                    target_index=target_index,
                    distance=distance,
                    rank=rank,
                    engine_used=engine_used,
                    fields=fields,
                    precision=final_precision,
                    preserve_properties=preserve_properties,
                    include_target_geometry=final_include_target_geometry,
                    status="matched",
                )
            )

    stats = _build_vector_metadata(output_features)

    warnings: list[str] = []

    if warn_if_geographic_crs and _is_geographic_crs(final_source_crs):
        warnings.append(
            "Nearest-neighbor distance is being calculated on a geographic CRS. "
            "Reproject to a projected CRS for reliable physical distance values."
        )

    if warn_if_crs_area_mismatch:
        area_warning = _crs_area_of_use_warning(
            final_source_crs, final_target_crs, source_items, target_items
        )
        if area_warning:
            warnings.append(area_warning)

    if warn_if_max_distance_excludes:
        max_distance_warning = _max_distance_exclusion_warning(
            final_max_distance,
            unmatched_source_count,
            len(source_items),
            max_distance_warning_fraction,
        )
        if max_distance_warning:
            warnings.append(max_distance_warning)

    combined_warning = " | ".join(warnings) if warnings else None

    user_metadata = metadata or {}
    if not isinstance(user_metadata, dict):
        raise ValueError("metadata must be a dict or None.")

    output_metadata = {
        "source": "nearest_neighbor",
        "loader": PLUGIN_ID,
        "operation": "nearest_neighbor",
        "engine_requested": final_engine,
        "engines_used": sorted(engines_used),
        "k": final_k,
        "max_distance": final_max_distance,
        "coordinate_precision": final_precision,
        "drop_unmatched": final_drop_unmatched,
        "include_target_geometry": final_include_target_geometry,
        "source_crs": final_source_crs,
        "target_crs": final_target_crs,
        "planar_only": True,
        "spatial_index_used": indexed_available,
        "warning": combined_warning,
        "source_feature_count": len(source_items),
        "target_feature_count": len(target_items),
        "pair_count": pair_count,
        "match_count": match_count,
        "matched_source_count": matched_source_count,
        "unmatched_source_count": unmatched_source_count,
        "failed_pair_count": failed_pair_count,
        "dropped_unmatched_count": dropped_unmatched_count,
        "output_feature_count": len(output_features),
        "created_at": _utc_now_iso(),
        **source_info,
        **target_info,
        **stats,
        **user_metadata,
    }

    return VectorOut(
        features=output_features,
        metadata=output_metadata,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Nearest Neighbor",
    description=(
        "Finds k nearest target features for each source feature using planar distance. "
        "Uses shapely when available and falls back to the pure-python distance engine."
    ),
    author="GeoChat Platform Team",
    permissions=[],
)
