"""dem_fetcher: tile maths, PNG decoding (built-in decoder), Terrarium elevations, limits."""

from __future__ import annotations

import math
import struct
import zlib

import pytest

np = pytest.importorskip("numpy")

import plugins.dem_fetcher as module  # noqa: E402
from plugins.dem_fetcher import (  # noqa: E402
    TILE,
    _decode_png_builtin,
    decode_png_rgb,
    fetch_dem,
    lonlat_to_mercator,
    lonlat_to_tile,
    terrarium_to_meters,
)
from smart_spatial_system.shared.net_policy import NetworkPolicyError  # noqa: E402


# --------------------------------------------------------------- PNG test helpers
def png_bytes(rgb, filters=None, color_type=2, depth=8, interlace=0, extra_idat=b""):
    height, width, _ = rgb.shape
    channels = 3 if color_type == 2 else 4
    pixels = rgb if channels == 3 else np.concatenate([rgb, np.full((height, width, 1), 255, np.uint8)], axis=2)
    stride = width * channels
    rows, previous = [], np.zeros(stride, dtype=np.int32)
    for y in range(height):
        raw = pixels[y].reshape(-1).astype(np.int32)
        kind = 0 if filters is None else filters[y % len(filters)]
        out = np.zeros(stride, dtype=np.int32)
        for x in range(stride):
            a = raw[x - channels] if x >= channels else 0
            b = previous[x]
            c = previous[x - channels] if x >= channels else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = a
            elif kind == 2:
                predictor = b
            elif kind == 3:
                predictor = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out[x] = (raw[x] - predictor) & 255
        rows.append(bytes([kind]) + bytes(out.astype(np.uint8)))
        previous = raw

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(b"".join(rows)) + extra_idat) + chunk(b"IEND", b""))


def elevation_tile(value_fn):
    """A 256x256 Terrarium PNG whose elevation is value_fn(row, col)."""
    rows = np.arange(TILE)[:, None]
    cols = np.arange(TILE)[None, :]
    meters = value_fn(rows, cols) + 32768.0
    r = np.floor(meters / 256).astype(np.uint8)
    g = np.floor(meters % 256).astype(np.uint8)
    b = np.floor((meters - np.floor(meters)) * 256).astype(np.uint8)
    return png_bytes(np.stack([r, g, b], axis=-1), filters=[2])


# ------------------------------------------------------------------------ maths
def test_tile_and_mercator_maths():
    assert lonlat_to_tile(0, 0, 1) == (1, 1)
    assert lonlat_to_tile(-180, 85, 0) == (0, 0)
    assert lonlat_to_tile(180, -85, 2) == (3, 3)
    x, y = lonlat_to_tile(51.389, 35.689, 10)
    assert (x, y) == (658, 403)
    assert lonlat_to_mercator(0, 0) == pytest.approx((0, 0), abs=1e-6)
    mx, my = lonlat_to_mercator(180, 85.05112878)
    assert mx == pytest.approx(20037508.342789244) and my == pytest.approx(20037508.34, rel=1e-6)


def test_terrarium_conversion():
    rgb = np.array([[[128, 0, 0], [128, 100, 128], [127, 255, 0], [140, 0, 0]]], dtype=np.uint8)
    assert list(terrarium_to_meters(rgb)[0]) == [0.0, 100.5, -1.0, 3072.0]


# ------------------------------------------------------------------- PNG decoder
@pytest.mark.parametrize("filters", [[0], [1], [2], [3], [4], [0, 1, 2, 3, 4]])
@pytest.mark.parametrize("color_type", [2, 6])
def test_builtin_decoder_handles_every_filter(filters, color_type):
    rng = np.random.default_rng(5)
    image = rng.integers(0, 256, size=(12, 17, 3), dtype=np.uint8)
    decoded = _decode_png_builtin(png_bytes(image, filters, color_type), 10_000, np)
    assert decoded.shape == (12, 17, 3) and np.array_equal(decoded, image)
    assert np.array_equal(decode_png_rgb(png_bytes(image, filters, color_type)), image)  # PIL or built-in


def test_decoder_rejects_unsupported_oversized_and_hostile_files():
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="not a PNG"):
        decode_png_rgb(b"GIF89a....")
    with pytest.raises(ValueError, match="Only 8-bit"):
        _decode_png_builtin(png_bytes(image, depth=16), 10_000, np)
    with pytest.raises(ValueError, match="Only 8-bit"):
        _decode_png_builtin(png_bytes(image, interlace=1), 10_000, np)
    with pytest.raises(ValueError, match="larger than allowed"):
        _decode_png_builtin(png_bytes(image), 10, np)
    with pytest.raises(ValueError, match="incomplete"):
        _decode_png_builtin(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, 10_000, np)
    # decompression bomb: the header says 8x8 but the data inflates far past it
    header, _, _ = png_bytes(image).partition(b"IDAT")
    big = zlib.compress(b"\x00" * 5_000_000)
    forged = header[:-4] + struct.pack(">I", len(big)) + b"IDAT" + big + struct.pack(">I", zlib.crc32(b"IDAT" + big))
    with pytest.raises(ValueError, match="unexpected size"):
        _decode_png_builtin(forged, 10_000, np)


