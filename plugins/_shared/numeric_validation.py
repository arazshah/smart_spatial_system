"""
plugins._shared.numeric_validation

Shared integer coercion for source plugins.

REFACTOR_PLAN.md Phase 6 (source abstraction) -- see
docs/PHASE6_SOURCE_ABSTRACTION_PLAN.md step 1. wms_wfs_fetcher.py and
geocoding_resolver.py each carried a byte-identical private `_to_int`
helper (reject bool, coerce via int(), raise a field-named ValueError on
failure). Both now call this shared implementation.

postgis_connector.py's own int handling (`_validate_limit`/
`_to_int_or_none`) is deliberately NOT unified with this: it is a
different, looser convention (accepts bool as an int, uses a plain
isinstance check instead of coercion) with different call sites - not
duplication, so left as-is per the plan's own distinction between real
duplication and legitimately-different domain logic.
"""

from __future__ import annotations

from typing import Any


def to_int(value: Any, field_name: str) -> int:
    """
    Convert value to int or raise a clear, field-named ValueError.

    Rejects bool explicitly (bool is a subclass of int in Python, and a
    caller passing True/False for a numeric field is almost always a
    mistake, not an intentional 1/0).
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc
