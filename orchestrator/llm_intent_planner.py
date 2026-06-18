"""
orchestrator.llm_intent_planner

LLM-based intent planner for natural geospatial queries.

This module converts a user query into a small, validated JSON intent object.
It does not execute plugins. It only plans intent/capabilities/parameters.
"""

from __future__ import annotations

import json
import re
from typing import Any

from orchestrator.llm_client import (
    LLMClientError,
    LLMConfigError,
    get_llm_config,
)

import urllib.error
import urllib.request


class LLMIntentPlannerError(RuntimeError):
    """Raised when LLM intent planning fails."""


DEFAULT_INTENT_SCHEMA: dict[str, Any] = {
    "intent_name": "unknown",
    "language": "unknown",
    "summary": "",
    "preferred_capabilities": [],
    "required_inputs": {
        "raster": False,
        "vector": False,
        "tabular": False,
    },
    "parameters": {},
    "output_expectation": {
        "map_layer": False,
        "table": False,
        "text": True,
    },
    "confidence": 0.0,
    "warnings": [],
}


def plan_intent_with_llm(
    *,
    query: str,
    available_capabilities: list[str] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Convert natural language query into a geospatial intent JSON.

    Args:
        query:
            User query in Persian/English/etc.
        available_capabilities:
            Capability names currently registered in orchestrator.
        model:
            Optional model override. Defaults to LLM_FAST_MODEL.

    Returns:
        A validated dict with stable fields.
    """
    if not isinstance(query, str) or not query.strip():
        raise LLMIntentPlannerError("query must be a non-empty string.")

    capabilities = available_capabilities or []

    raw = _call_llm_for_intent(
        query=query.strip(),
        available_capabilities=capabilities,
        model=model,
    )

    parsed = _extract_json_object(raw)
    intent = _normalize_intent(parsed, query=query, capabilities=capabilities)

    return {
        "ok": True,
        "query": query,
        "intent": intent,
        "model": model or get_llm_config().get("fast_model"),
    }


def _call_llm_for_intent(
    *,
    query: str,
    available_capabilities: list[str],
    model: str | None,
) -> str:
    config = get_llm_config()

    base_url = config.get("base_url")
    api_key = config.get("api_key")

    if not base_url:
        raise LLMConfigError("OPENAI_BASE_URL or LLM_BASE_URL is not configured.")

    if not api_key:
        raise LLMConfigError("OPENAI_API_KEY / AVALAI_API_KEY / LLM_API_KEY is not configured.")

    selected_model = model or config.get("fast_model") or config.get("default_model")
    if not selected_model:
        raise LLMConfigError("No LLM model is configured.")

    endpoint = str(base_url).rstrip("/") + "/chat/completions"

    system_prompt = """
You are a GIS/geospatial intent planner.

Your task:
Convert the user's natural language query into STRICT JSON only.
Do not execute anything.
Do not explain.
Do not wrap in Markdown.
Return exactly one JSON object.

Available capabilities are provided. Prefer them when possible.

Known capability meanings (use EXACT names):
- calculate_spectral_index: compute spectral indices such as NDVI from raster bands.
- calculate_ndvi: compute NDVI directly.
- ndvi_processor: full NDVI processing.
- threshold_raster: create raster masks based on numeric threshold.
- reclassify_raster: reclassify raster values into classes.
- clip_mask_raster: clip/mask raster by geometry.
- calculate_band_math: arbitrary band math on raster.
- calculate_raster_statistics: summarize raster values (min/max/mean).
- calculate_slope_aspect: compute slope/aspect from DEM raster.
- calculate_zonal_statistics: raster statistics per vector zone.
- raster_to_vector: polygonize/vectorize raster masks.
- filter_features: filter vector features by attributes/spatial predicates.
- validate_geometries: validate vector geometries.
- repair_geometries: repair invalid vector geometries.
- buffer_vector_features: create buffers around vector features.
- find_nearest_neighbors: find nearest features.
- calculate_distances: compute distances between features.
- calculate_area_perimeter: calculate area/perimeter of polygons.
- extract_centroids: extract centroids of features.
- calculate_attribute_statistics: statistics over vector attributes.
- dissolve_features: dissolve/merge features by attribute.
- intersect_features: intersect vector layers.
- spatial_join_features: join vector layers spatially.
- transform_vector_crs: reproject vector to another CRS.
- export_vector_geojson: export vector as geojson.

For vector display / "نقاط را نمایش بده" / "عوارض را نشان بده":
prefer filter_features (with no filter) or extract_centroids,
set required_inputs.vector=true, required_inputs.raster=false,
and output_expectation.map_layer=true.

Never include capabilities that are not in available_capabilities.

Output JSON schema:
{
  "intent_name": "vegetation_extraction | raster_vectorization | raster_statistics | vector_display | vector_summary | vector_filter | buffer_analysis | nearest_neighbor | spatial_overlay | geometry_validation | unknown",
  "language": "fa | en | mixed | unknown",
  "summary": "short summary",
  "preferred_capabilities": ["capability_name"],
  "required_inputs": {
    "raster": true,
    "vector": false,
    "tabular": false
  },
  "parameters": {
    "index": "ndvi",
    "threshold": 0.3,
    "vectorize": true
  },
  "output_expectation": {
    "map_layer": true,
    "table": false,
    "text": true
  },
  "confidence": 0.0,
  "warnings": []
}

Rules:
- For Persian vegetation / پوشش گیاهی / NDVI queries, use intent_name vegetation_extraction.
- For vegetation extraction, prefer calculate_spectral_index, threshold_raster, raster_to_vector when vectorization/polygon is requested.
- If user asks polygon/پلیگون/vector/وکتور/تبدیل, set parameters.vectorize=true and output map_layer=true.
- If no threshold is mentioned for NDVI vegetation, use threshold=0.3.
- Only include capabilities that are in available_capabilities.
- If no suitable capability exists, return intent_name unknown with warnings.
"""

    user_prompt = {
        "query": query,
        "available_capabilities": available_capabilities,
    }

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            {
                "role": "user",
                "content": json.dumps(user_prompt, ensure_ascii=False),
            },
        ],
        "temperature": 0,
        "max_tokens": 700,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=int(config.get("timeout_seconds") or 60),
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMClientError(
            f"LLM provider returned HTTP {exc.code}: {body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMClientError(
            f"Could not connect to LLM provider: {exc}"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMClientError(
            f"LLM provider returned invalid JSON: {raw[:500]}"
        ) from exc

    try:
        choices = data.get("choices") or []
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
    except Exception as exc:
        raise LLMClientError(
            f"LLM provider returned unexpected response shape: {raw[:500]}"
        ) from exc

    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    """
    Extract and parse the first JSON object from LLM text.
    Handles accidental ```json fences as a tolerance layer.
    """
    cleaned = str(text or "").strip()

    cleaned = re.sub(r"^```(?:json)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()

    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise LLMIntentPlannerError(
            f"LLM did not return a JSON object. Response preview: {cleaned[:300]}"
        )

    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMIntentPlannerError(
            f"Could not parse LLM intent JSON: {exc}. Response preview: {cleaned[:300]}"
        ) from exc

    if not isinstance(value, dict):
        raise LLMIntentPlannerError("LLM intent response must be a JSON object.")

    return value


def _normalize_intent(
    value: dict[str, Any],
    *,
    query: str,
    capabilities: list[str],
) -> dict[str, Any]:
    """
    Normalize LLM output into stable schema and filter invalid capabilities.
    """
    allowed_capabilities = set(capabilities)

    intent = json.loads(json.dumps(DEFAULT_INTENT_SCHEMA))

    for key in intent.keys():
        if key in value:
            intent[key] = value[key]

    intent["intent_name"] = str(intent.get("intent_name") or "unknown")
    intent["language"] = str(intent.get("language") or "unknown")
    intent["summary"] = str(intent.get("summary") or "")

    preferred = intent.get("preferred_capabilities")
    if not isinstance(preferred, list):
        preferred = []

    filtered_preferred = []
    for item in preferred:
        name = str(item)
        if not allowed_capabilities or name in allowed_capabilities:
            filtered_preferred.append(name)

    intent["preferred_capabilities"] = filtered_preferred

    required_inputs = intent.get("required_inputs")
    if not isinstance(required_inputs, dict):
        required_inputs = {}

    intent["required_inputs"] = {
        "raster": bool(required_inputs.get("raster", False)),
        "vector": bool(required_inputs.get("vector", False)),
        "tabular": bool(required_inputs.get("tabular", False)),
    }

    parameters = intent.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    # Safety defaults for vegetation/NDVI.
    lower_query = query.lower()
    if (
        "ndvi" in lower_query
        or "vegetation" in lower_query
        or "پوشش گیاهی" in query
        or "گیاهی" in query
    ):
        if intent["intent_name"] in {"unknown", ""}:
            intent["intent_name"] = "vegetation_extraction"

        parameters.setdefault("index", "ndvi")
        parameters.setdefault("threshold", 0.3)

        vectorize = any(
            token in lower_query or token in query
            for token in ["polygon", "vector", "پلیگون", "وکتور", "تبدیل"]
        )
        parameters.setdefault("vectorize", vectorize)

    intent["parameters"] = parameters

    output_expectation = intent.get("output_expectation")
    if not isinstance(output_expectation, dict):
        output_expectation = {}

    intent["output_expectation"] = {
        "map_layer": bool(output_expectation.get("map_layer", False)),
        "table": bool(output_expectation.get("table", False)),
        "text": bool(output_expectation.get("text", True)),
    }

    try:
        confidence = float(intent.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    intent["confidence"] = max(0.0, min(1.0, confidence))

    warnings = intent.get("warnings")
    if not isinstance(warnings, list):
        warnings = []

    intent["warnings"] = [str(item) for item in warnings]

    return intent
