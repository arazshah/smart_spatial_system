"""
Tests for the shared local-file path validation used by local_vector_loader
and local_raster_loader (REFACTOR_PLAN.md Phase 6).

Run:
    pytest tests/test_local_path_validation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins._shared.local_path_validation import (  # noqa: E402
    ensure_under_allowed_roots,
    validate_local_path,
)

_DEFAULT_EXTENSIONS = {".geojson", ".json"}


def test_validate_local_path_success(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.geojson"
    file_path.write_text("{}")

    resolved = validate_local_path(
        str(file_path), label="Vector", default_extensions=_DEFAULT_EXTENSIONS
    )

    assert resolved == file_path.resolve()


def test_validate_local_path_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        validate_local_path("", label="Vector", default_extensions=_DEFAULT_EXTENSIONS)


def test_validate_local_path_rejects_missing_file() -> None:
    with pytest.raises(FileNotFoundError, match="Raster file not found"):
        validate_local_path(
            "/tmp/this_file_does_not_exist_12345.tif",
            label="Raster",
            default_extensions=_DEFAULT_EXTENSIONS,
        )


def test_validate_local_path_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file"):
        validate_local_path(
            str(tmp_path), label="Vector", default_extensions=_DEFAULT_EXTENSIONS
        )


def test_validate_local_path_rejects_invalid_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("data")

    with pytest.raises(ValueError, match="Unsupported vector extension"):
        validate_local_path(
            str(file_path), label="Vector", default_extensions=_DEFAULT_EXTENSIONS
        )


def test_validate_local_path_allows_invalid_extension_when_not_strict(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("data")

    resolved = validate_local_path(
        str(file_path),
        label="Vector",
        default_extensions=_DEFAULT_EXTENSIONS,
        strict_extensions=False,
    )

    assert resolved == file_path.resolve()


def test_validate_local_path_rejects_path_outside_allowed_roots(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    other_root = tmp_path / "other"
    other_root.mkdir()

    file_path = other_root / "sample.geojson"
    file_path.write_text("{}")

    with pytest.raises(ValueError, match="allowed root"):
        validate_local_path(
            str(file_path),
            label="Vector",
            default_extensions=_DEFAULT_EXTENSIONS,
            allowed_roots=[str(allowed_root)],
        )


def test_validate_local_path_allows_path_inside_allowed_roots(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    file_path = allowed_root / "sample.geojson"
    file_path.write_text("{}")

    resolved = validate_local_path(
        str(file_path),
        label="Vector",
        default_extensions=_DEFAULT_EXTENSIONS,
        allowed_roots=[str(allowed_root)],
    )

    assert resolved == file_path.resolve()


def test_ensure_under_allowed_roots_noop_when_no_roots_configured(tmp_path: Path) -> None:
    # Should not raise.
    ensure_under_allowed_roots(tmp_path / "anywhere.geojson", None, label="Vector")
    ensure_under_allowed_roots(tmp_path / "anywhere.geojson", [], label="Vector")
