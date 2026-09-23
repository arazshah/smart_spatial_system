"""
orchestrator.planning.input_data_extent

Derive the combined WGS84 extent of a query's own input layers, and from
it a projected CRS suitable for metric/distance work on that data.

Why this exists: LLMQuerySpecGenerator.generate() only ever saw the raw
query text, never the data, so the model had to guess a projected CRS for
a location it could only infer from the user's wording. 0.5.3's prompt
used a literal CRS code as its worked example and the model copied it for
a city it didn't belong to; 0.5.4 replaced it with a placeholder, and the
model then copied the placeholder (or chose Web Mercator, which covers
everywhere but measures distance ~30% too long at mid latitudes). Neither
prompt could work, because the information needed to choose correctly
wasn't in it. This module puts it there: s3geo.query() computes it from
the layers it is about to execute on and hands the result to generate(),
which renders it into the system prompt as a stated fact. The value is
computed per query from that query's data, so there is no fixed example
for a model to copy.

The suggestion is the standard UTM rule applied to the extent's centroid
(zone = floor((lon + 180) / 6) + 1; EPSG 32600 + zone north of the
equator, 32700 + zone south), or the matching UPS pole CRS outside UTM's
80S..84N latitude band. It is then checked against the whole extent with
pyproj's own scale factors: if the data is too wide for that one CRS to
measure distance within MAX_SCALE_ERROR everywhere, no CRS is suggested
and the facts say why instead of stating a wrong one confidently. UTM's
Norway/Svalbard zone exceptions are not applied - the standard zone is
still a valid, accurate CRS there, just not the officially designated one.

Deliberately conservative, same posture as the 0.5.4 CRS checks: returns
None (no facts at all, prompt unchanged) whenever pyproj is missing - the
crs_transform plugin can't reproject to a UTM zone without it anyway -
whenever any vector layer's CRS can't be determined (a GeoDataFrame with
no .crs, a GeoJSON "crs" member pyproj can't resolve, or coordinates
outside lon/lat range for data that claims or defaults to EPSG:4326), and
whenever there is no finite coordinate to measure at all. A missing
suggestion, never a wrong one.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

# Largest distance error (as |scale factor - 1|, checked across the
# whole extent) a suggested CRS may have. UTM itself stays under ~0.1%
# inside its own 6-degree zone; 1% allows data spilling a few degrees
# over a zone edge (so a city straddling a zone boundary still gets a
# suggestion) while refusing to suggest a single UTM zone for a
# country/continent-wide extent.
MAX_SCALE_ERROR = 0.01

_WGS84 = "EPSG:4326"


@dataclass(frozen=True)
class InputDataExtent:
    """
    Facts about a query's input layers, computed from the data itself.

    bbox:
        Combined extent in EPSG:4326, (min_lon, min_lat, max_lon, max_lat).
    centroid:
        Center of bbox, (lon, lat).
    layer_crs:
        (layer name, CRS the layer is stored in) for every vector layer
        the extent was computed from.
    suggested_crs:
        Projected CRS for metric/distance work on this data (e.g. the
        centroid's UTM zone as "EPSG:<code>"), or None when no single CRS
        is accurate enough across the whole extent - see notes.
    suggested_crs_name:
        pyproj's human-readable name for suggested_crs.
    max_scale_error:
        Largest |scale factor - 1| of the candidate CRS across the extent
        (0.004 = distances off by up to 0.4%), or None if not computed.
    notes:
        Caveats worth telling the planner and the caller (extent spans
        several UTM zones, too wide for one CRS, ...).
    """

    bbox: tuple[float, float, float, float]
    centroid: tuple[float, float]
    layer_crs: tuple[tuple[str, str], ...]
    suggested_crs: str | None
    suggested_crs_name: str | None = None
    max_scale_error: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def utm_zone_for_lon(lon: float) -> int:
    """Standard UTM zone number (1..60) for a longitude in degrees."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    return min(max(zone, 1), 60)


def utm_epsg_for_lonlat(lon: float, lat: float) -> str:
    """WGS84 / UTM EPSG code for a point, by the standard UTM rule."""
    zone = utm_zone_for_lon(lon)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _candidate_crs(lon: float, lat: float) -> str:
    # UTM is only defined from 80S to 84N; beyond that the standard
    # companion is Universal Polar Stereographic.
    if lat > 84.0:
        return "EPSG:32661"
    if lat < -80.0:
        return "EPSG:32761"
    return utm_epsg_for_lonlat(lon, lat)


def _iter_positions(coords: Any) -> Iterator[tuple[float, float]]:
    if isinstance(coords, (list, tuple)):
        if (
            len(coords) >= 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords[:2])
        ):
            x, y = float(coords[0]), float(coords[1])
            if math.isfinite(x) and math.isfinite(y):
                yield x, y
            return
        for item in coords:
            yield from _iter_positions(item)


