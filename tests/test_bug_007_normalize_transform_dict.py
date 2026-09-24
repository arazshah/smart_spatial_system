"""
Regression test for case-study bug 007.

_normalize_transform in plugins/raster_clip_mask.py rejected a complete
{a..f} affine dict because `transform.get("e", -abs(float(transform.get("pixel_height"))))`
eagerly evaluates the default expression (float(None) -> TypeError) even
when "e" is present in the dict.

Run:
    pytest tests/test_bug_007_normalize_transform_dict.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.raster_clip_mask import _normalize_transform  # noqa: E402


def test_normalize_transform_accepts_complete_affine_dict():
    """
    A complete a..f affine dict must not raise, even though it has no
    'pixel_height'/'pixel_width'/'origin_x'/'origin_y' aliases.
    """
    transform = {"a": 10, "b": 0, "c": 300000, "d": 0, "e": -10, "f": 3330000}

    result = _normalize_transform(transform)

    assert result == [10.0, 0.0, 300000.0, 0.0, -10.0, 3330000.0]
