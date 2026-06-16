"""
Tests for local_raster_loader plugin.

Run:
    pytest tests/test_local_raster_loader.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.local_raster_loader import (  # noqa: E402
    ALLOWED_RASTER_EXTENSIONS,
    PLUGIN,
    PLUGIN_ID,
    _validate_path,
    load_local_raster,
)


@pytest.fixture
def sample_geotiff(tmp_path: Path) -> str:
    """
    Create a valid temporary GeoTIFF file.
    """
    raster_path = tmp_path / "sample.tif"

    width = 10
    height = 8
    band_count = 3

    data = np.zeros((band_count, height, width), dtype=np.uint8)
    data[0, :, :] = 10
    data[1, :, :] = 20
    data[2, :, :] = 30

    transform = from_origin(
        west=50.0,
        north=35.0,
        xsize=0.01,
        ysize=0.01,
    )

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=band_count,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(data)

    return str(raster_path)


@pytest.fixture
def corrupt_tif(tmp_path: Path) -> str:
    """
    Create a file with .tif extension but invalid raster content.
    """
    raster_path = tmp_path / "corrupt.tif"
    raster_path.write_bytes(b"this is not a valid raster file")
    return str(raster_path)


def test_plugin_manifest_basic_fields() -> None:
    """
    PLUGIN must expose a valid manifest.
    """
    assert PLUGIN.manifest.id == PLUGIN_ID
    assert PLUGIN.manifest.id == "local_raster_loader"
    assert PLUGIN.manifest.version == "1.0.0"
    assert PLUGIN.manifest.name == "Local Raster Loader"
    assert "filesystem" in PLUGIN.manifest.permissions


def test_allowed_raster_extensions() -> None:
    """
    Important raster extensions must be supported.
    """
    assert ".tif" in ALLOWED_RASTER_EXTENSIONS
    assert ".tiff" in ALLOWED_RASTER_EXTENSIONS
    assert ".geotiff" in ALLOWED_RASTER_EXTENSIONS
    assert ".vrt" in ALLOWED_RASTER_EXTENSIONS


def test_validate_path_success(sample_geotiff: str) -> None:
    """
    _validate_path should return resolved Path for valid raster path.
    """
    resolved = _validate_path(sample_geotiff)
    assert isinstance(resolved, Path)
    assert resolved.exists()
    assert resolved.is_file()
    assert resolved.suffix.lower() == ".tif"


def test_validate_path_rejects_empty_path() -> None:
    """
    Empty path must raise ValueError.
    """
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_path("")


def test_validate_path_rejects_none() -> None:
    """
    None path must raise ValueError.
    """
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_path(None)  # type: ignore[arg-type]


def test_validate_path_rejects_missing_file() -> None:
    """
    Missing raster file must raise FileNotFoundError.
    """
    with pytest.raises(FileNotFoundError):
        _validate_path("/tmp/this_file_does_not_exist_12345.tif")


def test_validate_path_rejects_directory(tmp_path: Path) -> None:
    """
    Directory path must not be accepted as raster file.
    """
    raster_dir = tmp_path / "folder.tif"
    raster_dir.mkdir()

    with pytest.raises(ValueError, match="not a file"):
        _validate_path(str(raster_dir))


def test_validate_path_rejects_invalid_extension(tmp_path: Path) -> None:
    """
    Invalid extension must raise ValueError when strict_extensions=True.
    """
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("not a raster")

    with pytest.raises(ValueError, match="Unsupported raster extension"):
        _validate_path(str(txt_file), strict_extensions=True)


def test_validate_path_allows_invalid_extension_when_not_strict(tmp_path: Path) -> None:
    """
    Invalid extension can pass path validation if strict_extensions=False.

    This only validates path. It does not mean rasterio can open it.
    """
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("not a raster")

    resolved = _validate_path(str(txt_file), strict_extensions=False)
    assert resolved.exists()
    assert resolved.suffix == ".txt"


def test_load_local_raster_success(sample_geotiff: str) -> None:
    """
    load_local_raster should return RasterOut for a valid GeoTIFF.
    """
    result = load_local_raster(sample_geotiff)

    assert result is not None
    assert result.path == str(Path(sample_geotiff).resolve())
    assert isinstance(result.metadata, dict)


def test_load_local_raster_metadata_core_fields(sample_geotiff: str) -> None:
    """
    Metadata should contain core raster information.
    """
    result = load_local_raster(sample_geotiff)
    md = result.metadata

    assert md["source"] == "local_file"
    assert md["loader"] == "local_raster_loader"
    assert md["filename"] == "sample.tif"
    assert md["extension"] == ".tif"
    assert md["driver"] == "GTiff"
    assert md["width"] == 10
    assert md["height"] == 8
    assert md["band_count"] == 3
    assert md["dtypes"] == ["uint8", "uint8", "uint8"]
    assert md["crs"] == "EPSG:4326"
    assert md["nodata"] == 0.0
    assert md["file_size_bytes"] > 0


def test_load_local_raster_bounds_and_resolution(sample_geotiff: str) -> None:
    """
    Bounds and resolution should be extracted correctly.
    """
    result = load_local_raster(sample_geotiff)
    md = result.metadata

    bounds = md["bounds"]
    resolution = md["resolution"]

    assert bounds["minx"] == pytest.approx(50.0)
    assert bounds["maxy"] == pytest.approx(35.0)
    assert bounds["maxx"] == pytest.approx(50.1)
    assert bounds["miny"] == pytest.approx(34.92)

    assert resolution["x"] == pytest.approx(0.01)
    assert resolution["y"] == pytest.approx(0.01)


def test_load_local_raster_rejects_corrupt_file(corrupt_tif: str) -> None:
    """
    Corrupted raster content should raise RuntimeError.
    """
    with pytest.raises(RuntimeError, match="corrupted|cannot be opened|Failed"):
        load_local_raster(corrupt_tif)


def test_load_local_raster_rejects_invalid_extension(tmp_path: Path) -> None:
    """
    load_local_raster should reject unsupported extensions by default.
    """
    file_path = tmp_path / "sample.txt"
    file_path.write_text("not raster")

    with pytest.raises(ValueError, match="Unsupported raster extension"):
        load_local_raster(str(file_path))


def test_load_local_raster_uppercase_extension(tmp_path: Path) -> None:
    """
    Uppercase raster extension should be accepted.
    """
    raster_path = tmp_path / "UPPER.TIF"

    data = np.ones((1, 4, 4), dtype=np.uint8)
    transform = from_origin(10.0, 20.0, 1.0, 1.0)

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data)

    result = load_local_raster(str(raster_path))

    assert result.metadata["width"] == 4
    assert result.metadata["height"] == 4
    assert result.metadata["extension"] == ".tif"


def test_rasterout_to_artifact(sample_geotiff: str) -> None:
    """
    RasterOut must be convertible to SDK/Kernel ExecutionArtifact.
    """
    result = load_local_raster(sample_geotiff)
    artifact = result.to_artifact(produced_by="test_local_raster_loader")

    assert artifact.kind == "raster_ref"
    assert artifact.produced_by == "test_local_raster_loader"
    assert artifact.payload["path"] == str(Path(sample_geotiff).resolve())
    assert artifact.payload["source"] == "local_file"
    assert artifact.payload["loader"] == "local_raster_loader"
    assert artifact.payload["width"] == 10
    assert artifact.payload["height"] == 8


def test_capability_registered_inside_plugin() -> None:
    """
    auto_collect should collect decorated capabilities into SDKPlugin.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    names = [reg.name for reg in regs]

    assert "load_local_raster" in names
    assert len(regs) >= 1


