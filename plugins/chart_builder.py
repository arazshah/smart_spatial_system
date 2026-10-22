"""
chart_builder.py

GeoChat SDK Plugin
==================

Plugin ID:
    chart_builder

Capability:
    - build_chart:
        Turn feature attributes into a chart artifact: a Vega-Lite v5 spec (for
        front ends that render it) plus a self-contained SVG (for reports and
        clients that cannot). Types: bar, line, scatter, pie, histogram.

Output:
    JSON-serialisable dict, ``type: "chart"`` (ADR-002 canonical artifact type):
    ``chart_type``, ``title``, ``spec``, ``svg``, ``data`` (the plotted rows),
    ``metadata``.

Safety:
    Every text that reaches the SVG is XML-escaped, sizes are fixed, and row,
    category and bin counts are bounded, so the SVG can be embedded as-is.
"""

from __future__ import annotations

import math
from typing import Any
from xml.sax.saxutils import escape

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins._shared.vector_common import (
    check_limit,
    extract_features,
    load_config,
        to_int,
    utc_now_iso,
    validate_choice,
)
from plugins.vector_aggregation import TABLE_OPS, _numeric, _reduce

PLUGIN_ID = "chart_builder"

CHART_TYPES = {"bar", "line", "scatter", "pie", "histogram"}
AGGREGATES = set(TABLE_OPS)
DEFAULTS = {"max_rows": 20_000, "max_categories": 50, "max_bins": 100}
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#999999"]
WIDTH, HEIGHT = 640, 400
MARGIN = {"left": 70, "right": 20, "top": 44, "bottom": 80}


def _limit(config: dict[str, Any], key: str) -> int:
    return to_int(config.get(key, DEFAULTS[key]), key)


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi == lo:
        hi = lo + 1.0
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    value = start
    while value <= hi + step * 0.5 and len(ticks) < 20:
        ticks.append(round(value, 12))
        value += step
    return ticks


def _fmt(value: float) -> str:
    if abs(value) >= 1e6 or (0 < abs(value) < 1e-3):
        return f"{value:.3g}"
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _label(value: Any, width: int = 18) -> str:
    text = str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


class _Svg:
    def __init__(self, title: str) -> None:
        self.parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(title, {chr(34): "&quot;"})}" '
            'font-family="sans-serif" font-size="11">',
            f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
            f'<text x="{WIDTH / 2}" y="24" text-anchor="middle" font-size="15" '
            f'font-weight="bold">{escape(title)}</text>',
        ]
        self.left = MARGIN["left"]
        self.right = WIDTH - MARGIN["right"]
        self.top = MARGIN["top"]
        self.bottom = HEIGHT - MARGIN["bottom"]

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def y_axis(self, lo: float, hi: float, label: str) -> tuple[float, float, Any]:
        ticks = _nice_ticks(lo, hi)
        lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

        def scale(v: float) -> float:
            return self.bottom - (v - lo) / (hi - lo or 1.0) * (self.bottom - self.top)

        for tick in ticks:
            y = scale(tick)
            self.add(f'<line x1="{self.left}" x2="{self.right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e3e3e3"/>')
            self.add(f'<text x="{self.left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(tick)}</text>')
        self.add(f'<line x1="{self.left}" x2="{self.left}" y1="{self.top}" y2="{self.bottom}" stroke="#444"/>')
        self.add(f'<line x1="{self.left}" x2="{self.right}" y1="{self.bottom}" y2="{self.bottom}" stroke="#444"/>')
        self.add(f'<text x="16" y="{(self.top + self.bottom) / 2}" text-anchor="middle" '
                 f'transform="rotate(-90 16 {(self.top + self.bottom) / 2})">{escape(label)}</text>')
        return lo, hi, scale

    def x_axis_title(self, label: str) -> None:
        self.add(f'<text x="{(self.left + self.right) / 2}" y="{HEIGHT - 8}" '
                 f'text-anchor="middle">{escape(label)}</text>')

    def finish(self) -> str:
        return "".join(self.parts) + "</svg>"


def _category_axis(svg: _Svg, categories: list[Any]) -> list[float]:
    step = (svg.right - svg.left) / max(len(categories), 1)
    centers = []
    for i, cat in enumerate(categories):
        cx = svg.left + step * (i + 0.5)
        centers.append(cx)
        svg.add(f'<text x="{cx:.1f}" y="{svg.bottom + 14}" text-anchor="end" '
                f'transform="rotate(-40 {cx:.1f} {svg.bottom + 14})">{escape(_label(cat))}</text>')
    return centers


