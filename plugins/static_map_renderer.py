"""
static_map_renderer.py

GeoChat SDK Plugin
==================

Plugin ID:
    static_map_renderer

Capability:
    - render_static_map:
        Draw vector layers as a self-contained SVG map (no tiles, no network): polygons, lines and
        points with fill/stroke styling, optional colouring by an attribute (numeric ramp or
        categories), title and legend. Meant for reports, PDF/HTML documents and clients that
        cannot show an interactive map.

Projection:
    Coordinates inside the lon/lat range are drawn in Web Mercator (so shapes look familiar);
    anything else is drawn planar as given.

Safety:
    Colours are validated (#rgb / #rrggbb), every text is XML-escaped, and image size, feature
    and vertex counts are bounded, so the SVG can be embedded as-is.

Config-aware behavior:
    Reads config/plugins/static_map_renderer.yaml.
"""

from __future__ import annotations

import math
import re
from typing import Any
from xml.sax.saxutils import escape

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    load_config,
    to_float,
    to_int,
    utc_now_iso,
    validate_choice,
)
from plugins.csv_xy_loader import parse_number

PLUGIN_ID = "static_map_renderer"

DEFAULTS = {"max_features": 50_000, "max_vertices": 300_000, "max_size": 2000, "max_layers": 6}
RAMPS = {
    "viridis": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
    "blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "diverging": ["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
}
CATEGORICAL = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#999999"]
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _color(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        raise ValueError(f"{name} must be a colour like '#336699' or '#369'.")
    return value


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _ramp_color(ramp: list[str], t: float) -> str:
    t = min(1.0, max(0.0, t))
    position = t * (len(ramp) - 1)
    low = min(int(position), len(ramp) - 2)
    frac = position - low
    a, b = _hex_to_rgb(ramp[low]), _hex_to_rgb(ramp[low + 1])
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * frac) for i in range(3))


def _geometry_parts(geometry: dict[str, Any]):
    """Yield (kind, coordinates) with kind in polygon/line/point."""
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Point":
        yield "point", [coords]
    elif kind == "MultiPoint":
        yield "point", coords
    elif kind == "LineString":
        yield "line", [coords]
    elif kind == "MultiLineString":
        yield "line", coords
    elif kind == "Polygon":
        yield "polygon", [coords]
    elif kind == "MultiPolygon":
        yield "polygon", coords
    elif kind == "GeometryCollection":
        for sub in geometry.get("geometries") or []:
            yield from _geometry_parts(sub)


def _all_positions(geometry: dict[str, Any]):
    for kind, group in _geometry_parts(geometry):
        if kind == "point":
            yield from group
        elif kind == "line":
            for line in group:
                yield from line
        else:
            for polygon in group:
                for ring in polygon:
                    yield from ring


