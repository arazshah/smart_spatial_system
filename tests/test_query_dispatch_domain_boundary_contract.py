"""
Domain boundary regression tests for generic query dispatch modules.

The dispatch layer is generic plumbing. It may receive injected handlers, but
its public parameter/local names must not encode a specific business domain such
as real_estate.
"""

from __future__ import annotations

from pathlib import Path


DISPATCH_PATHS = [
    Path("smart_spatial_system/application/services/query_execution/direct_query_dispatch.py"),
    Path("smart_spatial_system/application/services/query_execution/natural_query_dispatch.py"),
    Path("smart_spatial_system/application/services/query_execution/natural_query_execution.py"),
]


def test_query_dispatch_modules_use_domain_agnostic_handler_names() -> None:
    forbidden_tokens = [
        "missing_real_estate_inputs_handler",
        "real_estate_ranking_handler",
        "missing_real_estate_inputs_response",
        "real_estate_ranking_response",
    ]

    for path in DISPATCH_PATHS:
        source = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in source
