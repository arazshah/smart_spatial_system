"""
Tests for the shared int coercion used by wms_wfs_fetcher.py and
geocoding_resolver.py (REFACTOR_PLAN.md Phase 6, step 1 -- see
docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md).

Run:
    pytest tests/test_numeric_validation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._shared.numeric_validation import to_int  # noqa: E402


def test_to_int_accepts_int() -> None:
    assert to_int(5, "limit") == 5


def test_to_int_coerces_string() -> None:
    assert to_int("10", "limit") == 10


def test_to_int_coerces_float() -> None:
    assert to_int(3.9, "limit") == 3


def test_to_int_rejects_bool_true() -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        to_int(True, "limit")


def test_to_int_rejects_bool_false() -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        to_int(False, "limit")


def test_to_int_rejects_non_numeric_string() -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        to_int("not-a-number", "limit")


def test_to_int_rejects_none() -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        to_int(None, "limit")


def test_to_int_error_names_the_field() -> None:
    with pytest.raises(ValueError, match="timeout must be an integer"):
        to_int("nope", "timeout")
