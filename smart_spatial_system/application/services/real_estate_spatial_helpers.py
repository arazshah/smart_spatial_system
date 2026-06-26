"""
Real-estate spatial helper functions.

These helpers provide small geometry, distance, metric, and risk utilities used
by real-estate ranking/enrichment flows. They contain no orchestration logic.
"""

from __future__ import annotations

import math
from typing import Any


def to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def feature_point_lonlat(feature: dict[str, Any]) -> tuple[float, float] | None:
    geom = feature.get("geometry") or {}
    if not isinstance(geom, dict) or geom.get("type") != "Point":
        return None

    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None

    lon = to_float_or_none(coords[0])
    lat = to_float_or_none(coords[1])
    if lon is None or lat is None:
        return None

    return lon, lat


def point_in_ring_lonlat(point: tuple[float, float], ring: list[Any]) -> bool:
    if not isinstance(ring, list) or len(ring) < 3:
        return False

    x, y = point
    inside = False
    j = len(ring) - 1

    for i in range(len(ring)):
        pi = ring[i]
        pj = ring[j]
        if (
            isinstance(pi, list)
            and isinstance(pj, list)
            and len(pi) >= 2
            and len(pj) >= 2
        ):
            xi = to_float_or_none(pi[0])
            yi = to_float_or_none(pi[1])
            xj = to_float_or_none(pj[0])
            yj = to_float_or_none(pj[1])

            if xi is not None and yi is not None and xj is not None and yj is not None:
                intersects = ((yi > y) != (yj > y)) and (
                    x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
                )
                if intersects:
                    inside = not inside

        j = i

    return inside


def point_in_polygon_feature_lonlat(
    point: tuple[float, float],
    feature: dict[str, Any],
) -> bool:
    geom = feature.get("geometry") or {}
    if not isinstance(geom, dict):
        return False

    geom_type = geom.get("type")
    coords = geom.get("coordinates")

    def _inside_polygon(poly_coords: Any) -> bool:
        if not isinstance(poly_coords, list) or not poly_coords:
            return False

        outer = poly_coords[0]
        if not point_in_ring_lonlat(point, outer):
            return False

        # Holes: if inside any inner ring, point is outside polygon.
        for hole in poly_coords[1:]:
            if point_in_ring_lonlat(point, hole):
                return False

        return True

    if geom_type == "Polygon":
        return _inside_polygon(coords)

    if geom_type == "MultiPolygon" and isinstance(coords, list):
        return any(_inside_polygon(poly) for poly in coords)

    return False


def lonlat_to_local_xy_m(
    point: tuple[float, float],
    *,
    ref_lat: float,
) -> tuple[float, float]:
    lon, lat = point
    x = lon * 111_320.0 * math.cos(math.radians(ref_lat))
    y = lat * 110_540.0
    return x, y


def distance_point_to_segment_m(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    ref_lat = point[1]
    px, py = lonlat_to_local_xy_m(point, ref_lat=ref_lat)
    ax, ay = lonlat_to_local_xy_m(start, ref_lat=ref_lat)
    bx, by = lonlat_to_local_xy_m(end, ref_lat=ref_lat)

    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def distance_point_to_point_m(
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    lon1, lat1 = a
    lon2, lat2 = b

    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    h = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def distance_point_to_geometry_m(
    point: tuple[float, float],
    feature: dict[str, Any],
) -> float | None:
    geom = feature.get("geometry") or {}
    if not isinstance(geom, dict):
        return None

    geom_type = geom.get("type")
    coords = geom.get("coordinates")

    def _coord_to_point(value: Any) -> tuple[float, float] | None:
        if not isinstance(value, list) or len(value) < 2:
            return None
        lon = to_float_or_none(value[0])
        lat = to_float_or_none(value[1])
        if lon is None or lat is None:
            return None
        return lon, lat

    def _line_distance(line: Any) -> float | None:
        if not isinstance(line, list) or len(line) < 2:
            return None

        best: float | None = None
        prev = _coord_to_point(line[0])
        for raw in line[1:]:
            current = _coord_to_point(raw)
            if prev is not None and current is not None:
                dist = distance_point_to_segment_m(point, prev, current)
                best = dist if best is None else min(best, dist)
            prev = current

        return best

    if geom_type == "Point":
        other = _coord_to_point(coords)
        return distance_point_to_point_m(point, other) if other else None

    if geom_type == "MultiPoint" and isinstance(coords, list):
        distances = []
        for raw_point in coords:
            other = _coord_to_point(raw_point)
            if other:
                distances.append(distance_point_to_point_m(point, other))
        return min(distances) if distances else None

    if geom_type == "LineString":
        return _line_distance(coords)

    if geom_type == "MultiLineString" and isinstance(coords, list):
        distances = [d for line in coords if (d := _line_distance(line)) is not None]
        return min(distances) if distances else None

    if geom_type == "Polygon":
        if point_in_polygon_feature_lonlat(point, feature):
            return 0.0
        if isinstance(coords, list):
            distances = [d for ring in coords if (d := _line_distance(ring)) is not None]
            return min(distances) if distances else None

    if geom_type == "MultiPolygon" and isinstance(coords, list):
        if point_in_polygon_feature_lonlat(point, feature):
            return 0.0

        distances: list[float] = []
        for poly in coords:
            if isinstance(poly, list):
                for ring in poly:
                    dist = _line_distance(ring)
                    if dist is not None:
                        distances.append(dist)
        return min(distances) if distances else None

    return None


def nearest_distance_to_features_m(
    point: tuple[float, float],
    features: list[dict[str, Any]],
) -> float | None:
    distances: list[float] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        dist = distance_point_to_geometry_m(point, feature)
        if dist is not None:
            distances.append(dist)

    return min(distances) if distances else None


def has_metric_value(props: dict[str, Any], key: str) -> bool:
    value = props.get(key)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return to_float_or_none(value) is not None


def has_bool_like_value(props: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if props.get(key) is not None:
            return True
    return False


def normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()

    low_values = {"low", "l", "پایین", "کم", "خوب", "ایمن", "safe"}
    medium_values = {"medium", "med", "m", "متوسط", "میانه", "قابل قبول", "قابل‌قبول"}
    high_values = {"high", "h", "بالا", "زیاد", "پرخطر", "خطرناک", "unsafe"}

    if text in low_values:
        return "low"
    if text in medium_values:
        return "medium"
    if text in high_values:
        return "high"

    # اگر مقدار نامشخص بود، برای MVP آن را medium در نظر می‌گیریم تا حذف سخت‌گیرانه نشود.
    return "medium"
