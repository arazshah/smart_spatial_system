"""
orchestrator.planning.input_layers

Describe a query's own input layers to the planner, and check that a plan
only reads layers that exist.

Why this exists: s3geo.query(question, layers={...}) executes the plan
against the named layers, but the LLM that writes the plan was never told
their names, whether each one is a raster or a vector layer, how many bands
a raster has (and what they are), or which attribute fields a vector layer
carries. input_data_extent adds the combined extent of the *vector* layers
only - raster layers are skipped entirely. So the model guessed: entity refs
such as "sentinel2_image" or "districts_layer", band 4 for red in a 3-band
stack, a zone_id_field that is not in the data. Every such plan passed
generation-time validation and then failed inside DagExecutor with
"Reference '$inputs.<guess>' could not be resolved" - after planning, where
the repair loop can no longer help.

describe_input_layers() measures the facts from the layer values
themselves (nothing is inferred from the question), render_input_layer_facts()
states them in the system prompt, and unknown_input_refs() lets the
generator reject - and repair - a plan that reads a layer that isn't there.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Keep the prompt small for layers with many fields/bands.
MAX_FIELDS = 40
MAX_BANDS = 32


@dataclass(frozen=True)
class InputLayer:
    name: str
    kind: str  # "raster" | "vector" | "other"
    crs: str | None = None
    # raster
    bands: int | None = None
    height: int | None = None
    width: int | None = None
    pixel_size: tuple[float, float] | None = None
    band_names: tuple[str, ...] = ()
    dtype: str | None = None
    nodata: Any = None
    # vector
    feature_count: int | None = None
    geometry_types: tuple[str, ...] = ()
    fields: tuple[tuple[str, str], ...] = ()  # (name, python type of first non-null value)
    notes: tuple[str, ...] = field(default_factory=tuple)


def _raster_parts(layer: Any) -> tuple[Any, Mapping[str, Any]] | None:
    """(data, metadata) of a raster-shaped layer value, else None."""
    if isinstance(layer, Mapping):
        if "data" in layer or "array" in layer:
            data = layer.get("data") if "data" in layer else layer.get("array")
            meta = layer.get("metadata") or {}
            if isinstance(meta, Mapping):
                return data, meta
        return None
    data = getattr(layer, "data", None)
    meta = getattr(layer, "metadata", None)
    if data is not None and isinstance(meta, Mapping) and not hasattr(layer, "features"):
        return data, meta
    return None


def _shape(data: Any) -> tuple[int, int, int] | None:
    shape = getattr(data, "shape", None)
    if shape is not None:
        if len(shape) == 2:
            return 1, int(shape[0]), int(shape[1])
        if len(shape) == 3:
            return int(shape[0]), int(shape[1]), int(shape[2])
        return None
    if isinstance(data, list) and data:
        if isinstance(data[0], list) and data[0] and isinstance(data[0][0], list):
            return len(data), len(data[0]), len(data[0][0])
        if isinstance(data[0], list):
            return 1, len(data), len(data[0])
    return None


def _band_names(meta: Mapping[str, Any], bands: int) -> tuple[str, ...]:
    for key in ("band_names", "band_descriptions", "descriptions", "bands"):
        value = meta.get(key)
        if isinstance(value, (list, tuple)) and len(value) == bands and all(
            isinstance(v, (str, int, float)) for v in value
        ):
            return tuple(str(v) for v in value)
    return ()


def _pixel_size(meta: Mapping[str, Any]) -> tuple[float, float] | None:
    transform = meta.get("transform", meta.get("affine_transform"))
    try:
        if isinstance(transform, Mapping):
            a, e = float(transform["a"]), float(transform["e"])
        elif transform is not None:
            values = list(transform)
            a, e = float(values[0]), float(values[4])
        else:
            return None
    except Exception:
        return None
    return abs(a), abs(e)


def _crs_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        authority = value.to_authority()
        if authority:
            return f"{authority[0]}:{authority[1]}"
    except Exception:
        pass
    return str(value)


def _vector_features(layer: Any) -> tuple[list[Any], str | None] | None:
    """(features, crs) of a vector layer value (GeoDataFrame or GeoJSON), else None."""
    if hasattr(layer, "to_json") and hasattr(layer, "geometry"):
        import json

        crs = _crs_text(getattr(layer, "crs", None))
        return json.loads(layer.to_json(default=str)).get("features", []), crs
    if isinstance(layer, Mapping):
        gtype = layer.get("type")
        crs = None
        crs_member = layer.get("crs")
        if isinstance(crs_member, Mapping):
            crs = (crs_member.get("properties") or {}).get("name")
        if gtype == "FeatureCollection":
            return list(layer.get("features") or []), crs
        if gtype == "Feature":
            return [layer], crs
    if hasattr(layer, "features") and isinstance(getattr(layer, "features"), list):
        meta = getattr(layer, "metadata", None) or {}
        return list(layer.features), meta.get("crs") if isinstance(meta, Mapping) else None
    return None


def describe_input_layers(layers: Mapping[str, Any] | None) -> tuple[InputLayer, ...]:
    """Facts about every named input layer, measured from the values themselves."""
    if not layers:
        return ()
    out: list[InputLayer] = []
    for name, layer in layers.items():
        raster = _raster_parts(layer)
        if raster is not None:
            data, meta = raster
            shape = _shape(data)
            if shape is not None:
                bands, height, width = shape
                dtype = getattr(getattr(data, "dtype", None), "name", None)
                out.append(
                    InputLayer(
                        name=str(name),
                        kind="raster",
                        crs=_crs_text(meta.get("crs")),
                        bands=bands,
                        height=height,
                        width=width,
                        pixel_size=_pixel_size(meta),
                        band_names=_band_names(meta, bands),
                        dtype=dtype,
                        nodata=meta.get("nodata"),
                    )
                )
                continue
        vector = _vector_features(layer)
        if vector is not None:
            features, crs = vector
            gtypes: list[str] = []
            fields: dict[str, str] = {}
            for feature in features:
                if not isinstance(feature, Mapping):
                    continue
                geometry = feature.get("geometry")
                if isinstance(geometry, Mapping) and geometry.get("type") not in gtypes:
                    gtypes.append(str(geometry.get("type")))
                for key, value in (feature.get("properties") or {}).items():
                    if key not in fields or (fields[key] == "null" and value is not None):
                        fields[key] = "null" if value is None else type(value).__name__
            out.append(
                InputLayer(
                    name=str(name),
                    kind="vector",
                    crs=crs,
                    feature_count=len(features),
                    geometry_types=tuple(gtypes),
                    fields=tuple(list(fields.items())[:MAX_FIELDS]),
                    notes=(f"{len(fields) - MAX_FIELDS} more fields not listed",) if len(fields) > MAX_FIELDS else (),
                )
            )
            continue
        out.append(InputLayer(name=str(name), kind="other"))
    return tuple(out)


def render_input_layer_facts(layers: tuple[InputLayer, ...]) -> str:
    """The system-prompt section naming the query's input layers."""
    lines = [
        "Input layers available to this query (measured from the data passed in - "
        "these are the ONLY layer names that exist):",
    ]
    for layer in layers:
        if layer.kind == "raster":
            desc = f'- "{layer.name}": raster, {layer.bands} band(s), {layer.height} x {layer.width} pixels'
            if layer.pixel_size:
                desc += f", pixel size {layer.pixel_size[0]:g} x {layer.pixel_size[1]:g}"
            if layer.crs:
                desc += f", CRS {layer.crs}"
            if layer.dtype:
                desc += f", dtype {layer.dtype}"
            desc += f", nodata {layer.nodata!r}" if layer.nodata is not None else ", nodata = NaN/None"
            if layer.band_names:
                names = ", ".join(f"b{i + 1} = {n}" for i, n in enumerate(layer.band_names[:MAX_BANDS]))
                desc += f". Bands: {names}"
            lines.append(desc + ".")
        elif layer.kind == "vector":
            desc = f'- "{layer.name}": vector, {layer.feature_count} feature(s)'
            if layer.geometry_types:
                desc += f" ({', '.join(layer.geometry_types)})"
            if layer.crs:
                desc += f", CRS {layer.crs}"
            if layer.fields:
                desc += ". Fields: " + ", ".join(f"{k} ({t})" for k, t in layer.fields)
            for note in layer.notes:
                desc += f"; {note}"
            lines.append(desc + ".")
        else:
            lines.append(f'- "{layer.name}": (not a raster or vector layer).')
    lines.append(
        "Use these names exactly as entity refs and operation inputs; for band_math "
        "b1, b2, ... are the raster's bands in the order listed. Do not invent layer "
        "names or fields that are not listed here."
    )
    return "\n".join(lines)