# ----------------------------------------------------------------------- fetch_dem
@pytest.fixture
def tiles(monkeypatch):
    """Serve generated Terrarium tiles: elevation = 1000 + tile_x*10 + tile_y."""
    calls = []
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_tiles": 36})

    def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        z, x, y = (int(v) for v in url.rsplit("/", 3)[1:]) if False else \
            tuple(int(p.removesuffix(".png")) for p in url.split("/")[-3:])
        return elevation_tile(lambda r, c, x=x, y=y: 1000.0 + x * 10 + y + 0 * r + 0 * c), \
            {"content_type": "image/png", "status": 200}

    monkeypatch.setattr(module, "fetch_limited", fake_fetch)
    return calls


def test_fetch_dem_georeferencing_cropping_and_values(tiles):
    out = fetch_dem([51.30, 35.60, 51.45, 35.75], zoom=10)
    meta = out.metadata
    assert meta["crs"] == "EPSG:3857" and meta["zoom"] == 10 and meta["units"] == "metre"
    tx0, ty0 = lonlat_to_tile(51.30, 35.75, 10)
    assert {u.split("/")[-3] for u, _ in tiles} == {"10"} and meta["tile_count"] == len(tiles)
    assert all(kw["accept_content_types"] == ["image/png"] for _, kw in tiles)
    resolution = 2 * 20037508.342789244 / (256 * 2**10)
    a, _, c, _, e, f = meta["transform"]
    assert a == pytest.approx(resolution) and e == pytest.approx(-resolution)
    mx0, my_top = lonlat_to_mercator(51.30, 35.75)
    assert abs(c - mx0) <= resolution and abs(f - my_top) <= resolution  # origin is the bbox corner
    data = np.array(out.data)
    assert data.shape == (meta["height"], meta["width"]) and data[0, 0] == 1000 + tx0 * 10 + ty0
    assert meta["ground_resolution_m"] < resolution
    assert "Terrain tiles" in meta["attribution"]


def test_fetch_dem_picks_a_zoom_that_fits_the_tile_budget(tiles, monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_tiles": 4, "max_zoom": 14})
    out = fetch_dem([51.30, 35.60, 51.40, 35.70])
    assert out.metadata["tile_count"] <= 4 and out.metadata["zoom"] >= 8


def test_fetch_dem_validation_and_limits(tiles, monkeypatch):
    for bbox in (None, [1, 2, 3], [51.4, 35.7, 51.3, 35.6], [0, 0, 5, 5], [51, 89, 52, 90], ["a", 1, 2, 3]):
        with pytest.raises(ValueError):
            fetch_dem(bbox)
    with pytest.raises(ValueError, match="zoom"):
        fetch_dem([51.3, 35.6, 51.4, 35.7], zoom=15)
    with pytest.raises(ValueError, match="tiles"):
        fetch_dem([51.0, 35.0, 52.5, 36.5], zoom=14)
    assert tiles == []  # nothing was downloaded for refused requests
    monkeypatch.setattr(module, "load_config", lambda _id: {"tile_url": "https://tiles.example/{z}/{x}.png"})
    with pytest.raises(ValueError, match="tile_url"):
        fetch_dem([51.3, 35.6, 51.4, 35.7], zoom=9)


def test_fetch_dem_network_errors_are_redacted(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {})

    def refuse(url, **kwargs):
        raise NetworkPolicyError("url resolves to a disallowed network address")

    monkeypatch.setattr(module, "fetch_limited", refuse)
    with pytest.raises(ValueError, match="disallowed network address"):
        fetch_dem([51.3, 35.6, 51.4, 35.7], zoom=9)

    def explode(url, **kwargs):
        raise ConnectionError("failed https://u:secret@tiles.example/?key=SECRET")

    monkeypatch.setattr(module, "fetch_limited", explode)
    with pytest.raises(RuntimeError) as err:
        fetch_dem([51.3, 35.6, 51.4, 35.7], zoom=9)
    assert "secret" not in str(err.value).lower() and "ConnectionError" in str(err.value)


def test_fetch_dem_can_reproject(tiles):
    pytest.importorskip("rasterio")
    out = fetch_dem([51.30, 35.65, 51.36, 35.70], zoom=11, target_crs="EPSG:32639")
    assert out.metadata["crs"] == "EPSG:32639" and out.metadata["units"] == "metre"
    assert math.isclose(abs(out.metadata["transform"][0]), 62.0, rel_tol=0.1)  # z11 ground pixel at 35.7N (76.4 m * cos)
