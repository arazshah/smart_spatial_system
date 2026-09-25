"""
Vectorized (numpy) raster helpers shared by the raster plugins' ``numpy``
engine.

The raster plugins were written as pure-python per-pixel loops over nested
lists. That is exact and dependency-free, but a real Sentinel-2 scene clipped
to a lake or a city (tens of millions of pixels) takes hours per step and
several GB of RAM as python objects. The ``numpy`` engine computes the same
result with array operations.

Contract kept identical to the python engine:

* nodata: a pixel is nodata when it is None, NaN, non-numeric, or equal to
  the effective ``nodata`` value - same rule as each plugin's ``_is_nodata``.
* array in -> array out: when the input raster's ``data`` is a
  ``numpy.ndarray`` the output ``data`` is a ``numpy.ndarray`` too (float
  NaN, or the numeric ``nodata`` value, marks nodata). When the input is
  nested lists the output is nested lists with ``None``/``nodata`` markers,
  exactly as the python engine returns them, so existing callers see no
  difference other than speed.
* ``auto`` picks ``numpy`` when numpy is importable, else ``python``.

No plugin-specific logic lives here.
"""

from __future__ import annotations

import math
from typing import Any

try:  # numpy is a hard dependency of rasterio/shapely, but keep plugins importable without it
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


def numpy_available() -> bool:
    return np is not None


def is_ndarray(value: Any) -> bool:
    return np is not None and isinstance(value, np.ndarray)


def resolve_engine(requested: str) -> str:
    """Map a validated engine name (python | numpy | auto) to the one used."""
    if requested == "python":
        return "python"
    if requested == "numpy":
        if np is None:
            raise ValueError("engine='numpy' requires numpy to be installed.")
        return "numpy"
    return "numpy" if np is not None else "python"


def ndarray_shape(data: Any) -> tuple[int, int, int]:
    """(bands, height, width) of a 2D or band-first 3D ndarray."""
    if data.ndim == 2:
        return 1, int(data.shape[0]), int(data.shape[1])
    if data.ndim == 3:
        return int(data.shape[0]), int(data.shape[1]), int(data.shape[2])
    raise ValueError("Raster ndarray must be 2D (rows, cols) or 3D band-first (bands, rows, cols).")


def _nodata_is_nan(nodata: Any) -> bool:
    return isinstance(nodata, float) and math.isnan(nodata)


def _numeric_nodata(nodata: Any) -> float | None:
    if nodata is None or isinstance(nodata, bool):
        return None
    if isinstance(nodata, (int, float)) and math.isfinite(float(nodata)):
        return float(nodata)
    if np is not None and isinstance(nodata, np.generic):
        v = float(nodata)
        return v if math.isfinite(v) else None
    return None


def to_float_bands(data: Any, nodata: Any = None) -> tuple[Any, Any]:
    """
    Raster data (ndarray or nested lists, 2D or band-first 3D) ->
    (values float64 [bands, rows, cols], valid bool [bands, rows, cols]).

    valid is False for None / NaN / +-inf / non-numeric /
    == nodata; booleans count as 1.0 / 0.0. values is NaN wherever valid
    is False.
    """
    if is_ndarray(data):
        arr = data
        if arr.ndim == 2:
            arr = arr[np.newaxis, ...]
        if arr.dtype == object:
            return _object_to_float(arr, nodata)
        values = arr.astype("float64", copy=False)
        valid = np.isfinite(values)
        owns = values is not arr and values.base is not arr
    else:
        obj = np.array(data, dtype=object)
        if obj.ndim == 2:
            obj = obj[np.newaxis, ...]
        if obj.ndim != 3:
            raise ValueError("Raster data must be 2D list or 3D band-first list.")
        return _object_to_float(obj, nodata)

    nd = _numeric_nodata(nodata)
    if nd is not None:
        valid &= values != nd
    if not valid.all():
        if not owns:  # never write into the caller's array
            values = values.copy()
        values[~valid] = np.nan
    return values, valid


def _object_to_float(obj: Any, nodata: Any) -> tuple[Any, Any]:
    nd = _numeric_nodata(nodata)

    def conv(v: Any) -> float:
        if v is None:
            return math.nan
        if isinstance(v, (bool, np.bool_)):
            return 1.0 if v else 0.0
        if isinstance(v, (int, float)) or (np is not None and isinstance(v, (np.integer, np.floating))):
            f = float(v)
            if not math.isfinite(f):
                return math.nan
            if nd is not None and f == nd:
                return math.nan
            return f
        return math.nan

    values = np.frompyfunc(conv, 1, 1)(obj).astype("float64")
    return values, np.isfinite(values)


