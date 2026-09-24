"""
Regression test for case-study bug 004.

raster_to_vector._extract_transform ignored a dict transform (or an
affine_transform key), silently falling back to the default identity-like
transform [1, 0, 0, 0, -1, 0] -- silently wrong georeferencing.

Run:
    pytest tests/test_bug_004_raster_to_vector_dict_transform.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.raster_to_vector import raster_to_vector  # noqa: E402


def test_raster_to_vector_uses_dict_transform_from_metadata():
    """
    A dict transform in metadata must be honored, not silently discarded.
    """
    raster = {
        "data": [[1, 1], [1, 1]],
        "metadata": {
            "transform": {"a": 10, "b": 0, "c": 300000, "d": 0, "e": -10, "f": 3330000},
        },
    }

    result = raster_to_vector(raster, include_values=[1], mode="cells")

    first_feature = result["features"][0]
    first_vertex = first_feature["geometry"]["coordinates"][0][0]

    # First cell (row=0, col=0) corner must reflect the affine transform's
    # origin (c, f), not the [1, 0, 0, 0, -1, 0] default.
    assert first_vertex == [300000.0, 3330000.0]


def test_raster_to_vector_warns_when_no_transform_present_at_all():
    """
    When there is genuinely no transform anywhere, fall back to the default
    transform but say so via a warning in output metadata.
    """
    raster = {"data": [[1, 1], [1, 1]], "metadata": {}}

    result = raster_to_vector(raster, include_values=[1], mode="cells")

    assert result["metadata"]["transform_source"] == "default_transform"
    assert result["metadata"].get("warning")
    assert "default" in result["metadata"]["warning"].lower()