def _iter_geometry_positions(geometry: Any) -> Iterator[tuple[float, float]]:
    if not isinstance(geometry, Mapping):
        return
    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            yield from _iter_geometry_positions(child)
        return
    yield from _iter_positions(geometry.get("coordinates"))


def _geojson_bounds(layer: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    layer_type = layer.get("type")
    if layer_type == "FeatureCollection":
        geometries = [
            feature.get("geometry")
            for feature in layer.get("features") or []
            if isinstance(feature, Mapping)
        ]
    elif layer_type == "Feature":
        geometries = [layer.get("geometry")]
    else:
        geometries = [layer]

    xs: list[float] = []
    ys: list[float] = []
    for geometry in geometries:
        for x, y in _iter_geometry_positions(geometry):
            xs.append(x)
            ys.append(y)

    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _is_geojson_layer(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("type") in {
        "FeatureCollection",
        "Feature",
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    }


def _geojson_declared_crs(layer: Mapping[str, Any]) -> Any:
    """
    The legacy (GeoJSON 2008) "crs" member if present, else EPSG:4326 -
    RFC 7946 GeoJSON has no crs member and is always WGS84 lon/lat.
    None means a crs member is present but unreadable.
    """
    crs_member = layer.get("crs")
    if crs_member is None:
        return _WGS84
    if isinstance(crs_member, Mapping):
        properties = crs_member.get("properties")
        if isinstance(properties, Mapping) and properties.get("name"):
            return properties["name"]
    return None


def _crs_label(crs: Any) -> str:
    try:
        authority = crs.to_authority()
    except Exception:
        authority = None
    if authority:
        return f"{authority[0]}:{authority[1]}"
    try:
        return str(crs.to_string())
    except Exception:
        return str(crs)


def _layer_wgs84_bounds(
    layer: Any,
) -> tuple[bool, tuple[float, float, float, float] | None, str | None]:
    """
    (usable, bounds, crs_label) for one layer.

    usable=False means the layer is a vector layer whose CRS or extent
    can't be trusted, which disqualifies the whole suggestion. A non-
    vector value (usable=True, bounds=None) is simply skipped.
    """
    from pyproj import CRS, Transformer

    if hasattr(layer, "total_bounds") and hasattr(layer, "crs"):
        if len(layer) == 0:
            return True, None, None
        if layer.crs is None:
            return False, None, None
        try:
            crs = CRS.from_user_input(layer.crs)
            raw = tuple(float(v) for v in layer.total_bounds)
        except Exception:
            return False, None, None
        if not all(math.isfinite(v) for v in raw):
            return True, None, None
    elif _is_geojson_layer(layer):
        declared = _geojson_declared_crs(layer)
        if declared is None:
            return False, None, None
        try:
            crs = CRS.from_user_input(declared)
        except Exception:
            return False, None, None
        raw = _geojson_bounds(layer)
        if raw is None:
            return True, None, None
    else:
        return True, None, None

    minx, miny, maxx, maxy = raw
    label = _crs_label(crs)

    if crs.is_geographic and crs.equals(CRS.from_user_input(_WGS84), ignore_axis_order=True):
        bounds = raw
    else:
        try:
            transformer = Transformer.from_crs(crs, _WGS84, always_xy=True)
            bounds = tuple(
                float(v) for v in transformer.transform_bounds(minx, miny, maxx, maxy, densify_pts=21)
            )
        except Exception:
            return False, None, None

    if not all(math.isfinite(v) for v in bounds):
        return False, None, None

    # Data that is (or defaults to being) EPSG:4326 but has projected-
    # looking coordinates - a plain GeoJSON dict of metre coordinates
    # with no crs member, typically - has an unknown real CRS.
    if not (-180.0 <= bounds[0] <= bounds[2] <= 180.0 and -90.0 <= bounds[1] <= bounds[3] <= 90.0):
        return False, None, None

    return True, bounds, label


def _max_scale_error(crs_value: str, bbox: tuple[float, float, float, float]) -> float | None:
    """
    Largest |scale factor - 1| of crs_value over a 5x5 grid spanning bbox,
    using the worst direction at each point (Tissot semi-major/minor) so
    it is also meaningful for non-conformal projections.
    """
    from pyproj import Proj

    try:
        proj = Proj(crs_value)
    except Exception:
        return None

    min_lon, min_lat, max_lon, max_lat = bbox
    worst = 0.0
    steps = 4
    for i in range(steps + 1):
        lon = min_lon + (max_lon - min_lon) * i / steps
        for j in range(steps + 1):
            lat = min_lat + (max_lat - min_lat) * j / steps
            try:
                factors = proj.get_factors(lon, lat)
                values = (factors.tissot_semimajor, factors.tissot_semiminor)
            except Exception:
                return math.inf
            for value in values:
                if not math.isfinite(value):
                    return math.inf
                worst = max(worst, abs(value - 1.0))
    return worst


def derive_input_data_extent(layers: Mapping[str, Any] | None) -> InputDataExtent | None:
    """
    Combined WGS84 extent of every vector layer in ``layers`` (GeoJSON
    dicts and/or GeoDataFrames), plus a suggested projected CRS for it.
    See the module docstring for exactly when this returns None.
    """
    if not layers:
        return None

    try:
        from pyproj import CRS
    except ImportError:
        return None

    all_bounds: list[tuple[float, float, float, float]] = []
    layer_crs: list[tuple[str, str]] = []

    for name, layer in layers.items():
        try:
            usable, bounds, label = _layer_wgs84_bounds(layer)
        except Exception:
            return None
        if not usable:
            return None
        if bounds is None:
            continue
        all_bounds.append(bounds)
        layer_crs.append((str(name), label or "unknown"))

    if not all_bounds:
        return None

    bbox = (
        min(b[0] for b in all_bounds),
        min(b[1] for b in all_bounds),
        max(b[2] for b in all_bounds),
        max(b[3] for b in all_bounds),
    )
    centroid = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

    if bbox[2] - bbox[0] > 180.0:
        return InputDataExtent(
            bbox=bbox,
            centroid=centroid,
            layer_crs=tuple(layer_crs),
            suggested_crs=None,
            notes=(
                "The input data spans more than 180 degrees of longitude "
                "(possibly crossing the antimeridian), so no single projected "
                "CRS was suggested - its bbox centroid is not a meaningful "
                "location for this data.",
            ),
        )

    candidate = _candidate_crs(*centroid)
    error = _max_scale_error(candidate, bbox)
    try:
        candidate_name = CRS.from_user_input(candidate).name
    except Exception:
        candidate_name = None

    notes: list[str] = []
    zones = sorted({utm_zone_for_lon(bbox[0]), utm_zone_for_lon(bbox[2])})
    if candidate.startswith(("EPSG:326", "EPSG:327")) and zones[0] != zones[-1]:
        notes.append(
            f"The input extent spans UTM zones {zones[0]}-{zones[-1]}; "
            f"{candidate} is the zone of the data's centroid."
        )

    if error is None or error > MAX_SCALE_ERROR:
        detail = (
            f"{candidate} would be off by up to {error:.1%} at the edges of the data"
            if error is not None and math.isfinite(error)
            else f"{candidate} could not be evaluated across the whole extent"
        )
        notes.append(
            "The input data is too wide for any single UTM/UPS zone to measure "
            f"distance within {MAX_SCALE_ERROR:.0%} everywhere ({detail}), so no "
            "projected CRS was suggested. Planar distances over an extent this "
            "large are approximate in any single projected CRS."
        )
        return InputDataExtent(
            bbox=bbox,
            centroid=centroid,
            layer_crs=tuple(layer_crs),
            suggested_crs=None,
            max_scale_error=error,
            notes=tuple(notes),
        )

    if zones[0] != zones[-1]:
        notes.append(
            f"Distance error in {candidate} stays within {error:.2%} across the "
            "whole extent, so a single CRS is still appropriate."
        )

    return InputDataExtent(
        bbox=bbox,
        centroid=centroid,
        layer_crs=tuple(layer_crs),
        suggested_crs=candidate,
        suggested_crs_name=candidate_name,
        max_scale_error=error,
        notes=tuple(notes),
    )


def render_input_data_facts(extent: InputDataExtent) -> str:
    """The system-prompt section stating these facts to the planner."""
    min_lon, min_lat, max_lon, max_lat = extent.bbox
    lines = [
        "Input data facts (computed from THIS query's own input layers - these "
        "are measured facts about the data, not an example):",
    ]
    if extent.layer_crs:
        lines.append(
            "- Input layers and the CRS each is stored in: "
            + ", ".join(f"{name} ({crs})" for name, crs in extent.layer_crs)
        )
    lines.append(
        f"- Combined extent in EPSG:4326 (lon/lat): min_lon={min_lon:.6f}, "
        f"min_lat={min_lat:.6f}, max_lon={max_lon:.6f}, max_lat={max_lat:.6f}; "
        f"centroid lon={extent.centroid[0]:.6f}, lat={extent.centroid[1]:.6f}."
    )
    if extent.suggested_crs:
        name = f" ({extent.suggested_crs_name})" if extent.suggested_crs_name else ""
        lines.append(
            f"- Suitable projected CRS for metric work on this data: "
            f"{extent.suggested_crs}{name}. Use exactly this value as target_crs "
            "for every crs_transform that prepares data for a distance, "
            "nearest-neighbor, buffer or area operation, and as source_crs/"
            "target_crs on the distance operation itself - unless the user "
            "explicitly asks for a different CRS."
        )
    else:
        lines.append("- No single projected CRS is suggested for this data (see below).")
    for note in extent.notes:
        lines.append(f"- {note}")
    return "\n".join(lines)
