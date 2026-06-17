"""
orchestrator.capability_scoring

Keyword-scoring router for natural-language queries.

This module is the first step from exact capability resolution toward
intelligent routing.

Current routing layers:

    CapabilityRegistry
        discovers capability descriptors from plugins

    RegistryBackedCapabilityRouter
        resolves exact capability names

    KeywordScoringCapabilityRouter
        scores/ranks capabilities against a natural-language query

No LLM is used here.
LLM will be added later as a gated fallback when confidence is low or ambiguous.
"""

from __future__ import annotations

import re
from typing import Any

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.models import CapabilityBinding, ScoredCapability


DEFAULT_DOMAIN_HINTS: dict[str, list[str]] = {
    "calculate_spectral_index": [
        "ndvi",
        "ndwi",
        "ndbi",
        "savi",
        "evi",
        "شاخص",
        "شاخص طیفی",
        "پوشش گیاهی",
        "آب",
        "ماهواره",
        "ماهواره‌ای",
    ],
    "threshold_raster": [
        "threshold",
        "آستانه",
        "ماسک",
        "بیشتر از",
        "بالاتر از",
        "کمتر از",
        "پایین‌تر از",
        "greater than",
        "less than",
        ">",
        "<",
        ">=",
        "<=",
    ],
    "raster_to_vector": [
        "polygon",
        "polygons",
        "vector",
        "vectorize",
        "پلیگون",
        "وکتور",
        "بردار",
        "تبدیل کن",
        "تبدیل",
        "استخراج کن",
    ],
}


CONVERSION_TO_VECTOR_CUES = [
    "به پلیگون",
    "پلیگون تبدیل",
    "به وکتور",
    "وکتور تبدیل",
    "به بردار",
    "بردار تبدیل",
    "رستر را به پلیگون",
    "ماسک رستر را به پلیگون",
    "ماسک را به پلیگون",
    "تبدیل به پلیگون",
    "تبدیل به وکتور",
    "to polygon",
    "to vector",
    "convert to polygon",
    "convert to vector",
    "vectorize",
]


THRESHOLD_CUES = [
    "threshold",
    "آستانه",
    "بیشتر از",
    "بالاتر از",
    "کمتر از",
    "پایین تر از",
    "پایین‌تر از",
    "greater than",
    "less than",
    ">=",
    "<=",
    ">",
    "<",
]


