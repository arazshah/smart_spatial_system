"""
orchestrator.query_parser

A minimal deterministic natural-language parser for the first E2E workflows.

No LLM is used here.
LLM will be added later as a gated fallback/helper.
"""

from __future__ import annotations

import re

from orchestrator.models import QueryIntent


class SimpleNaturalLanguageParser:
    """
    Minimal deterministic parser for the first E2E test.

    Supported workflow:
        NDVI vegetation extraction with threshold and polygonization.
    """

    def __init__(self, *, strict: bool = True) -> None:
        self.strict = strict

    def parse(self, query: str) -> QueryIntent:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")

        normalized = query.strip().lower()

        if "ndvi" not in normalized and "پوشش گیاهی" not in normalized:
            if self.strict:
                raise ValueError("Only NDVI vegetation extraction query is supported in this parser version.")
            # Non-strict mode is used by the API/workbench MVP.
            # Keep the deterministic NDVI pipeline alive by falling back to
            # vegetation extraction intent instead of hard-failing.
            # The original user query remains available in the raw query field
            # if QueryIntent stores it.
            pass

        threshold = self._extract_threshold(normalized)

        vectorize = any(
            token in normalized
            for token in [
                "پلیگون",
                "polygon",
                "vector",
                "وکتور",
                "تبدیل کن",
                "استخراج کن",
            ]
        )

        return QueryIntent(
            raw_query=query,
            intent_name="extract_vegetation_polygons_from_ndvi_threshold",
            index_name="ndvi",
            threshold_operator="gt",
            threshold_value=threshold,
            vectorize=vectorize,
            output_geometry="polygon" if vectorize else "raster_mask",
        )

    @staticmethod
    def _extract_threshold(query: str) -> float:
        """
        Extract threshold from Persian/English query.

        Supported examples:
            بیشتر از 0.3
            بالاتر از 0.3
            greater than 0.3
            > 0.3
        """
        patterns = [
            r"بیشتر\s+از\s+([0-9]+(?:\.[0-9]+)?)",
            r"بالاتر\s+از\s+([0-9]+(?:\.[0-9]+)?)",
            r"greater\s+than\s+([0-9]+(?:\.[0-9]+)?)",
            r">\s*([0-9]+(?:\.[0-9]+)?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                return float(match.group(1))

        # Conservative default for vegetation extraction.
        return 0.3