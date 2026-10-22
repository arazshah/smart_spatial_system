"""
dem_fetcher.py

GeoChat SDK Plugin
==================

Plugin ID:
    dem_fetcher

Capability:
    - fetch_dem:
        Elevation raster for a bounding box from public Terrarium-encoded terrain tiles
        (default: the open "Mapzen/AWS Terrain Tiles"). The tiles are downloaded one by one under
        the shared network policy, decoded (elevation = R*256 + G + B/256 - 32768 m), stitched and
        cropped to the bbox.

Connector:
    source_type ``dem_tiles`` (raster, network access, no caller credentials: the tile server is
    deployer configuration).

CRS:
    The mosaic is in Web Mercator (EPSG:3857). Pixel size there is not the ground size away from the
    equator (it is stretched by 1/cos(latitude)), so for slope, hillshade or any metric analysis
    pass ``target_crs`` (for example a UTM zone) to get a reprojected DEM.

Limits:
    zoom <= max_zoom, at most ``max_tiles`` tiles per call, each tile bounded in bytes and decoded
    size. No tile cache in this version (every call downloads). Attribution is in the metadata.
"""

from __future__ import annotations

import math
import struct
import time
import zlib
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from orchestrator.provider_error_mapping import redact_provider_error_message
from plugins._shared.raster_common import DEFAULT_NODATA, make_raster_out, numpy_module
from plugins._shared.vector_common import load_config, to_float, to_int, utc_now_iso
from smart_spatial_system.shared.net_policy import NetworkPolicyError, fetch_limited

PLUGIN_ID = "dem_fetcher"

ATTRIBUTION = (
    "Terrain tiles: Mapzen / AWS Open Data (https://github.com/tilezen/joerd/blob/master/docs/attribution.md)"
)
DEFAULTS = {
    "tile_url": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
    "max_zoom": 14,
    "max_tiles": 36,
    "max_tile_bytes": 2_000_000,
    "timeout_seconds": 20,
    "min_interval_seconds": 0.0,
    "max_bbox_degrees": 2.0,
}
TILE = 256
MERCATOR_EXTENT = 20037508.342789244
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------ tile math
def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    return (lon * MERCATOR_EXTENT / 180.0,
            math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * MERCATOR_EXTENT / math.pi)


# -------------------------------------------------------------- PNG decoding
def decode_png_rgb(data: bytes, *, max_pixels: int = TILE * TILE * 4) -> Any:
    """Decode an 8-bit, non-interlaced RGB/RGBA PNG into an (h, w, 3) uint8 array (Pillow when
    available, otherwise a small built-in decoder). Refuses anything larger than ``max_pixels``."""
    np = numpy_module()
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("The tile is not a PNG image.")
    if len(data) < 24:
        raise ValueError("The PNG is incomplete.")
    width, height = struct.unpack(">II", data[16:24])  # IHDR comes first; check before any decoding
    if width * height > max_pixels:
        raise ValueError("The PNG tile is larger than allowed.")
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except ImportError:
        pass
    return _decode_png_builtin(data, max_pixels, np)