def to_output(values: Any, valid: Any, *, nodata: Any, as_array: bool, integer: bool = False,
              boolean: bool = False) -> Any:
    """
    2D result -> the output ``data`` in the same form the input came in.

    as_array: float32/float64 ndarray (NaN or numeric nodata where invalid),
    or for boolean/integer outputs an ndarray of that kind when nothing is
    invalid. Otherwise nested lists of python floats/ints/bools with the
    ``nodata`` marker (None when nodata is None) where invalid - the python
    engine's representation.
    """
    if as_array:
        nd = _numeric_nodata(nodata)
        if boolean and valid.all():
            return values.astype(bool)
        if integer and valid.all():
            return values.astype("int64")
        out = values.astype("float64", copy=True)
        if not valid.all():
            out[~valid] = nd if nd is not None else np.nan
        return out

    if boolean:
        py = values.astype(bool).astype(object)
    elif integer:
        py = np.where(valid, values, 0).astype("int64").astype(object)
    else:
        py = values.astype("float64").astype(object)
    py[~valid] = nodata if not _nodata_is_nan(nodata) else float("nan")
    return py.tolist()


def round_half_even(values: Any, precision: int | None) -> Any:
    """Python's round() is round-half-even on the binary value; np.round matches for typical data."""
    if precision is None:
        return values
    return np.round(values, precision)


# ---------------------------------------------------------------------------
# Polygon -> pixel-centre mask, bit-for-bit the same rule as the python
# engine's ray casting (raster_clip_mask._point_in_ring): a centre is inside a
# ring when the number of ring edges with ((yi > y) != (yj > y)) and
# x < (xj - xi) * (y - yi) / (yj - yi) + xi is odd. Computed one pixel row at a
# time: crossings for that row's y, sorted, then counted per column with
# searchsorted - O(rows * (edges + cols)) instead of O(rows * cols * edges).
# ---------------------------------------------------------------------------

def _ring_array(ring: Any) -> Any:
    pts = np.asarray([(float(p[0]), float(p[1])) for p in ring], dtype="float64")
    return pts


def ring_center_mask(ring: Any, xs: Any, ys: Any) -> Any:
    """Pixel centres (xs: cols, ys: rows) inside one ring -> bool [rows, cols]."""
    out = np.zeros((len(ys), len(xs)), dtype=bool)
    if ring is None or len(ring) < 3:
        return out
    pts = _ring_array(ring)
    xi, yi = pts[:, 0], pts[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)  # j = i - 1, as in the python loop (j starts at n - 1)
    ymin, ymax = min(yi.min(), yj.min()), max(yi.max(), yj.max())
    for r, y in enumerate(ys):
        if y < ymin or y > ymax:
            continue
        straddle = (yi > y) != (yj > y)
        if not straddle.any():
            continue
        a_xi, a_yi, a_xj, a_yj = xi[straddle], yi[straddle], xj[straddle], yj[straddle]
        xc = (a_xj - a_xi) * (y - a_yi) / (a_yj - a_yi) + a_xi
        xc.sort()
        # number of crossings strictly greater than x (x < xc)
        n_greater = len(xc) - np.searchsorted(xc, xs, side="right")
        out[r] = (n_greater % 2) == 1
    return out


def polygon_center_mask(polygon_coords: Any, xs: Any, ys: Any) -> Any:
    """GeoJSON Polygon coordinates (outer + holes) -> centre-inside mask."""
    if not polygon_coords:
        return np.zeros((len(ys), len(xs)), dtype=bool)
    mask = ring_center_mask(polygon_coords[0], xs, ys)
    for hole in polygon_coords[1:]:
        if mask.any():
            mask &= ~ring_center_mask(hole, xs, ys)
    return mask


def pixel_centers(transform: Any, row_start: int, row_stop: int, col_start: int, col_stop: int
                  ) -> tuple[Any, Any] | None:
    """Centre x per column and y per row for a non-rotated transform, else None.

    Same arithmetic as raster_clip_mask._pixel_center (a*(col+.5) + b*(row+.5) + c).
    """
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    if b != 0.0 or d != 0.0:
        return None
    cols = np.arange(col_start, col_stop, dtype="float64")
    rows = np.arange(row_start, row_stop, dtype="float64")
    xs = a * (cols + 0.5) + 0.0 + c
    ys = 0.0 + e * (rows + 0.5) + f
    return xs, ys