@capability(
    name="render_static_map",
    keywords=[
        "static map", "map image", "render map", "draw map", "map svg", "export map picture", "thematic map",
        "choropleth", "map for report",
        "نقشه ثابت", "تصویر نقشه", "رسم نقشه", "نقشه موضوعی", "نقشه برای گزارش", "خروجی تصویری نقشه",
    ],
    description=(
        "Render vector layers as a self-contained SVG map with optional colouring by an attribute "
        "(numeric ramp or categories), title and legend; no tiles or network needed."
    ),
    required_inputs=["features"],
    optional_inputs=["overlays", "color_field", "palette", "classes", "fill", "stroke", "stroke_width",
                     "point_radius", "opacity", "title", "width", "height", "legend", "background",
                     "metadata"],
    output_kind="json",
    permissions=[],
    metadata={
        "category": "output",
        "data_type": "vector",
        "operation": "render_static_map",
        "returns": "MapImage",
        "artifact_kind": "map_image",
        "access_scope": "attribute_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def render_static_map(
    features: Any,
    overlays: list[Any] | None = None,
    color_field: str | None = None,
    palette: str = "viridis",
    classes: int = 5,
    fill: str = "#6baed6",
    stroke: str = "#333333",
    stroke_width: float = 0.8,
    point_radius: float = 3.0,
    opacity: float = 0.85,
    title: str | None = None,
    width: int = 800,
    height: int = 600,
    legend: bool = True,
    background: str = "#ffffff",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Render a map.

    Args:
        features: The main layer (drawn on top).
        overlays: Extra background layers drawn below it in light grey (e.g. boundaries, roads).
        color_field: Attribute to colour the main layer by: numeric -> ``classes`` equal-interval
            steps of ``palette``; text -> one colour per category (max 8 shown, rest grey).
        palette: viridis | blues | reds | greens | diverging.
        classes: Number of numeric classes (2-9).
        fill / stroke: Default colours as '#rrggbb' (fill is used when there is no color_field).
        stroke_width / point_radius: In pixels.
        opacity: Fill opacity 0-1.
        title: Optional title (max 150 characters).
        width / height: Image size in pixels (max 2000).
        legend: Draw a legend when colouring by an attribute.
        background: Background colour.
        metadata: Extra output metadata.

    Returns:
        dict ``{"type": "map_image", "format": "svg", "svg": "<svg ...>", "width", "height",
        "bbox", "projection", "legend": [...], "layers": [...]}``.
    """
    config = load_config(PLUGIN_ID)
    shape_limit = _limit(config, "max_size")
    w, h = to_int(width, "width"), to_int(height, "height")
    if not (100 <= w <= shape_limit and 100 <= h <= shape_limit):
        raise ValueError(f"width and height must be between 100 and {shape_limit}.")
    fill_color, stroke_color = _color(fill, "fill"), _color(stroke, "stroke")
    bg = _color(background, "background")
    swidth = to_float(stroke_width, "stroke_width")
    radius = to_float(point_radius, "point_radius")
    alpha = to_float(opacity, "opacity")
    if not (0 <= swidth <= 20 and 0 < radius <= 50 and 0 <= alpha <= 1):
        raise ValueError("stroke_width must be 0-20, point_radius 0-50 and opacity 0-1.")
    ramp = RAMPS[validate_choice(palette, set(RAMPS), "palette")]
    n_classes = to_int(classes, "classes")
    if not 2 <= n_classes <= 9:
        raise ValueError("classes must be between 2 and 9.")
    if title is not None and (not isinstance(title, str) or len(title) > 150):
        raise ValueError("title must be a string of at most 150 characters.")

    main, source_info = extract_features(features)
    layers: list[tuple[str, list[dict[str, Any]]]] = [("main", main)]
    background_layers = []
    for index, extra in enumerate(overlays or []):
        background_layers.append((f"overlay_{index + 1}", extract_features(extra)[0]))
    check_limit(len(background_layers) + 1, _limit(config, "max_layers"), "layer count")
    layers = background_layers + layers
    check_limit(sum(len(f) for _, f in layers), _limit(config, "max_features"), "feature count")

    vertices = 0
    lons_lats = True
    xs, ys = [], []
    for _, feats in layers:
        for feature in feats:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            for position in _all_positions(geometry):
                vertices += 1
                x, y = float(position[0]), float(position[1])
                xs.append(x)
                ys.append(y)
                if not (-180 <= x <= 180 and -85 <= y <= 85):
                    lons_lats = False
    check_limit(vertices, _limit(config, "max_vertices"), "vertex count (simplify the layers first)")
    if not xs:
        raise ValueError("There is nothing to draw: no feature has a geometry.")

    def project(x: float, y: float) -> tuple[float, float]:
        if lons_lats:
            return x, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(y) / 2)))
        return x, y

    projected = [project(x, y) for x, y in zip(xs, ys, strict=True)]
    min_x, max_x = min(p[0] for p in projected), max(p[0] for p in projected)
    min_y, max_y = min(p[1] for p in projected), max(p[1] for p in projected)
    span_x, span_y = max(max_x - min_x, 1e-9), max(max_y - min_y, 1e-9)
    margin, top = 16.0, 16.0 + (22.0 if title else 0.0)
    scale = min((w - 2 * margin) / span_x, (h - top - margin) / span_y)
    off_x = margin + ((w - 2 * margin) - span_x * scale) / 2
    off_y = top + ((h - top - margin) - span_y * scale) / 2

    def to_px(x: float, y: float) -> tuple[float, float]:
        px, py = project(float(x), float(y))
        return off_x + (px - min_x) * scale, off_y + (max_y - py) * scale

    # colours of the main layer
    legend_items: list[dict[str, str]] = []
    colors: list[str] = [fill_color] * len(main)
    if color_field:
        raw = [(f.get("properties") or {}).get(color_field) for f in main]
        numbers = [parse_number(v) for v in raw]
        numeric_values = [n for n in numbers if n is not None]
        if numeric_values and len(numeric_values) >= 0.8 * sum(1 for v in raw if v not in (None, "")):
            lo, hi = min(numeric_values), max(numeric_values)
            step = (hi - lo) / n_classes or 1.0
            palette_colors = [_ramp_color(ramp, i / (n_classes - 1)) for i in range(n_classes)]
            for i in range(n_classes):
                legend_items.append({"label": f"{lo + i * step:.4g} – {lo + (i + 1) * step:.4g}",
                                     "color": palette_colors[i]})
            colors = [
                "#cccccc" if n is None else palette_colors[min(n_classes - 1, int((n - lo) / step))]
                for n in numbers
            ]
        else:
            counts: dict[str, int] = {}
            for v in raw:
                if v not in (None, ""):
                    counts[str(v)] = counts.get(str(v), 0) + 1
            shown = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:len(CATEGORICAL)]]
            lookup = {k: CATEGORICAL[i] for i, k in enumerate(shown)}
            legend_items = [{"label": k, "color": lookup[k]} for k in shown]
            colors = [lookup.get(str(v), "#cccccc") if v not in (None, "") else "#cccccc" for v in raw]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{escape(title or "Map", {chr(34): "&quot;"})}" '
        f'font-family="sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="{bg}"/>',
    ]
    if title:
        parts.append(f'<text x="{w / 2}" y="20" text-anchor="middle" font-size="15" '
                     f'font-weight="bold">{escape(title)}</text>')

    def draw(feats: list[dict[str, Any]], layer_colors: list[str], layer_stroke: str, layer_width: float,
             layer_alpha: float, layer_radius: float) -> None:
        for feature, color in zip(feats, layer_colors, strict=True):
            geometry = feature.get("geometry")
            if not geometry:
                continue
            for kind, group in _geometry_parts(geometry):
                if kind == "point":
                    for x, y in (g[:2] for g in group):
                        px, py = to_px(x, y)
                        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{layer_radius}" fill="{color}" '
                                     f'fill-opacity="{layer_alpha}" stroke="{layer_stroke}" '
                                     f'stroke-width="{layer_width}"/>')
                elif kind == "line":
                    for line in group:
                        pts = " ".join("%.1f,%.1f" % to_px(*p[:2]) for p in line)
                        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                                     f'stroke-width="{max(layer_width, 1.2)}" stroke-linejoin="round"/>')
                else:
                    for polygon in group:
                        d = " ".join(
                            "M" + " L".join("%.1f,%.1f" % to_px(*p[:2]) for p in ring) + " Z"
                            for ring in polygon)
                        parts.append(f'<path d="{d}" fill="{color}" fill-opacity="{layer_alpha}" '
                                     f'fill-rule="evenodd" stroke="{layer_stroke}" stroke-width="{layer_width}" '
                                     'stroke-linejoin="round"/>')

    for _, feats in background_layers:
        draw(feats, ["#e3e3e3"] * len(feats), "#b0b0b0", 0.6, 0.9, 2.0)
    draw(main, colors, stroke_color, swidth, alpha, radius)

    if legend and legend_items:
        box_h = 16 + 16 * len(legend_items)
        y0 = h - margin - box_h
        parts.append(f'<rect x="{margin}" y="{y0}" width="170" height="{box_h}" fill="#ffffff" '
                     'fill-opacity="0.85" stroke="#cccccc"/>')
        parts.append(f'<text x="{margin + 6}" y="{y0 + 13}" font-weight="bold">{escape(str(color_field))[:24]}</text>')
        for i, item in enumerate(legend_items):
            y = y0 + 20 + 16 * i
            parts.append(f'<rect x="{margin + 6}" y="{y}" width="12" height="12" fill="{item["color"]}"/>')
            parts.append(f'<text x="{margin + 24}" y="{y + 10}">{escape(item["label"])[:22]}</text>')
    parts.append("</svg>")

    return {
        "type": "map_image",
        "format": "svg",
        "svg": "".join(parts),
        "width": w,
        "height": h,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "projection": "web_mercator" if lons_lats else "planar",
        "legend": legend_items,
        "layers": [{"name": n, "feature_count": len(f)} for n, f in layers],
        "metadata": {"source": PLUGIN_ID, "operation": "render_static_map", "created_at": utc_now_iso(),
                     "vertex_count": vertices, "color_field": color_field, **source_info,
                     **(metadata or {})},
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Static Map Renderer",
    description="Renders vector layers to a self-contained SVG map with attribute colouring and legend.",
    author="GeoChat Platform Team",
    permissions=[],
)