class KeywordScoringCapabilityRouter:
    """
    Scores and ranks capabilities for a natural-language query.

    This class does not replace exact resolution.
    It augments the registry-backed router with query scoring.
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        plugin_module_names: list[str] | None = None,
        domain_hints: dict[str, list[str]] | None = None,
    ) -> None:
        self.registry = registry or CapabilityRegistry.from_plugin_modules(plugin_module_names)
        self.domain_hints = domain_hints or DEFAULT_DOMAIN_HINTS

    def resolve(self, capability_name: str) -> CapabilityBinding:
        """
        Exact capability resolution.
        """
        return self.registry.resolve(capability_name)

    def registered_capability_names(self) -> list[str]:
        """
        Return sorted registered capability names.
        """
        return self.registry.registered_capability_names()

    def descriptor_for(self, capability_name: str) -> Any:
        """
        Return descriptor for capability.
        """
        return self.registry.descriptor_for(capability_name)

    def score_query(
        self,
        query: str,
        *,
        expected_output_kind: str | None = None,
        min_score: float = 0.0,
        top_k: int | None = None,
    ) -> list[ScoredCapability]:
        """
        Score registered capabilities against a natural-language query.

        Args:
            query:
                User natural-language query.
            expected_output_kind:
                Optional output kind filter such as raster/vector.
            min_score:
                Minimum score to keep.
            top_k:
                Optional maximum number of returned candidates.

        Returns:
            Sorted list of ScoredCapability, highest score first.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")

        normalized_query = _normalize_text(query)

        scored: list[ScoredCapability] = []

        for capability_name in self.registry.registered_capability_names():
            binding = self.registry.resolve(capability_name)

            if expected_output_kind is not None and binding.output_kind != expected_output_kind:
                continue

            descriptor = self.registry.descriptor_for(capability_name)

            candidate = self._score_capability(
                normalized_query=normalized_query,
                binding=binding,
                descriptor=descriptor,
            )

            if candidate.score >= min_score:
                scored.append(candidate)

        scored.sort(
            key=lambda item: (
                item.score,
                len(item.matched_terms),
                item.capability_name,
            ),
            reverse=True,
        )

        if top_k is not None:
            if top_k <= 0:
                raise ValueError("top_k must be a positive integer or None.")
            return scored[:top_k]

        return scored

    def select_relevant(
        self,
        query: str,
        *,
        min_score: float = 0.2,
        top_k: int | None = None,
    ) -> list[ScoredCapability]:
        """
        Return relevant capabilities for a query.
        """
        return self.score_query(
            query,
            min_score=min_score,
            top_k=top_k,
        )

    def best_match(
        self,
        query: str,
        *,
        expected_output_kind: str | None = None,
        min_score: float = 0.2,
    ) -> ScoredCapability:
        """
        Return best matching capability.

        Raises:
            ValueError if no candidate reaches min_score.
        """
        candidates = self.score_query(
            query,
            expected_output_kind=expected_output_kind,
            min_score=min_score,
            top_k=1,
        )

        if not candidates:
            raise ValueError("No capability matched the query with sufficient score.")

        return candidates[0]

    def _score_capability(
        self,
        *,
        normalized_query: str,
        binding: CapabilityBinding,
        descriptor: Any,
    ) -> ScoredCapability:
        """
        Score one capability.
        """
        matched_terms: list[str] = []
        reasons: list[str] = []
        score = 0.0

        capability_name = binding.name
        plugin_id = binding.plugin_id

        capability_tokens = _name_tokens(capability_name)
        operation = str((getattr(descriptor, "metadata", {}) or {}).get("operation", ""))
        operation_tokens = _name_tokens(operation)

        # 1. Direct capability name / operation tokens.
        for token in capability_tokens:
            if token and token in normalized_query:
                score += 0.12
                _append_unique(matched_terms, token)
                _append_unique(reasons, "capability_name_token")

        for token in operation_tokens:
            if token and token in normalized_query:
                score += 0.10
                _append_unique(matched_terms, token)
                _append_unique(reasons, "operation_token")

        # 2. Descriptor keywords.
        descriptor_keywords = list(getattr(descriptor, "keywords", []) or [])
        binding_keywords = binding.keywords or []

        for keyword in descriptor_keywords + binding_keywords:
            normalized_keyword = _normalize_text(str(keyword))
            if not normalized_keyword:
                continue

            if normalized_keyword in normalized_query:
                score += _keyword_weight(normalized_keyword)
                _append_unique(matched_terms, normalized_keyword)
                _append_unique(reasons, "descriptor_keyword")

        # 3. Domain hints for known capability names.
        for hint in self.domain_hints.get(capability_name, []):
            normalized_hint = _normalize_text(str(hint))
            if not normalized_hint:
                continue

            if normalized_hint in normalized_query:
                score += _hint_weight(normalized_hint)
                _append_unique(matched_terms, normalized_hint)
                _append_unique(reasons, "domain_hint")

        # 4. Description weak signal.
        description = _normalize_text(str(getattr(descriptor, "description", "") or ""))
        if description:
            description_tokens = set(_name_tokens(description))
            query_tokens = set(_query_tokens(normalized_query))
            overlap = description_tokens.intersection(query_tokens)
            if overlap:
                weak = min(0.10, 0.02 * len(overlap))
                score += weak
                _append_unique(reasons, "description_overlap")
                for token in sorted(overlap)[:5]:
                    _append_unique(matched_terms, token)

        # 5. Output-kind hint.
        if binding.output_kind and binding.output_kind in normalized_query:
            score += 0.05
            _append_unique(matched_terms, binding.output_kind)
            _append_unique(reasons, "output_kind")

        # 6. Intent-level routing corrections.
        #
        # Some words such as "mask/ماسک" may appear in conversion queries:
        #     "ماسک رستر را به پلیگون تبدیل کن"
        #
        # In that case, the user's dominant intent is raster_to_vector, not
        # threshold_raster. So we add a strong conversion boost and reduce
        # threshold score when no real threshold cue exists.
        has_conversion_to_vector_cue = _contains_any(
            normalized_query,
            CONVERSION_TO_VECTOR_CUES,
        )
        has_threshold_cue = _contains_any(
            normalized_query,
            THRESHOLD_CUES,
        )

        if capability_name == "raster_to_vector" and has_conversion_to_vector_cue:
            score += 0.45
            _append_unique(reasons, "conversion_to_vector_intent")
            for cue in CONVERSION_TO_VECTOR_CUES:
                normalized_cue = _normalize_text(cue)
                if normalized_cue in normalized_query:
                    _append_unique(matched_terms, normalized_cue)

        if capability_name == "threshold_raster":
            if has_threshold_cue:
                score += 0.20
                _append_unique(reasons, "threshold_intent")
            elif has_conversion_to_vector_cue:
                score = max(0.0, score - 0.35)
                _append_unique(reasons, "penalty_no_threshold_cue_in_conversion_query")

        # Keep score bounded.
        #
        # Important:
        # Keyword scoring should not produce perfect confidence (1.0).
        # A score of 1.0 should be reserved for stronger future evidence,
        # e.g. explicit user selection, validated semantic match, or LLM-confirmed
        # routing. This also lets RouterDecisionConfig(high_threshold=1.0)
        # force a non-HIGH decision during tests/policy simulation.
        final_score = min(0.95, round(score, 4))

        return ScoredCapability(
            capability_name=capability_name,
            plugin_id=plugin_id,
            output_kind=binding.output_kind,
            score=final_score,
            matched_terms=matched_terms,
            reasons=reasons,
        )