def unknown_input_refs(spec: Any, layers: tuple[InputLayer, ...]) -> list[str]:
    """
    Messages for operation inputs that are neither an input layer nor the
    output of an earlier operation (empty list = plan reads only real data).
    """
    known = {layer.name for layer in layers}
    kinds = {layer.name: layer.kind for layer in layers}
    produced: set[str] = set()
    problems: list[str] = []
    for index, op in enumerate(getattr(spec, "operations", []) or []):
        inputs = getattr(op, "inputs", {}) or {}
        for role, ref in inputs.items():
            refs = ref if isinstance(ref, list) else [ref]
            for item in refs:
                if not isinstance(item, str):
                    continue
                key = item
                for prefix in ("$inputs.", "$input.", "$entity.", "$entities."):
                    if key.startswith(prefix):
                        key = key[len(prefix):]
                if key in produced:
                    continue
                if key not in known:
                    problems.append(
                        f"operations[{index}] ({getattr(op, 'op', '?')}) input {role!r} reads {item!r}, "
                        f"which is neither an input layer ({', '.join(sorted(known))}) nor the output "
                        "of an earlier operation."
                    )
                elif role == "raster" and kinds.get(key) == "vector":
                    problems.append(
                        f"operations[{index}] ({getattr(op, 'op', '?')}) input 'raster' reads {item!r}, "
                        "which is a vector layer."
                    )
                elif role in {"zones", "features", "vector"} and kinds.get(key) == "raster":
                    problems.append(
                        f"operations[{index}] ({getattr(op, 'op', '?')}) input {role!r} reads {item!r}, "
                        "which is a raster layer."
                    )
        output = getattr(op, "output", None)
        if isinstance(output, str):
            produced.add(output)
    return problems