def _decode_png_builtin(data: bytes, max_pixels: int, np: Any) -> Any:
    position = len(_PNG_SIGNATURE)
    header = None
    idat = []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        raise ValueError("The PNG is incomplete.")
    width, height, depth, color_type, _, _, interlace = header
    if depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ValueError("Only 8-bit non-interlaced RGB/RGBA PNG tiles are supported.")
    if width * height > max_pixels:
        raise ValueError("The PNG tile is larger than allowed.")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    expected = height * (stride + 1)
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(b"".join(idat), expected + 1)  # cap output: no zip bombs
    if len(raw) != expected or decompressor.unconsumed_tail:
        raise ValueError("The PNG pixel data has an unexpected size.")
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, stride + 1)
    out = np.zeros((height, stride), dtype=np.uint8)
    previous = np.zeros(stride, dtype=np.int32)
    for y in range(height):
        filter_type = int(rows[y, 0])
        line = rows[y, 1:].astype(np.int32)
        if filter_type == 0:
            current = line
        elif filter_type == 1:  # Sub
            current = line.copy()
            for x in range(channels, stride):
                current[x] = (current[x] + current[x - channels]) & 255
        elif filter_type == 2:  # Up
            current = (line + previous) & 255
        elif filter_type == 3:  # Average
            current = line.copy()
            for x in range(stride):
                left = current[x - channels] if x >= channels else 0
                current[x] = (current[x] + ((left + previous[x]) >> 1)) & 255
        elif filter_type == 4:  # Paeth
            current = line.copy()
            for x in range(stride):
                a = current[x - channels] if x >= channels else 0
                b = previous[x]
                c = previous[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                current[x] = (current[x] + predictor) & 255
        else:
            raise ValueError("The PNG uses an unknown filter.")
        out[y] = current.astype(np.uint8)
        previous = current
    return out.reshape(height, width, channels)[:, :, :3]


def terrarium_to_meters(rgb: Any) -> Any:
    np = numpy_module()
    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    return r * 256.0 + g + b / 256.0 - 32768.0


# --------------------------------------------------------------------- tool
@capability(
    name="fetch_dem",
    keywords=[
        "dem", "elevation", "digital elevation model", "terrain", "download elevation", "srtm", "height data",
        "ارتفاع", "مدل رقومی ارتفاع", "دم", "داده ارتفاعی", "توپوگرافی", "دانلود ارتفاع",
    ],
    description=(
        "Download a digital elevation model (metres) for a WGS84 bounding box from open terrain tiles, "
        "stitched into one raster (Web Mercator, or reprojected with target_crs)."
    ),
    required_inputs=["bbox"],
    optional_inputs=["zoom", "target_crs", "metadata"],
    output_kind="raster",
    permissions=[],
    metadata={
        "connector": {
            "source_type": "dem_tiles",
            "kind": "raster",
            "access": ["network"],
            "credentials": "none",
        },
        "category": "data_io",
        "data_type": "raster",
        "operation": "fetch_dem",
        "returns": "RasterOut",
        "artifact_kind": "raster",
        "access_scope": "network_fetch",
        "config_aware": True,
        "routable": True,
    },
)
def fetch_dem(
    bbox: list[float] | dict[str, float],
    zoom: int | None = None,
    target_crs: str | int | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Fetch elevation for a bbox.

    Args:
        bbox: [min_lon, min_lat, max_lon, max_lat] WGS84 (each side at most max_bbox_degrees).
        zoom: Web-map zoom level (higher = finer; 10 is ~150 m/pixel, 12 ~40 m, 14 ~10 m at mid
            latitudes). Default: the finest zoom whose tile count fits ``max_tiles``.
        target_crs: Reproject the DEM (e.g. "EPSG:32639"); needs rasterio. Strongly recommended
            before slope/hillshade/zonal statistics.
        metadata: Extra output metadata.

    Returns:
        RasterOut with elevation in metres, cropped to the bbox; CRS EPSG:3857 unless target_crs.
    """
    np = numpy_module()
    config = load_config(PLUGIN_ID)
    if isinstance(bbox, dict):
        try:
            bbox = [bbox["minx"], bbox["miny"], bbox["maxx"], bbox["maxy"]]
        except KeyError as exc:
            raise ValueError("bbox dict needs minx, miny, maxx, maxy.") from exc
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat].")
    west, south, east, north = (to_float(v, "bbox") for v in bbox)
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError("bbox must be WGS84 [min_lon, min_lat, max_lon, max_lat] with min < max (|lat| <= 85).")
    side_limit = to_float(config.get("max_bbox_degrees", DEFAULTS["max_bbox_degrees"]), "max_bbox_degrees")
    if east - west > side_limit or north - south > side_limit:
        raise ValueError(f"bbox is too large: each side may span at most {side_limit} degrees.")

    max_zoom = to_int(config.get("max_zoom", DEFAULTS["max_zoom"]), "max_zoom")
    max_tiles = to_int(config.get("max_tiles", DEFAULTS["max_tiles"]), "max_tiles")

    def tile_range(level: int):
        x0, y1 = lonlat_to_tile(west, south, level)
        x1, y0 = lonlat_to_tile(east, north, level)
        return x0, x1, y0, y1

    if zoom is None:
        chosen = 0
        for level in range(max_zoom + 1):
            x0, x1, y0, y1 = tile_range(level)
            if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
                chosen = level
        level = chosen
    else:
        level = to_int(zoom, "zoom")
        if not 0 <= level <= max_zoom:
            raise ValueError(f"zoom must be between 0 and {max_zoom}.")
    x0, x1, y0, y1 = tile_range(level)
    count = (x1 - x0 + 1) * (y1 - y0 + 1)
    if count > max_tiles:
        raise ValueError(f"This bbox and zoom need {count} tiles; the limit is {max_tiles}. Lower the zoom or the bbox.")

    template = str(config.get("tile_url", DEFAULTS["tile_url"]))
    if not all(token in template for token in ("{z}", "{x}", "{y}")):
        raise ValueError("tile_url in the dem_fetcher config must contain {z}, {x} and {y}.")
    seconds = to_float(config.get("timeout_seconds", DEFAULTS["timeout_seconds"]), "timeout_seconds")
    max_bytes = to_int(config.get("max_tile_bytes", DEFAULTS["max_tile_bytes"]), "max_tile_bytes")
    pause = to_float(config.get("min_interval_seconds", DEFAULTS["min_interval_seconds"]), "min_interval_seconds")

    mosaic = np.zeros(((y1 - y0 + 1) * TILE, (x1 - x0 + 1) * TILE))
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = template.replace("{z}", str(level)).replace("{x}", str(tx)).replace("{y}", str(ty))
            if pause > 0:
                time.sleep(pause)
            try:
                body, _ = fetch_limited(
                    url, timeout=seconds, max_bytes=max_bytes,
                    allow_private_network=bool(config.get("allow_private_network", False)),
                    allowed_hosts=config.get("allowed_hosts") or None,
                    accept_content_types=["image/png"],
                )
                elevation = terrarium_to_meters(decode_png_rgb(body))
            except NetworkPolicyError as exc:
                raise ValueError(str(exc)) from None
            except ValueError:
                raise
            except Exception as exc:
                raise RuntimeError(
                    redact_provider_error_message(f"Failed to fetch terrain tile {level}/{tx}/{ty}: {type(exc).__name__}")
                ) from None
            if elevation.shape != (TILE, TILE):
                raise ValueError("Unexpected terrain tile size.")
            mosaic[(ty - y0) * TILE:(ty - y0 + 1) * TILE, (tx - x0) * TILE:(tx - x0 + 1) * TILE] = elevation

    resolution = 2 * MERCATOR_EXTENT / (TILE * 2**level)
    origin_x = -MERCATOR_EXTENT + x0 * TILE * resolution
    origin_y = MERCATOR_EXTENT - y0 * TILE * resolution
    # crop to the requested bbox
    min_x, min_y = lonlat_to_mercator(west, south)
    max_x, max_y = lonlat_to_mercator(east, north)
    col0 = max(0, int(math.floor((min_x - origin_x) / resolution)))
    col1 = min(mosaic.shape[1], int(math.ceil((max_x - origin_x) / resolution)))
    row0 = max(0, int(math.floor((origin_y - max_y) / resolution)))
    row1 = min(mosaic.shape[0], int(math.ceil((origin_y - min_y) / resolution)))
    cropped = mosaic[row0:row1, col0:col1]
    raster_meta = {
        "source": PLUGIN_ID, "operation": "fetch_dem", "crs": "EPSG:3857",
        "transform": [resolution, 0.0, origin_x + col0 * resolution, 0.0, -resolution,
                      origin_y - row0 * resolution],
        "width": int(cropped.shape[1]), "height": int(cropped.shape[0]), "band_count": 1,
        "nodata": None, "units": "metre", "zoom": level, "tile_count": count,
        "ground_resolution_m": resolution * math.cos(math.radians((south + north) / 2)),
        "bbox": [west, south, east, north], "attribution": ATTRIBUTION, "fetched_at": utc_now_iso(),
        **(metadata or {}),
    }
    result = make_raster_out(cropped, raster_meta, nodata=DEFAULT_NODATA)
    if target_crs is not None:
        from plugins.raster_tools import reproject_raster

        reprojected = reproject_raster(result, target_crs=target_crs, resampling="bilinear")
        reprojected.metadata.update(units="metre", zoom=level, attribution=ATTRIBUTION, bbox=[west, south, east, north])
        return reprojected
    return result


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="DEM Fetcher",
    description="Downloads open terrain tiles for a bbox and stitches them into an elevation raster.",
    author="GeoChat Platform Team",
    permissions=[],
)