def _normalize_text(value: str) -> str:
    """
    Normalize query/keyword text.

    Keeps Persian and English text.
    Lowercases English text and normalizes common Arabic/Persian variants.
    """
    text = value.strip().lower()

    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "‌": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _name_tokens(value: str) -> list[str]:
    """
    Convert snake_case or free text into useful tokens.
    """
    normalized = _normalize_text(value.replace("_", " "))
    return [
        token
        for token in re.split(r"[\s,\-/()]+", normalized)
        if token
    ]


def _query_tokens(value: str) -> list[str]:
    """
    Extract rough query tokens.
    """
    normalized = _normalize_text(value)
    return [
        token
        for token in re.split(r"[\s,\-/()]+", normalized)
        if token
    ]


def _keyword_weight(keyword: str) -> float:
    """
    Weight descriptor keyword matches.
    """
    if len(keyword) <= 2:
        return 0.06
    if " " in keyword:
        return 0.28
    return 0.20


def _hint_weight(hint: str) -> float:
    """
    Weight domain hint matches.
    """
    if hint in {">", "<", ">=", "<="}:
        return 0.18
    if " " in hint:
        return 0.30
    return 0.22


def _contains_any(text: str, cues: list[str]) -> bool:
    """
    Return True if any normalized cue appears in text.
    """
    normalized_text = _normalize_text(text)

    for cue in cues:
        normalized_cue = _normalize_text(cue)
        if normalized_cue and normalized_cue in normalized_text:
            return True

    return False


def _append_unique(items: list[str], value: str) -> None:
    """
    Append only if not already present.
    """
    if value not in items:
        items.append(value)


# ---------------------------------------------------------------------------
# ScoredCapability mapping compatibility
# ---------------------------------------------------------------------------
# Some orchestration layers consume routing evidence as dict-like objects:
#     item["score"]
#     item.get("capability_name")
# This compatibility layer keeps ScoredCapability usable both as a dataclass
# object and as mapping-like routing evidence.

def _scored_capability_mapping_payload(self):
    from dataclasses import asdict, is_dataclass

    if is_dataclass(self):
        payload = asdict(self)
    else:
        payload = dict(getattr(self, "__dict__", {}) or {})

    # Copy direct attributes if they exist.
    for key in (
        "score",
        "capability_name",
        "name",
        "plugin_id",
        "plugin_name",
        "plugin",
        "output_kind",
        "matched_terms",
        "reasons",
    ):
        if key not in payload:
            try:
                payload[key] = getattr(self, key)
            except Exception:
                pass

    capability = payload.get("capability")

    # Extract capability_name from nested capability object/dict.
    if not payload.get("capability_name") and capability is not None:
        if isinstance(capability, dict):
            payload["capability_name"] = (
                capability.get("capability_name")
                or capability.get("name")
                or capability.get("id")
            )
        else:
            payload["capability_name"] = (
                getattr(capability, "capability_name", None)
                or getattr(capability, "name", None)
                or getattr(capability, "id", None)
            )

    # Extract plugin_id from nested capability object/dict.
    if not payload.get("plugin_id") and capability is not None:
        if isinstance(capability, dict):
            payload["plugin_id"] = (
                capability.get("plugin_id")
                or capability.get("plugin_name")
                or capability.get("plugin")
            )
        else:
            payload["plugin_id"] = (
                getattr(capability, "plugin_id", None)
                or getattr(capability, "plugin_name", None)
                or getattr(capability, "plugin", None)
            )

    if payload.get("reasons") is None:
        payload["reasons"] = []

    if payload.get("matched_terms") is None:
        payload["matched_terms"] = []

    return payload


def _scored_capability_get(self, key, default=None):
    return self._mapping_payload().get(key, default)


def _scored_capability_getitem(self, key):
    payload = self._mapping_payload()

    if key not in payload:
        raise KeyError(key)

    return payload[key]


def _scored_capability_contains(self, key):
    return key in self._mapping_payload()


def _scored_capability_keys(self):
    return self._mapping_payload().keys()


def _scored_capability_items(self):
    return self._mapping_payload().items()


def _scored_capability_values(self):
    return self._mapping_payload().values()


try:
    ScoredCapability._mapping_payload = _scored_capability_mapping_payload
    ScoredCapability.get = _scored_capability_get
    ScoredCapability.__getitem__ = _scored_capability_getitem
    ScoredCapability.__contains__ = _scored_capability_contains
    ScoredCapability.keys = _scored_capability_keys
    ScoredCapability.items = _scored_capability_items
    ScoredCapability.values = _scored_capability_values
except NameError:
    # If ScoredCapability is renamed or removed, fail silently at import time.
    # Tests will catch the missing compatibility.
    pass