def _render_bar(title, rows, x_label, y_label) -> str:
    svg = _Svg(title)
    values = [r["value"] for r in rows]
    lo, hi = min(0.0, min(values)), max(0.0, max(values))
    lo, hi, scale = svg.y_axis(lo, hi, y_label)
    centers = _category_axis(svg, [r["category"] for r in rows])
    width = (svg.right - svg.left) / max(len(rows), 1) * 0.7
    zero = scale(0.0)
    for cx, row in zip(centers, rows, strict=True):
        top = scale(row["value"])
        svg.add(f'<rect x="{cx - width / 2:.1f}" y="{min(top, zero):.1f}" width="{width:.1f}" '
                f'height="{abs(zero - top):.1f}" fill="{PALETTE[0]}"/>')
    svg.x_axis_title(x_label)
    return svg.finish()


def _render_line(title, rows, x_label, y_label) -> str:
    svg = _Svg(title)
    values = [r["value"] for r in rows]
    lo, hi, scale = svg.y_axis(min(values), max(values), y_label)
    centers = _category_axis(svg, [r["category"] for r in rows])
    points = " ".join(f"{cx:.1f},{scale(r['value']):.1f}" for cx, r in zip(centers, rows, strict=True))
    svg.add(f'<polyline points="{points}" fill="none" stroke="{PALETTE[0]}" stroke-width="2"/>')
    for cx, r in zip(centers, rows, strict=True):
        svg.add(f'<circle cx="{cx:.1f}" cy="{scale(r["value"]):.1f}" r="3" fill="{PALETTE[0]}"/>')
    svg.x_axis_title(x_label)
    return svg.finish()


def _render_scatter(title, rows, x_label, y_label) -> str:
    svg = _Svg(title)
    xs, ys = [r["x"] for r in rows], [r["y"] for r in rows]
    ylo, yhi, yscale = svg.y_axis(min(ys), max(ys), y_label)
    xticks = _nice_ticks(min(xs), max(xs))
    xlo, xhi = min(min(xs), xticks[0]), max(max(xs), xticks[-1])

    def xscale(v: float) -> float:
        return svg.left + (v - xlo) / (xhi - xlo or 1.0) * (svg.right - svg.left)

    for tick in xticks:
        svg.add(f'<text x="{xscale(tick):.1f}" y="{svg.bottom + 14}" text-anchor="middle">{_fmt(tick)}</text>')
    for r in rows:
        svg.add(f'<circle cx="{xscale(r["x"]):.1f}" cy="{yscale(r["y"]):.1f}" r="3" '
                f'fill="{PALETTE[0]}" fill-opacity="0.7"/>')
    svg.x_axis_title(x_label)
    return svg.finish()


def _render_histogram(title, rows, x_label, y_label) -> str:
    svg = _Svg(title)
    xlo, xhi = rows[0]["bin_start"], rows[-1]["bin_end"]
    _, _, yscale = svg.y_axis(0.0, max(r["count"] for r in rows), y_label)

    def xscale(v: float) -> float:
        return svg.left + (v - xlo) / (xhi - xlo or 1.0) * (svg.right - svg.left)

    for r in rows:
        x0, x1 = xscale(r["bin_start"]), xscale(r["bin_end"])
        svg.add(f'<rect x="{x0:.1f}" y="{yscale(r["count"]):.1f}" width="{max(x1 - x0 - 1, 0.5):.1f}" '
                f'height="{svg.bottom - yscale(r["count"]):.1f}" fill="{PALETTE[0]}"/>')
    for tick in _nice_ticks(xlo, xhi):
        if xlo <= tick <= xhi:
            svg.add(f'<text x="{xscale(tick):.1f}" y="{svg.bottom + 14}" text-anchor="middle">{_fmt(tick)}</text>')
    svg.x_axis_title(x_label)
    return svg.finish()