def test_capability_descriptor_content() -> None:
    """
    Capability descriptor generated by SDK registration should contain expected fields.
    """
    regs = getattr(PLUGIN, "_capabilities_regs", [])
    reg = next(item for item in regs if item.name == "load_local_raster")

    descriptor = reg.build_descriptor(plugin_id=PLUGIN_ID)

    assert descriptor.name == "load_local_raster"
    assert descriptor.plugin_id == PLUGIN_ID
    assert descriptor.kind == "capability"
    assert descriptor.output_kind == "raster"
    assert "path" in descriptor.required_inputs
    assert "strict_extensions" in descriptor.optional_inputs
    assert "filesystem" in descriptor.requires_permissions
    assert descriptor.metadata["routable"] is True
    assert descriptor.metadata["category"] == "data_io"
    assert descriptor.metadata["artifact_kind"] == "raster_ref"


def test_load_local_raster_uses_config_allowed_extension(monkeypatch, tmp_path: Path) -> None:
    """
    local_raster_loader should read allowed_extensions and allowed_roots from config.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    data_dir = tmp_path / "rasters"
    data_dir.mkdir()

    raster_path = data_dir / "sample.dat"

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
    ) as dst:
        dst.write(np.ones((1, 2, 2), dtype="uint8"))

    config_file = config_dir / "local_raster_loader.yaml"
    config_file.write_text(
        f"""
default_strict_extensions: true
allowed_extensions:
  - .dat
allowed_roots:
  - {str(data_dir)}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    result = load_local_raster(str(raster_path))

    assert result is not None
    assert result.path == str(raster_path.resolve())
    assert result.metadata["filename"] == "sample.dat"
    assert result.metadata["extension"] == ".dat"


def test_load_local_raster_rejects_path_outside_config_allowed_roots(monkeypatch, tmp_path: Path) -> None:
    """
    local_raster_loader should reject files outside configured allowed_roots.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    config_dir = tmp_path / "config" / "plugins"
    config_dir.mkdir(parents=True)

    allowed_dir = tmp_path / "allowed"
    outside_dir = tmp_path / "outside"
    allowed_dir.mkdir()
    outside_dir.mkdir()

    raster_path = outside_dir / "sample.tif"

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
    ) as dst:
        dst.write(np.ones((1, 2, 2), dtype="uint8"))

    config_file = config_dir / "local_raster_loader.yaml"
    config_file.write_text(
        f"""
default_strict_extensions: true
allowed_extensions:
  - .tif
allowed_roots:
  - {str(allowed_dir)}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEOCHAT_PLUGIN_CONFIG_DIR", str(config_dir))

    with pytest.raises(ValueError, match="allowed root"):
        load_local_raster(str(raster_path))