def numeric_band(data: Any, band_index: int) -> Any | None:
    """
    One band (1-based) as float64 with NaN for None/NaN, or None when the
    band holds anything the python engines treat specially (bools,
    strings, other objects) - callers then keep the python path.
    """
    if is_ndarray(data):
        band = data if data.ndim == 2 else data[band_index - 1]
        if band.dtype.kind in "iuf":
            return band.astype("float64", copy=False)
        if band.dtype != object:
            return None
        obj = band
    else:
        obj = np.array(data, dtype=object)
        if obj.ndim == 3:
            obj = obj[band_index - 1]
        elif obj.ndim != 2:
            return None

    def ok(v: Any) -> bool:
        return v is None or (isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_)))

    if not np.frompyfunc(ok, 1, 1)(obj).astype(bool).all():
        return None
    return np.frompyfunc(lambda v: math.nan if v is None else float(v), 1, 1)(obj).astype("float64")


def label_runs(selected: Any, values: Any, connectivity: int) -> tuple[Any, int]:
    """
    Connected-component labels (1..n, 0 = unselected) of selected cells,
    growing only between equal values - the same components as the
    python engine's BFS - via horizontal runs and union-find, so the python
    work is O(runs), not O(cells). Labels are numbered in row-major order of
    each component's first cell, which is the python engine's order.
    """
    height, width = selected.shape
    runs_row: list[int] = []
    runs_c0: list[int] = []
    runs_c1: list[int] = []  # exclusive
    runs_val: list[float] = []
    row_first: list[int] = []

    for r in range(height):
        row_first.append(len(runs_row))
        sel = selected[r]
        if not sel.any():
            continue
        val = values[r]
        # boundaries between columns k-1 and k where a run cannot continue
        brk = np.ones(width + 1, dtype=bool)
        if width > 1:
            brk[1:width] = ~(sel[1:] & sel[:-1] & (val[1:] == val[:-1]))
        cut = np.nonzero(brk)[0]
        starts = cut[:-1]
        ends = cut[1:]
        keep = sel[starts]
        for s, e in zip(starts[keep].tolist(), ends[keep].tolist()):
            runs_row.append(r)
            runs_c0.append(s)
            runs_c1.append(e)
            runs_val.append(float(val[s]))
    row_first.append(len(runs_row))

    n = len(runs_row)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ext = 1 if connectivity == 8 else 0
    for r in range(1, height):
        a0, a1 = row_first[r - 1], row_first[r]
        b0, b1 = row_first[r], row_first[r + 1]
        i = a0
        for j in range(b0, b1):
            while i < a1 and runs_c1[i] + ext <= runs_c0[j]:
                i += 1
            k = i
            while k < a1 and runs_c0[k] < runs_c1[j] + ext:
                if runs_val[k] == runs_val[j]:
                    rk, rj = find(k), find(j)
                    if rk != rj:
                        # keep the earlier (row-major) run as root
                        if rk < rj:
                            parent[rj] = rk
                        else:
                            parent[rk] = rj
                k += 1

    labels = np.zeros((height, width), dtype="int64")
    label_of_root: dict[int, int] = {}
    for idx in range(n):
        root = find(idx)
        lab = label_of_root.get(root)
        if lab is None:
            lab = len(label_of_root) + 1
            label_of_root[root] = lab
        labels[runs_row[idx], runs_c0[idx]:runs_c1[idx]] = lab
    return labels, len(label_of_root)


def boundary_edges_by_label(labels: Any) -> dict[int, dict[tuple[int, int], list[tuple[int, int]]]]:
    """
    Directed boundary edges of every labelled component (component on the
    left in x = col, y = -row), keyed by label - the vectorized equivalent of
    raster_to_vector._component_boundary_edges for all components at once.
    """
    padded = np.pad(labels, 1)
    core = padded[1:-1, 1:-1]
    out: dict[int, dict[tuple[int, int], list[tuple[int, int]]]] = {}

    sides = [
        (padded[2:, 1:-1], (1, 0), (1, 1)),   # bottom: (r+1,c) -> (r+1,c+1)
        (padded[1:-1, 2:], (1, 1), (0, 1)),   # right:  (r+1,c+1) -> (r,c+1)
        (padded[:-2, 1:-1], (0, 1), (0, 0)),  # top:    (r,c+1) -> (r,c)
        (padded[1:-1, :-2], (0, 0), (1, 0)),  # left:   (r,c) -> (r+1,c)
    ]
    for neighbour, (sr, sc), (er, ec) in sides:
        rows, cols = np.nonzero((core > 0) & (neighbour != core))
        labs = core[rows, cols]
        for lab, r, c in zip(labs.tolist(), rows.tolist(), cols.tolist()):
            out.setdefault(lab, {}).setdefault((r + sr, c + sc), []).append((r + er, c + ec))
    return out