def _render_pie(title, rows, x_label, y_label) -> str:
    svg = _Svg(title)
    total = sum(r["value"] for r in rows)
    cx, cy, radius = 220, 220, 130
    angle = -math.pi / 2
    for i, r in enumerate(rows):
        sweep = 2 * math.pi * r["value"] / total
        color = PALETTE[i % len(PALETTE)]
        if len(rows) == 1:
            svg.add(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}"/>')
        else:
            x0, y0 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x1, y1 = cx + radius * math.cos(angle + sweep), cy + radius * math.sin(angle + sweep)
            large = 1 if sweep > math.pi else 0
            svg.add(f'<path d="M{cx},{cy} L{x0:.1f},{y0:.1f} A{radius},{radius} 0 {large} 1 '
                    f'{x1:.1f},{y1:.1f} Z" fill="{color}" stroke="#fff"/>')
        angle += sweep
        ly = 70 + i * 18
        svg.add(f'<rect x="400" y="{ly - 10}" width="12" height="12" fill="{color}"/>')
        svg.add(f'<text x="418" y="{ly}">{escape(_label(r["category"], 24))} '
                f'({r["value"] / total:.0%})</text>')
    return svg.finish()


def _vega(chart_type: str, rows: list[dict[str, Any]], x_label: str, y_label: str, title: str):
    base = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "title": title,
            "width": 480, "height": 300, "data": {"values": rows}}
    if chart_type == "bar" or chart_type == "line":
        return {**base, "mark": "bar" if chart_type == "bar" else {"type": "line", "point": True},
                "encoding": {"x": {"field": "category", "type": "nominal", "sort": None, "title": x_label},
                             "y": {"field": "value", "type": "quantitative", "title": y_label}}}
    if chart_type == "scatter":
        return {**base, "mark": "point",
                "encoding": {"x": {"field": "x", "type": "quantitative", "title": x_label},
                             "y": {"field": "y", "type": "quantitative", "title": y_label}}}
    if chart_type == "histogram":
        return {**base, "mark": "bar",
                "encoding": {"x": {"field": "bin_start", "type": "quantitative", "bin": {"binned": True},
                                   "title": x_label},
                             "x2": {"field": "bin_end"},
                             "y": {"field": "count", "type": "quantitative", "title": y_label}}}
    return {**base, "mark": "arc",
            "encoding": {"theta": {"field": "value", "type": "quantitative"},
                         "color": {"field": "category", "type": "nominal", "title": x_label}}}


@capability(
    name="build_chart",
    keywords=[
        "chart", "graph", "plot", "bar chart", "line chart", "pie chart", "histogram", "scatter plot",
        "visualize attribute", "distribution",
        "نمودار", "نمودار میله ای", "نمودار خطی", "نمودار دایره ای", "هیستوگرام", "نمودار پراکنش", "توزیع",
    ],
    description=(
        "Build a chart (bar, line, scatter, pie, histogram) from feature attributes, returned as "
        "a Vega-Lite spec plus a ready-to-embed SVG."
    ),
    required_inputs=["features", "chart_type", "x"],
    optional_inputs=["y", "aggregate", "title", "sort", "top_n", "bins", "metadata"],
    output_kind="json",
    permissions=[],
    metadata={
        "category": "output",
        "data_type": "vector",
        "operation": "build_chart",
        "returns": "ChartOut",
        "artifact_kind": "chart",
        "access_scope": "attribute_analysis",
        "config_aware": True,
        "routable": True,
    },
)
def build_chart(
    features: Any,
    chart_type: str,
    x: str,
    y: str | None = None,
    aggregate: str = "sum",
    title: str | None = None,
    sort: str | None = None,
    top_n: int | None = None,
    bins: int = 10,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a chart.

    Args:
        features: Input features (only attributes are used).
        chart_type: bar | line | scatter | pie | histogram.
        x: bar/line/pie: category field; scatter/histogram: numeric field.
        y: bar/line/pie: numeric field summarised per x (omit to count rows); scatter: numeric
            field (required); histogram: not used.
        aggregate: How rows sharing an x are combined for bar/line/pie (sum, mean, count,
            min, max, median...).
        title: Chart title (default derived from the fields).
        sort: ``asc`` / ``desc`` by value (bar and pie default: desc; line keeps data order).
        top_n: Keep the N largest categories (bar/pie); default max_categories.
        bins: Histogram bin count.
        metadata: Extra output metadata.

    Returns:
        dict with ``type: "chart"``, ``chart_type``, ``title``, ``spec`` (Vega-Lite), ``svg``,
        ``data`` and ``metadata``.
    """
    config = load_config(PLUGIN_ID)
    kind = validate_choice(chart_type, CHART_TYPES, "chart_type")
    if not isinstance(x, str) or not x.strip():
        raise ValueError("x must be a field name.")
    op = validate_choice(aggregate, AGGREGATES, "aggregate")
    input_features, source_info = extract_features(features)
    check_limit(len(input_features), _limit(config, "max_rows"), "feature count")
    rows_in = [f.get("properties") or {} for f in input_features]
    max_categories = _limit(config, "max_categories")
    limit = min(to_int(top_n, "top_n"), max_categories) if top_n is not None else max_categories
    if limit < 1:
        raise ValueError("top_n must be >= 1.")

    x_label, y_label = x, (y or "count")
    data: list[dict[str, Any]]
    if kind in {"bar", "line", "pie"}:
        groups: dict[str, tuple[Any, list[Any]]] = {}
        for props in rows_in:
            key = props.get(x)
            if key is None:
                continue
            groups.setdefault(repr(key), (key, []))[1].append(props.get(y) if y else 1)
        if not groups:
            raise ValueError(f"No rows have a value for '{x}'.")
        data = []
        effective_op = op if y else "count"
        for key, values in groups.values():
            value = _reduce(values, effective_op)
            if value is not None:
                data.append({"category": key if isinstance(key, (int, float, str)) else str(key),
                             "value": float(value)})
        if not data:
            raise ValueError(f"No numeric values found in '{y}'.")
        order = sort or ("desc" if kind in {"bar", "pie"} else None)
        if order:
            order = validate_choice(order, {"asc", "desc"}, "sort")
            data.sort(key=lambda r: r["value"], reverse=order == "desc")
        if len(data) > limit:
            if top_n is None:
                raise ValueError(
                    f"{len(data)} categories exceed the limit of {max_categories}; pass top_n."
                )
            data = data[:limit]
        if kind == "pie":
            if any(r["value"] < 0 for r in data) or sum(r["value"] for r in data) <= 0:
                raise ValueError("pie charts need non-negative values with a positive total.")
        y_label = f"{op}({y})" if y else "count"
    elif kind == "scatter":
        if not y:
            raise ValueError("scatter charts need y.")
        data = []
        for props in rows_in:
            px, py = _numeric(props.get(x)), _numeric(props.get(y))
            if px is not None and py is not None:
                data.append({"x": px, "y": py})
        if not data:
            raise ValueError("No rows have numeric values for both x and y.")
        y_label = y
    else:  # histogram
        numbers = [n for props in rows_in if (n := _numeric(props.get(x))) is not None]
        if not numbers:
            raise ValueError(f"No numeric values found in '{x}'.")
        bin_count = to_int(bins, "bins")
        if not 1 <= bin_count <= _limit(config, "max_bins"):
            raise ValueError(f"bins must be between 1 and {_limit(config, 'max_bins')}.")
        lo, hi = min(numbers), max(numbers)
        width = (hi - lo) / bin_count or 1.0
        counts = [0] * bin_count
        for n in numbers:
            counts[min(int((n - lo) / width), bin_count - 1)] += 1
        data = [{"bin_start": lo + i * width, "bin_end": lo + (i + 1) * width, "count": c}
                for i, c in enumerate(counts)]
        y_label = "count"

    chart_title = title if title is not None else (
        f"{y_label} by {x}" if kind in {"bar", "line", "pie"} else
        f"{y} vs {x}" if kind == "scatter" else f"Distribution of {x}"
    )
    if not isinstance(chart_title, str) or len(chart_title) > 200:
        raise ValueError("title must be a string of at most 200 characters.")
    renderer = {"bar": _render_bar, "line": _render_line, "scatter": _render_scatter,
                "pie": _render_pie, "histogram": _render_histogram}[kind]
    return {
        "type": "chart",
        "chart_type": kind,
        "title": chart_title,
        "spec": _vega(kind, data, x_label, y_label, chart_title),
        "svg": renderer(chart_title, data, x_label, y_label),
        "data": data,
        "metadata": {
            "source": PLUGIN_ID,
            "operation": "build_chart",
            "row_count": len(data),
            "input_feature_count": len(input_features),
            "created_at": utc_now_iso(),
            **source_info,
            **(metadata or {}),
        },
    }


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="Chart Builder",
    description="Builds bar, line, scatter, pie and histogram charts (Vega-Lite spec + SVG).",
    author="GeoChat Platform Team",
    permissions=[],
)
