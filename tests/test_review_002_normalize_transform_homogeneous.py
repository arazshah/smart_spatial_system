"""
Regression test for a PR #58 review finding on bugs 004/006's fix.

raster_to_vector._extract_transform originally accepted a 9-element
homogeneous affine matrix ([a, b, c, d, e, f, 0, 0, 1], as some callers
store it) by taking `len(transform) >= 6` and using the first six
coefficients. Routing raster_to_vector's transform handling through
raster_clip_mask._normalize_transform (to fix bugs 004/007) tightened that
to `len(transform) == 6`, so a previously-valid 9-element transform now
raises instead of vectorizing.

Run:
    pytest tests/test_review_002_normalize_transform_homogeneous.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.raster_clip_mask import _normalize_transform  # noqa: E402


def test_normalize_transform_accepts_nine_element_homogeneous_matrix() -> None:
    homogeneous = [2.0, 0.0, 100.0, 0.0, -2.0, 200.0, 0.0, 0.0, 1.0]

    normalized = _normalize_transform(homogeneous)

    assert normalized == [2.0, 0.0, 100.0, 0.0, -2.0, 200.0]


def test_normalize_transform_still_accepts_plain_six_element_list() -> None:
    six = [1.0, 0.0, 0.0, 0.0, -1.0, 0.0]

    assert _normalize_transform(six) == six
