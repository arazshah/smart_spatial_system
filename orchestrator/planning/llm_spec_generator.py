"""
LLM QuerySpec Generator.

LLM converts natural language into declarative QuerySpec only.
It must not execute plugins or code.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from orchestrator.planning.input_data_extent import (
    InputDataExtent,
    render_input_data_facts,
)
from orchestrator.planning.input_layers import (
    InputLayer,
    render_input_layer_facts,
    unknown_input_refs,
)
from orchestrator.planning.op_catalog import (
    get_op,
    is_supported,
    list_pending_ops,
    list_supported_ops,
)
from orchestrator.planning.op_param_shapes import op_param_shape_reference
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec


@dataclass(frozen=True)
class SpecGenerationAttempt:
    """
    One LLM round-trip inside LLMQuerySpecGenerator.generate().

    number:
        1 for the first attempt, 2 for the first repair, ...
    plan:
        The QuerySpec JSON the LLM returned, as parsed and before any
        normalization - None if the response contained no parseable JSON.
    raw_response:
        The LLM's response text, verbatim.
    error:
        Why this attempt's plan was rejected, or None if it was accepted.
    """

    number: int
    plan: dict[str, Any] | None
    raw_response: str
    error: str | None = None


class LLMSpecGenerationError(ValueError):
    """
    Raised when an LLM plan can't be turned into a valid QuerySpec.

    When raised from generate() after the LLM responded, it also carries
    the evidence needed to audit the failure without re-running it:

    plan:
        The rejected QuerySpec JSON of the LAST attempt (as returned by the
        LLM, before normalization), or None if no JSON could be parsed.
    raw_response:
        The last attempt's LLM response text.
    attempts:
        Every attempt, oldest first (SpecGenerationAttempt) - more than one
        when repair attempts were made.
    """

    def __init__(
        self,
        message: str = "",
        *,
        plan: dict[str, Any] | None = None,
        raw_response: str | None = None,
        attempts: tuple[SpecGenerationAttempt, ...] = (),
    ) -> None:
        super().__init__(message)
        self.plan = plan
        self.raw_response = raw_response
        self.attempts = tuple(attempts)


class LLMClient(Protocol):
    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        ...


class StaticLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_messages: list[dict[str, str]] | None = None
        self.last_kwargs: dict[str, Any] | None = None

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.last_messages = messages
        self.last_kwargs = dict(kwargs)
        return self.response


class OpenAICompatibleLLMClient:
    """
    OpenAI-compatible client.

    For AvalAI:
        export LLM_BASE_URL="https://api.avalai.ir/v1"
        export LLM_API_KEY="..."
        export LLM_MODEL="gpt-4o-mini"
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("AVALAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.base_url = (
            base_url
            or os.getenv("LLM_BASE_URL")
            or "https://api.avalai.ir/v1"
        ).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL") or "gpt-4o-mini"
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if not self.api_key:
            raise LLMSpecGenerationError(
                "LLM API key is missing. Set LLM_API_KEY / AVALAI_API_KEY / OPENAI_API_KEY."
            )

        payload: dict[str, Any] = {
            "model": kwargs.get("model") or self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.1),
            "response_format": kwargs.get("response_format", {"type": "json_object"}),
        }

        if kwargs.get("max_tokens") is not None:
            payload["max_tokens"] = kwargs["max_tokens"]

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMSpecGenerationError(f"LLM HTTP error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMSpecGenerationError(f"LLM request failed: {exc}") from exc

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMSpecGenerationError(f"Invalid LLM response: {body[:500]}") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise LLMSpecGenerationError("LLM response is empty.")

    s = text.strip()

    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        s,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            raise LLMSpecGenerationError(f"Invalid fenced JSON: {exc}") from exc

    start = s.find("{")
    if start < 0:
        raise LLMSpecGenerationError("No JSON object found.")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start : i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError as exc:
                    raise LLMSpecGenerationError(f"Invalid JSON object: {exc}") from exc

    raise LLMSpecGenerationError("Could not extract complete JSON object.")


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LLMSpecGenerationError(f"{label} must be an object.")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LLMSpecGenerationError(f"{label} must be a list.")
    return value


def _coerce_list_of_pairs_to_dict(value: Any) -> dict[str, Any] | None:
    """
    Coerce common LLM mistakes into an object.

    Accepted examples:
      [{"source": "a"}, {"target": "b"}] -> {"source": "a", "target": "b"}
      [{"key": "source", "value": "a"}] -> {"source": "a"}
      [{"name": "k", "value": 1}] -> {"k": 1}
      [["source", "a"], ["target", "b"]] -> {"source": "a", "target": "b"}

    Ambiguous values return None.
    """
    if isinstance(value, dict):
        return dict(value)

    if value in (None, ""):
        return {}

    if not isinstance(value, list):
        return None

    result: dict[str, Any] = {}

    for index, item in enumerate(value):
        if isinstance(item, dict):
            # Common explicit key/value shapes.
            explicit_key = (
                item.get("key")
                or item.get("name")
                or item.get("role")
                or item.get("input")
                or item.get("param")
            )

            if explicit_key is not None:
                explicit_value = (
                    item.get("value")
                    if "value" in item
                    else item.get("source")
                    if "source" in item
                    else item.get("ref")
                    if "ref" in item
                    else item.get("target")
                    if "target" in item
                    else None
                )
                result[str(explicit_key)] = explicit_value
                continue

            # Single-key object: {"source": "layer"}
            if len(item) == 1:
                k, v = next(iter(item.items()))
                result[str(k)] = v
                continue

            # Multi-key object where all keys look like direct mapping.
            # Example: {"source": "a", "target": "b"}
            if item:
                for k, v in item.items():
                    result[str(k)] = v
                continue

            continue

        if isinstance(item, (list, tuple)) and len(item) == 2:
            result[str(item[0])] = item[1]
            continue

        # Plain string arrays are ambiguous for strict QuerySpec inputs.
        return None

    return result


_QUERY_DATABASE_PARAM_KEYS = {
    "source_type",
    "mode",
    "schema",
    "table",
    "columns",
    "geom_col",
    "geom_alias",
    "where",
    "limit",
    "output_srid",
    "profile",
    "dsn",
    "host",
    "port",
    "database",
    "user",
    "password",
    "connect_timeout",
    "metadata",
}


_VECTOR_INPUT_ALIAS_OPS = {
    "top_n",
    "rank",
    "rank_features",
    "sort_limit",
    "display_vector",
    "summarize_vector",
    "export_geojson",
    "build_report",
}


def _is_database_entity(item: Any) -> bool:
    if not isinstance(item, dict):
        return False

    kind = str(item.get("kind") or "").strip().lower()
    binding = item.get("binding")

    if kind not in {"database", "postgis", "table", "layer"}:
        return False

    if not isinstance(binding, dict):
        return False

    return bool(binding.get("schema") and binding.get("table") and binding.get("geom_col"))


def _query_database_params_from_binding(binding: dict[str, Any]) -> dict[str, Any]:
    params = {
        k: v
        for k, v in binding.items()
        if k in _QUERY_DATABASE_PARAM_KEYS and v not in (None, "")
    }

    params.setdefault("source_type", "postgis")
    params.setdefault("mode", "select_table")
    params.setdefault("geom_alias", "geom")

    if "limit" not in params:
        params["limit"] = 5000

    return params


def _repair_query_database_operation_shape(op_item: dict[str, Any]) -> dict[str, Any]:
    """
    Repair common LLM mistake:
      query_database.inputs contains PostGIS params and params is empty.

    Correct shape:
      query_database.inputs = {}
      query_database.params = {...}
    """
    op_copy = dict(op_item)

    if str(op_copy.get("op") or "") not in {"query_database", "load_postgis_layer"}:
        return op_copy

    inputs = op_copy.get("inputs")
    params = op_copy.get("params")

    if not isinstance(inputs, dict):
        return op_copy

    if not isinstance(params, dict):
        params = {}

    movable = {
        k: v
        for k, v in inputs.items()
        if k in _QUERY_DATABASE_PARAM_KEYS
    }

    if not movable:
        return op_copy

    remaining_inputs = {
        k: v
        for k, v in inputs.items()
        if k not in _QUERY_DATABASE_PARAM_KEYS
    }

    merged_params = dict(params)
    for k, v in movable.items():
        merged_params.setdefault(k, v)

    merged_params.setdefault("source_type", "postgis")
    merged_params.setdefault("mode", "select_table")
    merged_params.setdefault("geom_alias", "geom")

    if "limit" not in merged_params:
        merged_params["limit"] = 5000

    op_copy["inputs"] = remaining_inputs
    op_copy["params"] = merged_params

    return op_copy


def _repair_vector_input_aliases(op_item: dict[str, Any]) -> dict[str, Any]:
    """
    Repair common LLM mistake:
      top_n/display_vector/summarize_vector inputs={"source": "..."}
    when the catalog expects:
      inputs={"vector": "..."}
    """
    op_copy = dict(op_item)
    op_name = str(op_copy.get("op") or "")

    if op_name not in _VECTOR_INPUT_ALIAS_OPS:
        return op_copy

    inputs = op_copy.get("inputs")
    if not isinstance(inputs, dict):
        return op_copy

    if "vector" not in inputs:
        for alias in ("source", "features", "input", "layer"):
            if alias in inputs and inputs.get(alias) not in (None, ""):
                new_inputs = dict(inputs)
                new_inputs["vector"] = new_inputs.get(alias)
                op_copy["inputs"] = new_inputs
                break

    return op_copy


def _existing_operation_outputs(operations: list[Any]) -> set[str]:
    outputs: set[str] = set()
    for op in operations:
        if isinstance(op, dict) and op.get("output"):
            outputs.add(str(op.get("output")))
    return outputs


def _inject_query_database_ops_for_database_entities(data: dict[str, Any]) -> dict[str, Any]:
    """
    If LLM defines database entities but forgets to create query_database
    operations for them, inject deterministic load operations.

    Example:
      entities:
        ref=metro_station, kind=database, binding={schema, table, geom_col, ...}
      operations:
        spatial_nearest inputs={source: metro_station, target: shopping_center}

    Becomes:
      query_database output=metro_station
      query_database output=shopping_center
      spatial_nearest ...
    """
    entities = data.get("entities")
    operations = data.get("operations")

    if not isinstance(entities, list) or not isinstance(operations, list):
        return data

    existing_outputs = _existing_operation_outputs(operations)

    injected: list[dict[str, Any]] = []

    for entity in entities:
        if not _is_database_entity(entity):
            continue

        ref = str(entity.get("ref") or "").strip()
        if not ref:
            continue

        if ref in existing_outputs:
            continue

        binding = entity.get("binding")
        if not isinstance(binding, dict):
            continue

        params = _query_database_params_from_binding(binding)

        injected.append(
            {
                "op": "query_database",
                "inputs": {},
                "params": params,
                "output": ref,
            }
        )

    if not injected:
        return data

    new_data = dict(data)
    new_data["operations"] = injected + operations

    metadata = dict(new_data.get("metadata") or {})
    repairs = list((metadata.get("pre_normalization_repairs") or []))
    repairs.append("injected query_database operations for database entities")
    metadata["pre_normalization_repairs"] = repairs
    new_data["metadata"] = metadata

    return new_data


def _compact_ref(value: Any) -> str:
    s = str(value or "").strip().lower()
    s = s.replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in s if ch.isalnum() or ch == "_")


def _semantic_concept_aliases(concept: str) -> set[str]:
    c = _compact_ref(concept)
    aliases = {c}

    if c:
        aliases.add(c + "s")

    # Common plural/alias variants produced by LLMs.
    if c == "metro_station":
        aliases.update({"metro_stations", "metro", "subway_station", "subway_stations"})
    elif c == "shopping_center":
        aliases.update({
            "shopping_centers",
            "shopping_centre",
            "shopping_centres",
            "mall",
            "malls",
            "market",
            "markets",
        })
    elif c == "park":
        aliases.update({"parks"})
    elif c == "hospital":
        aliases.update({"hospitals"})
    elif c == "school":
        aliases.update({"schools"})

    return aliases


def _ref_matches_concept(ref: Any, concept: str) -> bool:
    r = _compact_ref(ref)
    if not r:
        return False

    aliases = _semantic_concept_aliases(concept)
    if r in aliases:
        return True

    if any(alias and alias in r for alias in aliases):
        return True

    # Fallback: all concept tokens appear in output ref.
    tokens = [t for t in _compact_ref(concept).split("_") if t]
    return bool(tokens) and all(t in r for t in tokens)


def _semantic_layer_params_from_context(
    context: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(context, dict):
        return []

    semantic_context = context.get("semantic_planning_context")
    if not isinstance(semantic_context, dict):
        return []

    layers_by_concept = semantic_context.get("semantic_layers")
    if not isinstance(layers_by_concept, dict):
        return []

    result: list[tuple[str, dict[str, Any]]] = []

    for concept, layers in layers_by_concept.items():
        if not isinstance(layers, list):
            continue

        for layer in layers:
            if not isinstance(layer, dict):
                continue

            params = layer.get("params")
            if not isinstance(params, dict):
                continue

            clean_params = {
                k: v
                for k, v in params.items()
                if k in _QUERY_DATABASE_PARAM_KEYS and v not in (None, "")
            }

            if clean_params:
                result.append((str(concept), clean_params))

    return result


def _best_semantic_params_for_output(
    output_ref: Any,
    context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    candidates = _semantic_layer_params_from_context(context)
    if not candidates:
        return None

    # First, match by concept/output alias.
    for concept, params in candidates:
        if _ref_matches_concept(output_ref, concept):
            return dict(params)

    return None


def _query_database_params_complete(params: Any) -> bool:
    if not isinstance(params, dict):
        return False

    return bool(
        params.get("schema")
        and params.get("table")
        and params.get("geom_col")
    )


def _merge_query_database_params(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge semantic candidate params with LLM params.

    Semantic params provide schema/table/geom_col/columns/where.
    LLM params may override non-empty values, but cannot remove required fields.
    """
    merged = dict(base)

    for k, v in override.items():
        if v not in (None, ""):
            merged[k] = v

    merged.setdefault("source_type", "postgis")
    merged.setdefault("mode", "select_table")
    merged.setdefault("geom_alias", "geom")
    merged.setdefault("limit", 5000)

    return merged


def _repair_query_database_from_semantic_context(
    op_item: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Complete incomplete query_database/load_postgis_layer operations using
    semantic_planning_context.semantic_layers.

    Example:
      params={"where": "...", "output_srid": 3857}
    becomes:
      params={
        "source_type": "postgis",
        "mode": "select_table",
        "schema": "public",
        "table": "planet_osm_point",
        "columns": [...],
        "geom_col": "way",
        "geom_alias": "geom",
        "where": "...",
        "limit": 5000,
        "output_srid": 3857
      }
    """
    op_copy = dict(op_item)
    op_name = str(op_copy.get("op") or "")

    if op_name not in {"query_database", "load_postgis_layer"}:
        return op_copy

    params = op_copy.get("params")
    if not isinstance(params, dict):
        params = {}

    if _query_database_params_complete(params):
        return op_copy

    semantic_params = _best_semantic_params_for_output(
        op_copy.get("output"),
        context,
    )

    if not semantic_params:
        return op_copy

    op_copy["params"] = _merge_query_database_params(
        semantic_params,
        params,
    )
    op_copy.setdefault("inputs", {})

    return op_copy


def _has_query_database_for_entity_ref(
    operations: list[Any],
    ref: str,
) -> bool:
    for op in operations:
        if not isinstance(op, dict):
            continue

        if str(op.get("op") or "") not in {"query_database", "load_postgis_layer"}:
            continue

        output = op.get("output")
        if _ref_matches_concept(output, ref):
            return True

    return False


def _inject_query_database_ops_for_database_entities(
    data: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Improved version.

    Inject query_database only when there is no existing query_database output
    matching the entity ref/concept, including plural aliases like:
      metro_station -> metro_stations
      shopping_center -> shopping_centers

    If semantic context has a better candidate, use it to complete injected params.
    """
    entities = data.get("entities")
    operations = data.get("operations")

    if not isinstance(entities, list) or not isinstance(operations, list):
        return data

    injected: list[dict[str, Any]] = []

    for entity in entities:
        if not _is_database_entity(entity):
            continue

        ref = str(entity.get("ref") or "").strip()
        if not ref:
            continue

        if _has_query_database_for_entity_ref(operations, ref):
            continue

        binding = entity.get("binding")
        if not isinstance(binding, dict):
            continue

        params = _query_database_params_from_binding(binding)

        semantic_params = _best_semantic_params_for_output(ref, context)
        if semantic_params:
            params = _merge_query_database_params(semantic_params, params)

        injected.append(
            {
                "op": "query_database",
                "inputs": {},
                "params": params,
                "output": ref,
            }
        )

    if not injected:
        return data

    new_data = dict(data)
    new_data["operations"] = injected + operations

    metadata = dict(new_data.get("metadata") or {})
    repairs = list((metadata.get("pre_normalization_repairs") or []))
    repairs.append("injected query_database operations for database entities")
    metadata["pre_normalization_repairs"] = repairs
    new_data["metadata"] = metadata

    return new_data


def _pre_normalize_query_spec_json(data: dict[str, Any], *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Pre-normalize raw LLM JSON before strict QuerySpec parsing.

    The strict parser is still the source of truth. This function only repairs
    common JSON-shape mistakes that do not change semantic meaning, especially:
      - arrays used where QuerySpec requires objects
      - query_database params placed under inputs
      - source/features aliases where op_catalog expects vector
      - database entities without query_database load operations
    """
    if not isinstance(data, dict):
        return data

    normalized = dict(data)

    operations = normalized.get("operations")
    if isinstance(operations, list):
        new_operations: list[Any] = []

        for op_item in operations:
            if not isinstance(op_item, dict):
                new_operations.append(op_item)
                continue

            op_copy = dict(op_item)

            if "inputs" in op_copy:
                coerced_inputs = _coerce_list_of_pairs_to_dict(op_copy.get("inputs"))
                if coerced_inputs is not None:
                    op_copy["inputs"] = coerced_inputs

            if "params" in op_copy:
                coerced_params = _coerce_list_of_pairs_to_dict(op_copy.get("params"))
                if coerced_params is not None:
                    op_copy["params"] = coerced_params

            op_copy = _repair_query_database_operation_shape(op_copy)
            op_copy = _repair_query_database_from_semantic_context(op_copy, context)
            op_copy = _repair_vector_input_aliases(op_copy)

            new_operations.append(op_copy)

        normalized["operations"] = new_operations

    outputs = normalized.get("outputs")
    if isinstance(outputs, list):
        new_outputs: list[Any] = []

        for output_item in outputs:
            if not isinstance(output_item, dict):
                new_outputs.append(output_item)
                continue

            out_copy = dict(output_item)

            if "config" in out_copy:
                coerced_config = _coerce_list_of_pairs_to_dict(out_copy.get("config"))
                if coerced_config is not None:
                    out_copy["config"] = coerced_config

            new_outputs.append(out_copy)

        normalized["outputs"] = new_outputs

    normalized = _inject_query_database_ops_for_database_entities(normalized, context=context)

    return normalized


def query_spec_from_dict(data: dict[str, Any], *, raw_query_fallback: str = "") -> QuerySpec:
    data = _dict(data, "QuerySpec JSON")

    goal = str(data.get("goal") or "")
    if not goal:
        raise LLMSpecGenerationError("QuerySpec JSON must contain non-empty goal.")

    operations_raw = _list(data.get("operations", []), "operations")
    if not operations_raw:
        raise LLMSpecGenerationError("QuerySpec JSON must contain at least one operation.")

    entities: list[EntitySpec] = []
    for i, item in enumerate(_list(data.get("entities", []), "entities")):
        item = _dict(item, f"entities[{i}]")
        ref = str(item.get("ref") or "")
        kind = str(item.get("kind") or "")
        if not ref:
            raise LLMSpecGenerationError(f"entities[{i}].ref is required.")
        if not kind:
            raise LLMSpecGenerationError(f"entities[{i}].kind is required.")

        entities.append(
            EntitySpec(
                ref=ref,
                kind=kind,
                binding=_dict(item.get("binding") or {}, f"entities[{i}].binding"),
                hints=_dict(item.get("hints") or {}, f"entities[{i}].hints"),
            )
        )

    operations: list[OperationSpec] = []
    for i, item in enumerate(operations_raw):
        item = _dict(item, f"operations[{i}]")
        op = str(item.get("op") or "")
        if not op:
            raise LLMSpecGenerationError(f"operations[{i}].op is required.")

        operations.append(
            OperationSpec(
                op=op,
                inputs=_dict(item.get("inputs") or {}, f"operations[{i}].inputs"),
                params=_dict(item.get("params") or {}, f"operations[{i}].params"),
                output=str(item.get("output") or ""),
            )
        )

    outputs: list[OutputSpec] = []
    for i, item in enumerate(_list(data.get("outputs", []), "outputs")):
        item = _dict(item, f"outputs[{i}]")
        kind = str(item.get("kind") or "")
        if not kind:
            raise LLMSpecGenerationError(f"outputs[{i}].kind is required.")

        outputs.append(
            OutputSpec(
                kind=kind,
                source=str(item.get("source") or ""),
                format=str(item.get("format") or ""),
                config=_dict(item.get("config") or {}, f"outputs[{i}].config"),
            )
        )

    metadata = _dict(data.get("metadata") or {}, "metadata")
    metadata.setdefault("generated_by", "llm")

    return QuerySpec(
        raw_query=str(data.get("raw_query") or raw_query_fallback or ""),
        goal=goal,
        entities=entities,
        operations=operations,
        outputs=outputs,
        source=str(data.get("source") or "llm"),
        metadata=metadata,
    )


def query_spec_to_dict(spec: QuerySpec) -> dict[str, Any]:
    return asdict(spec)


def _schema_hint() -> str:
    return """
Return ONLY one JSON object with this shape:

{
  "raw_query": "original user query",
  "goal": "short_goal_name",
  "entities": [
    {
      "ref": "properties",
      "kind": "vector|raster|database|external_api",
      "binding": {},
      "hints": {}
    }
  ],
  "operations": [
    {
      "op": "supported_operation_name",
      "inputs": {"logical_input_role": "entity_or_previous_output_ref"},
      "params": {},
      "output": "new_output_ref"
    }
  ],
  "outputs": [
    {
      "kind": "vector|map_layer|report|pdf|json|file",
      "source": "operation_output_ref",
      "format": "pdf|geojson|json|",
      "config": {}
    }
  ],
  "metadata": {
    "language": "fa",
    "assumptions": []
  }
}
"""


def _op_input_roles_reference() -> str:
    """
    Every supported operation's required input roles - the exact keys
    "inputs" must use for that operation - generated directly from
    OP_CATALOG's input_map rather than hand-written per-operation
    examples.

    This exists because a hand-written example is exactly how distance_to
    went undocumented here: "Important mappings" below spells out
    filter_by_distance, filter_points_in_polygon and enrich_risk by hand,
    but nobody added an entry when distance_to (source_features/
    target_features, i.e. inputs "vector"/"target") was added to the
    catalog, and the LLM repeatedly omitted "target" as a result. Deriving
    this list from OP_CATALOG means a future operation can't silently go
    undocumented the same way.
    """
    lines = []
    for name in list_supported_ops():
        roles = list(get_op(name).input_map)
        role_desc = ", ".join(roles) if roles else "(none)"
        lines.append(f"- {name}: inputs keys = {{{role_desc}}}")
    return "\n".join(lines)


def _op_param_reference() -> str:
    """
    Every supported operation's accepted params - the exact keys "params"
    may use for that operation - generated directly from OP_CATALOG's
    param_map rather than hand-written per-operation examples.

    Mirrors _op_input_roles_reference() above, for the same reason: a
    hand-written list is exactly how an operation's real parameter names
    go undocumented here, same as distance_to's inputs did before that
    fix. DeterministicPlanner(PlannerConfig(strict_params=True)) rejects
    any params key not in this list at planning time, so an LLM that
    guesses a name not listed here fails the plan outright instead of
    reaching a plugin with a wrong keyword argument.
    """
    lines = []
    for name in list_supported_ops():
        params = list(get_op(name).param_map)
        param_desc = ", ".join(params) if params else "(none)"
        lines.append(f"- {name}: params keys = {{{param_desc}}}")
    return "\n".join(lines)


def _op_param_shape_reference() -> str:
    """
    A worked value-shape example for every structured op param (the
    filter_attribute.where / enrich_feature_properties.rules gap: the model
    was taught the key names above but never what a valid value for a
    non-scalar key looks like). Lives in op_param_shapes.py as a maintained
    table keyed by (capability, kwarg); tests/test_op_param_shapes.py fails
    if a structured param_map target has no entry, and runs the examples
    through the real plugins.
    """
    return op_param_shape_reference()


def _domain_guidance() -> str:
    supported = ", ".join(list_supported_ops())
    pending = ", ".join(list_pending_ops())
    input_roles = _op_input_roles_reference()
    op_params = _op_param_reference()
    op_param_shapes = _op_param_shape_reference()

    return f"""
You are a planning assistant for a smart spatial analysis system.

Your job:
- Convert natural language into QuerySpec JSON.
- Use supported operations only for executable operations.
- Generate scoring_spec when user asks for scoring/ranking.
- Generate OutputSpec/report config when user asks for PDF/report/map/table.
- Do not execute anything.

Supported operations:
{supported}

Every operation REQUIRES exactly these "inputs" keys - an operation with a
missing key fails to plan. This list is generated from the operation
catalog itself, so trust it over any example below if the two ever
disagree:
{input_roles}

Every operation only ACCEPTS these "params" keys - a params key not listed
for that operation is rejected and fails to plan. This list is also
generated from the operation catalog itself:
{op_params}

Structured param VALUES - a params key whose value is an object or list
(not a plain string/number/boolean) must use exactly one of these shapes.
A value with the wrong shape (e.g. a SQL-style string for
filter_attribute.where) fails the plan. Params not listed here take a plain
string, number or boolean:
{op_param_shapes}
Note: sort_limit has no "where" param. To filter AND sort in one step, use
filter_attribute with "where" plus "sort_by"/"sort_order"/"limit".

Pending operations, not executable yet:
{pending}

Important mappings:
- CRITICAL - any distance/nearest-neighbor operation (filter_by_distance,
  spatial_nearest, nearest_neighbor, distance_to) needs BOTH of its vector
  inputs reprojected with crs_transform first, not just the source/site
  layer. Distance is computed from raw geometry coordinates with zero CRS
  awareness - if one layer is reprojected to a metric CRS and the other is
  left in whatever CRS it was uploaded in (commonly EPSG:4326), the
  "distance" returned is a number between two points in DIFFERENT CRSs,
  meaningless but not an error by itself. Reproject EVERY layer that feeds
  a distance operation to the SAME target CRS, and pass that CRS as both
  source_crs and target_crs on the distance operation itself - this lets
  the operation catch a mismatch and raise instead of returning a bogus
  value:
    crs_transform(sites) -> sites_metric        (target_crs="<PROJECTED_CRS>")
    crs_transform(metro) -> metro_metric        (target_crs="<PROJECTED_CRS>")
    spatial_nearest(source=sites_metric, target=metro_metric,
      params={{"source_crs": "<PROJECTED_CRS>", "target_crs": "<PROJECTED_CRS>"}})
  WRONG: spatial_nearest(source=sites_metric, target=metro) - "metro" was
  never reprojected, so its coordinates are still in the original CRS.
  CRITICAL - <PROJECTED_CRS> above is a placeholder, not a real code to
  copy: pick a projected CRS whose own area of use actually covers where
  the input data is located (e.g. the correct local UTM zone, or a
  national/regional grid for that country) - never reuse a CRS code from
  this prompt, an earlier answer, or a different query's data just
  because it is a familiar or "safe-looking" example. Both layers ending
  up in the SAME CRS is not enough on its own: reprojecting data in
  Istanbul to an Austrian or French national grid produces internally
  consistent but physically meaningless distances, with no error at any
  step, because the projection math still runs - it is simply the wrong
  part of the planet for that CRS's definition. If an "Input data facts"
  section appears later in these instructions, it states a projected CRS
  computed from THIS query's own input data - use that value.

- "nearer than X meters to POI" (keep only features WITHIN X):
  op="filter_by_distance"
  inputs={{"vector": "<source>", "reference": "<poi>"}}
  params={{"max_distance_m": X, "k": 1, "drop_unmatched": true}}

- "farther than X meters from the nearest POI" / "no POI within X" /
  "underserved because the nearest POI is too far" - the OPPOSITE case:
  find the nearest POI for EVERY feature with NO max_distance/
  max_distance_m, then filter on the distance field:
    spatial_nearest(source=<source>, target=<poi>, params={{"k": 1, ...}})
    filter_attribute(vector=<nearest output>,
      params={{"where": {{"field": "_nearest_distance", "op": "gt", "value": X}}}})
  CRITICAL - never set max_distance/max_distance_m on spatial_nearest/
  nearest_neighbor to find "the nearest": features beyond the cap lose
  their distance, so a "farther than X" filter after it silently misses
  the farthest features (or returns nothing at all if the cap <= X).
  max_distance is only for "within X" questions.

- "multiple rings/distance bands around a point/line/polygon" (e.g.
  "200m, 500m and 800m rings around each station", concentric zones, a
  gradient outward from a feature):
  use op="ring_buffer" with params={{"distances": [200, 500, 800]}} - do
  NOT chain op="buffer" multiple times for this. Chaining buffer only ever
  produces nested full-circle disks (each one containing all the smaller
  ones), never the annulus/gap BETWEEN two radii, which is what "ring" or
  "band" means here.

- CRITICAL - spatial_join's params.cardinality defaults to "first": each
  source feature is joined to only ONE matching target feature (the first
  one found), even if it actually matches several. This silently
  undercounts whenever the target layer's zones can overlap - most
  commonly a spatial_join whose target is a ring_buffer output (or any
  other layer with multiple zones generated from separate input
  features, e.g. several stations' service areas). A point inside two
  overlapping rings/zones would only be counted for one of them with the
  default. Whenever the query means for a source feature matching several
  zones to be counted under EACH of them (e.g. "for every station, which
  points are within its rings", "for each amenity, list every zone it
  falls into" - i.e. a point can legitimately belong to more than one
  zone), set params={{"cardinality": "one_to_many", "include_target_properties": true}}
  explicitly on that spatial_join. Do not leave cardinality unset and
  rely on the default when the target can have overlapping zones.

- "inside permitted/buildable polygon":
  op="filter_points_in_polygon"
  inputs={{"vector": "<points>", "polygon": "<polygon_layer>"}}
  params={{"predicate": "within", "drop_outside": true}}

- CRITICAL - "points inside a polygon/zone" has TWO different shapes that
  need DIFFERENT operations - picking the wrong one plans successfully and
  runs without error, but silently loses the information the user asked
  for:
  * User only wants a boolean keep/drop filter (e.g. "keep only points
    inside the permitted area", "drop points outside the buildable zone"):
    use op="filter_points_in_polygon". It returns nothing but a
    __in_polygon__ true/false flag - it does NOT record which polygon
    matched.
  * User wants to know WHICH specific zone/polygon/ring each point falls
    into (e.g. "for each point, tell me which zone it falls into", "label
    each point by its zone", "group points by distance band/ring"):
    do NOT use op="filter_points_in_polygon" for this - it has no way to
    carry the matched polygon's identity, so the output would have no
    zone information at all. Use op="spatial_join" instead:
    inputs={{"source": "<points>", "target": "<zones/polygons>"}}
    params={{"predicate": "within", "include_target_properties": true}}
    include_target_properties=true is what copies the matched polygon's
    own properties (id, name, zone label, ring_label, etc.) onto each
    output point.

- After a distance operation, if the resulting distance field should be used for scoring,
  use enrich_feature_properties to copy/rename it to a semantic field like:
  distance_to_poi or distance_to_road.

- For flood/earthquake/fire risk enrichment:
  use enrich_risk before score_features:
  op="enrich_risk"
  inputs={{"vector": "<features>"}}
  params={{
    "default_risks": {{
      "flood_risk": "low",
      "earthquake_risk": "low",
      "fire_risk": "low"
    }}
  }}

- "score/rank":
  use score_features then rank_features.

- CRITICAL - every scoring factor MUST include an explicit "type". There is
  NO default - a factor with no "type" is rejected, it does not fall back
  to anything. (An earlier version of this system defaulted a missing
  "type" to "boolean" silently; for a distance field that meant every
  score came out 0.0 for every feature, with no error - that default no
  longer exists.) Valid types: "boolean", "boolean_bonus",
  "inverse_distance", "risk_level", "inverse_level", "threshold",
  "condition", "direct", "numeric", "inverse_numeric". A distance field
  (anything you computed with spatial_nearest/nearest_neighbor/distance_to)
  needs "type": "inverse_distance" and a numeric "max_distance":
    {{"name": "near_metro", "field": "distance_to_metro",
      "type": "inverse_distance", "max_distance": 800, "weight": 1}}
  WRONG (missing "type" - rejected, not defaulted):
    {{"field": "distance_to_metro", "weight": -1}}

- CRITICAL - scoring against MORE THAN ONE computed field (e.g. distance to
  several different amenities/references): each computation must be CHAINED
  onto the previous one's output, not all computed separately from the
  original base vector. score_features.inputs.vector must be the output of
  the LAST computation in the chain - the only ref that actually carries
  every field the scoring factors reference. A score_features op reading
  from the base vector, or from any computation earlier than the last one,
  is missing every field computed after that point, and every factor
  referencing a missing field silently scores 0 - this does not raise an
  error, so a degenerate all-zero/near-zero score on every feature is a
  sign this happened.

  WRONG - three computations all read "sites", so only one field ever
  reaches score_features:
    spatial_nearest(source="sites", target="metro") -> "sites_with_metro"
    spatial_nearest(source="sites", target="schools") -> "sites_with_schools"
    spatial_nearest(source="sites", target="parks") -> "sites_with_parks"
    score_features(vector="sites") - WRONG: none of the three computed
      distance fields are on "sites" itself.

  CORRECT - each step reads the PREVIOUS step's output, so fields
  accumulate onto one ref:
    spatial_nearest(source="sites", target="metro",
      params={{"distance_field": "distance_to_metro"}}) -> "sites_with_metro"
    spatial_nearest(source="sites_with_metro", target="schools",
      params={{"distance_field": "distance_to_schools"}}) -> "sites_with_schools"
    spatial_nearest(source="sites_with_schools", target="parks",
      params={{"distance_field": "distance_to_parks"}}) -> "sites_with_parks"
    score_features(vector="sites_with_parks") - CORRECT: this ref alone
      carries distance_to_metro, distance_to_schools AND distance_to_parks,
      because each step's "source" was the previous step's output, not
      "sites" again. Set params.distance_field on each spatial_nearest call
      so the fields get distinct names instead of overwriting each other.

  If two computations genuinely cannot be chained (they operate on
  unrelated feature sets that must be merged by a shared key rather than
  by being the same features), use join_feature_properties to merge them
  before scoring instead of chaining.

- If user asks for PDF/report but report plugin is not executable yet:
  include outputs with kind="report", format="pdf".
  Do not invent unsupported operation nodes.

Safety:
- JSON only.
- No markdown.
- No code execution.
- No shell.
- No direct plugin calls.
"""


def _semantic_planning_context_guidance() -> str:
    return """
Semantic Planning Context Guardrails:
- If context.semantic_planning_context is provided, use it as the primary source
  for PostGIS layer selection and spatial operation planning.
- Do not invent PostGIS table names.
- Do not invent PostGIS column names.
- Do not generate raw SQL.
- Do not use params.sql for query_database.
- For loading PostGIS data, use op="query_database" or op="load_postgis_layer"
  with params copied/adapted from semantic_planning_context.semantic_layers[*][*].params.
- Use only schema/table/geom_col/columns/where values present in semantic layer
  candidates unless the user explicitly provides a verified schema.
- Respect semantic_planning_context.guardrails:
  llm_must_not_generate_raw_sql,
  llm_must_not_invent_table_names,
  llm_must_not_invent_column_names,
  use_semantic_layer_candidates_first.
- For nearest/closest relationship between two semantic concepts, use:
  op="spatial_nearest"
  params={"k": 1, "include_target_geometry": true}
- For top N nearest results after spatial_nearest, use:
  op="top_n"
  params={
    "score_field": "_nearest_distance",
    "descending": false,
    "limit": requested_limit
  }
- If semantic_planning_context.operation_hints is provided, follow those hints.
- QuerySpec JSON shape is strict:
  operations must be an array of objects.
  operations[i].inputs must be a JSON object, never an array.
  operations[i].params must be a JSON object, never an array.
  outputs[i].config must be a JSON object, never an array.
- Correct examples:
  "inputs": {"source": "metro_layer", "target": "shopping_layer"}
  "params": {"k": 1, "include_target_geometry": true}
- Incorrect examples:
  "inputs": [{"source": "metro_layer"}, {"target": "shopping_layer"}]
  "params": [{"k": 1}]
- If no semantic layer candidate exists for a requested concept, do not guess a
  table or column. Return a safe QuerySpec that reports insufficient semantic
  layer resolution or asks for clarification.
"""


def build_llm_messages(
    raw_query: str,
    *,
    context: dict[str, Any] | None = None,
    system_hints: str | None = None,
    input_data_extent: InputDataExtent | None = None,
    input_layers: tuple[InputLayer, ...] | None = None,
) -> list[dict[str, str]]:
    system = _domain_guidance() + "\n" + _schema_hint()

    if isinstance(context, dict) and context.get("semantic_planning_context"):
        system += "\n" + _semantic_planning_context_guidance()

    # Before system_hints, so a caller's own hints still come last and can
    # override a computed fact they know better about.
    if input_layers:
        system += "\n" + render_input_layer_facts(input_layers) + "\n"

    if input_data_extent is not None:
        system += "\n" + render_input_data_facts(input_data_extent) + "\n"

    if system_hints:
        system += "\nAdditional hints:\n" + system_hints

    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "query": raw_query,
                    "context": context or {},
                },
                ensure_ascii=False,
            ),
        },
    ]


def _semantic_distance_field(reference_ref: str) -> str:
    """
    Pick a semantic distance field based on the reference entity/output name.
    """
    ref = str(reference_ref or "").lower()

    if any(token in ref for token in ("road", "roads", "street", "highway", "خیابان", "جاده")):
        return "distance_to_road"

    if any(token in ref for token in ("poi", "metro", "mall", "shopping", "station", "مترو", "مرکز", "خرید")):
        return "distance_to_poi"

    return "distance_to_reference"


def _unique_ref(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


# Operations known to be able to produce several overlapping output zones
# from separate input features - e.g. ring_buffer builds one ring set per
# input feature, and two features' rings routinely overlap (two metro
# stations 1500m apart both have a 1200m ring covering the space between
# them). A spatial_join whose target traces back to one of these needs
# cardinality="one_to_many" or it silently drops every match after the
# first for any source feature that falls inside more than one zone - see
# _default_spatial_join_cardinality_for_overlapping_zones below. Currently
# just ring_buffer; add here if a future op gains the same shape.
_OVERLAP_PRONE_ZONE_OPS = frozenset({"ring_buffer"})


def normalize_llm_query_spec_for_planning(spec: QuerySpec) -> QuerySpec:
    """
    Harden and improve LLM-produced QuerySpec before deterministic planning.

    Repairs:
        - Remove unsupported input roles.
        - Add default scoring_spec when score_features misses it.
        - Add score_field/rank_field to rank_features when missing.
        - Default spatial_join's cardinality to "one_to_many" when its
          target traces back to an op that can produce overlapping zones
          (see _OVERLAP_PRONE_ZONE_OPS) and the caller left cardinality
          unset - the plugin's own default ("first") silently keeps only
          one match per source feature otherwise.
        - If default scoring is used, inject enrichment nodes after spatial
          operations to create stable semantic scoring fields:
              distance_to_poi
              distance_to_road
              inside_buildable_zone
    """
    repairs: list[str] = []
    normalized_ops: list[OperationSpec] = []

    ops_by_output: dict[str, OperationSpec] = {
        op.output: op for op in spec.operations if op.output
    }

    used_refs: set[str] = set()
    for op in spec.operations:
        if op.output:
            used_refs.add(op.output)

    # Inject enrichment only when LLM did not provide its own scoring spec.
    # This keeps old explicit LLM specs stable, but improves weak/missing specs.
    #
    # NOTE: since a score_features op missing scoring_spec/factors now
    # raises (see below) instead of getting a default injected, this flag
    # can only be True for a spec that is about to raise before this
    # function returns - so the enrichment-injection code below it is
    # currently unreachable in any spec that completes normally. Left in
    # place rather than removed with this fix: ripping it out means also
    # removing _semantic_distance_field and updating the tests that
    # exercise it, which is a larger, separate change from the two bugs
    # this pass fixes.
    should_inject_enrichment = any(
        op.op == "score_features"
        and "scoring_spec" not in op.params
        and "factors" not in op.params
        for op in spec.operations
    )

    last_score_field: str | None = None
    ref_rewrites: dict[str, str] = {}

    for op in spec.operations:
        # Unknown operations stay unchanged so Planner can raise clear errors.
        if not is_supported(op.op):
            normalized_ops.append(op)
            continue

        descriptor = get_op(op.op)

        allowed_roles = set(descriptor.input_map)
        clean_inputs: dict[str, str] = {}

        for role, ref in op.inputs.items():
            if role in allowed_roles:
                ref_str = str(ref)
                clean_inputs[role] = ref_rewrites.get(ref_str, ref_str)
            else:
                repairs.append(
                    f"removed unsupported input role {role!r} from operation {op.op!r}"
                )

        clean_params = dict(op.params)

        if op.op == "score_features":
            if "scoring_spec" not in clean_params and "factors" not in clean_params:
                # No safe generic default exists here: this used to inject
                # a hardcoded real-estate scoring_spec (investment_score,
                # inside_buildable_zone, flood_risk, ...) into ANY
                # score_features op that omitted one, regardless of the
                # query's actual domain. For a non-real-estate query that
                # silently produced a nonsense score column, or crashed
                # downstream when those fields didn't exist on the data -
                # indistinguishable from a real real-estate query's own
                # valid output. The LLM must describe its own domain's
                # scoring, or this fails loudly right here instead.
                raise LLMSpecGenerationError(
                    f"score_features operation (output={op.output!r}) has "
                    "neither 'scoring_spec' nor 'factors'. There is no "
                    "domain-neutral default to fall back to - the LLM must "
                    "supply a complete scoring specification for its own "
                    "query."
                )

            scoring_spec = clean_params.get("scoring_spec")
            if isinstance(scoring_spec, dict):
                scoring_inner = scoring_spec.get("scoring")
                if isinstance(scoring_inner, dict):
                    last_score_field = str(scoring_inner.get("output_field") or "score")
                else:
                    last_score_field = str(scoring_spec.get("output_field") or "score")
            else:
                last_score_field = str(clean_params.get("output_field") or "score")

        if op.op == "rank_features":
            if "score_field" not in clean_params:
                clean_params["score_field"] = last_score_field or "score"
                repairs.append(
                    f"added score_field={clean_params['score_field']!r} to rank_features"
                )
            if "rank_field" not in clean_params:
                clean_params["rank_field"] = "rank"
                repairs.append("added rank_field='rank' to rank_features")

        if op.op == "spatial_join" and "cardinality" not in clean_params:
            target_ref = clean_inputs.get("target")
            if target_ref is not None:
                ancestors = _ancestor_refs(str(target_ref), ops_by_output)
                traces_to_overlap_prone_op = any(
                    ops_by_output.get(ref) is not None
                    and ops_by_output[ref].op in _OVERLAP_PRONE_ZONE_OPS
                    for ref in ancestors
                )

                if traces_to_overlap_prone_op:
                    clean_params["cardinality"] = "one_to_many"
                    repairs.append(
                        f"set cardinality='one_to_many' on spatial_join "
                        f"(output={op.output!r}) because its target "
                        f"{target_ref!r} traces back to an operation that "
                        "can produce overlapping zones - the plugin's own "
                        "default cardinality='first' would silently keep "
                        "only one match for any source feature that falls "
                        "in more than one zone"
                    )

        normalized_op = OperationSpec(
            op=op.op,
            inputs=clean_inputs,
            params=clean_params,
            output=op.output,
        )
        normalized_ops.append(normalized_op)

        if not should_inject_enrichment:
            continue

        # Inject semantic distance enrichment after distance operations.
        if op.op == "filter_by_distance" and op.output:
            reference_ref = str(op.inputs.get("reference") or "")
            target_field = _semantic_distance_field(reference_ref)

            enriched_output = _unique_ref(f"{op.output}_enriched", used_refs)

            normalized_ops.append(
                OperationSpec(
                    op="enrich_feature_properties",
                    inputs={"vector": op.output},
                    params={
                        "rules": [
                            {
                                "target": target_field,
                                "first_existing": [
                                    "distance_m",
                                    "distance",
                                    "nearest_distance_m",
                                    "nearest_distance",
                                    "min_distance_m",
                                    "min_distance",
                                ],
                                "transform": "float",
                            }
                        ],
                        "skip_missing": True,
                    },
                    output=enriched_output,
                )
            )

            ref_rewrites[op.output] = enriched_output
            repairs.append(
                f"inserted enrich_feature_properties after {op.op!r} "
                f"to create {target_field!r}"
            )

        # Inject inside_buildable_zone after polygon filtering.
        if op.op == "filter_points_in_polygon" and op.output:
            enriched_output = _unique_ref(f"{op.output}_enriched", used_refs)

            normalized_ops.append(
                OperationSpec(
                    op="enrich_feature_properties",
                    inputs={"vector": op.output},
                    params={
                        "rules": [
                            {
                                "target": "inside_buildable_zone",
                                "value": True,
                            }
                        ],
                        "skip_missing": True,
                    },
                    output=enriched_output,
                )
            )

            ref_rewrites[op.output] = enriched_output
            repairs.append(
                "inserted enrich_feature_properties after "
                "'filter_points_in_polygon' to create 'inside_buildable_zone'"
            )

    # Rewrite OutputSpec source if it points to an operation output that was enriched.
    normalized_outputs: list[OutputSpec] = []
    for output in spec.outputs:
        source = output.source
        if source in ref_rewrites:
            source = ref_rewrites[source]
            repairs.append(f"rewrote output source to enriched ref {source!r}")

        normalized_outputs.append(
            OutputSpec(
                kind=output.kind,
                source=source,
                format=output.format,
                config=output.config,
            )
        )

    metadata = dict(spec.metadata or {})
    if repairs:
        normalization = metadata.get("normalization")
        if not isinstance(normalization, dict):
            normalization = {}
        existing_repairs = normalization.get("repairs")
        if not isinstance(existing_repairs, list):
            existing_repairs = []
        normalization["applied"] = True
        normalization["repairs"] = existing_repairs + repairs
        metadata["normalization"] = normalization

    return QuerySpec(
        raw_query=spec.raw_query,
        goal=spec.goal,
        entities=spec.entities,
        operations=normalized_ops,
        outputs=normalized_outputs,
        source=spec.source,
        metadata=metadata,
    )


def _should_inject_risk_enrichment(spec: QuerySpec) -> bool:
    """
    Returns True if:
    - scoring_spec references risk fields (flood_risk, earthquake_risk, fire_risk)
    - but no enrich_risk operation exists in the spec
    """
    has_risk_fields = False
    has_enrich_risk = False

    for op in spec.operations:
        if op.op == "enrich_risk":
            has_enrich_risk = True
            break
        if op.op == "score_features":
            scoring = op.params.get("scoring_spec", {})
            factors = scoring.get("factors", [])
            for factor in factors:
                if factor.get("field") in {"flood_risk", "earthquake_risk", "fire_risk"}:
                    has_risk_fields = True

    return has_risk_fields and not has_enrich_risk


def _inject_risk_before_scoring(spec: QuerySpec) -> tuple[QuerySpec, list[str]]:
    """
    Inject enrich_risk node before the first score_features node.
    """
    repairs: list[str] = []
    new_ops: list[OperationSpec] = []
    injected = False

    for op in spec.operations:
        if op.op == "score_features" and not injected:
            # Find the input vector ref of score_features.
            vector_ref = str(op.inputs.get("vector") or "")

            risk_output = f"{vector_ref}_risk" if vector_ref else "risk_enriched"

            new_ops.append(OperationSpec(
                op="enrich_risk",
                inputs={"vector": vector_ref},
                params={
                    "default_risks": {
                        "flood_risk": "low",
                        "earthquake_risk": "low",
                        "fire_risk": "low",
                    }
                },
                output=risk_output,
            ))

            # Rewrite score_features input to risk_output.
            new_ops.append(OperationSpec(
                op=op.op,
                inputs={"vector": risk_output},
                params=op.params,
                output=op.output,
            ))

            repairs.append(
                f"auto-injected enrich_risk before score_features "
                f"({vector_ref!r} -> {risk_output!r})"
            )
            injected = True
        else:
            new_ops.append(op)

    return (
        QuerySpec(
            raw_query=spec.raw_query,
            goal=spec.goal,
            entities=spec.entities,
            operations=new_ops,
            outputs=spec.outputs,
            source=spec.source,
            metadata=spec.metadata,
        ),
        repairs,
    )



def _should_inject_report(spec: QuerySpec) -> bool:
    """
    Returns True if:
    - output kind is 'report' or format is 'pdf'
    - but no build_report operation exists
    """
    has_report_output = any(
        o.kind in {"report", "pdf"} or o.format in {"pdf", "html"}
        for o in spec.outputs
    )
    has_build_report = any(op.op == "build_report" for op in spec.operations)

    return has_report_output and not has_build_report


def _inject_report_pipeline(spec: QuerySpec) -> tuple[QuerySpec, list[str]]:
    """
    After rank_features, inject:
        build_report
        render_pdf  (only if format is pdf)
    And update outputs to point to the final report node.
    """
    repairs: list[str] = []

    # Find the last rank_features op, so build_report reuses the score/rank
    # field names that op actually used - not a hardcoded guess. By this
    # point base normalization has already ensured rank_features carries
    # score_field/rank_field (either LLM-provided or defaulted from the
    # upstream score_features op's own output_field), so these are always
    # present here.
    last_rank_output: str | None = None
    last_rank_op: OperationSpec | None = None
    for op in spec.operations:
        if op.op == "rank_features" and op.output:
            last_rank_output = op.output
            last_rank_op = op

    if not last_rank_output:
        # Fallback: use the last operation output.
        for op in reversed(spec.operations):
            if op.output:
                last_rank_output = op.output
                break

    if not last_rank_output:
        return spec, []

    # Previously hardcoded to "investment_score"/"rank" regardless of what
    # the plan's own rank_features op used - a non-real-estate plan (e.g.
    # score_field="accessibility_score") got a report referencing a column
    # its data never had.
    score_field = "score"
    rank_field = "rank"
    if last_rank_op is not None:
        score_field = str(last_rank_op.params.get("score_field") or score_field)
        rank_field = str(last_rank_op.params.get("rank_field") or rank_field)

    new_ops = list(spec.operations)

    # build_report node
    report_output = "report"
    new_ops.append(OperationSpec(
        op="build_report",
        inputs={"vector": last_rank_output},
        params={
            "score_field": score_field,
            "rank_field": rank_field,
        },
        output=report_output,
    ))
    repairs.append(
        f"auto-injected build_report after {last_rank_output!r}"
    )

    # render_pdf node if format is pdf
    want_pdf = any(
        o.format == "pdf" or o.kind == "pdf"
        for o in spec.outputs
    )

    final_output = report_output

    if want_pdf:
        pdf_output = "pdf_report"
        new_ops.append(OperationSpec(
            op="render_pdf",
            inputs={"report": report_output},
            params={"save_to_disk": True},
            output=pdf_output,
        ))
        repairs.append("auto-injected render_pdf for PDF output")
        final_output = pdf_output

    # Update output sources.
    new_outputs: list[OutputSpec] = []
    for out in spec.outputs:
        if out.kind in {"report", "pdf"} or out.format in {"pdf", "html"}:
            new_outputs.append(OutputSpec(
                kind=out.kind,
                source=final_output,
                format=out.format,
                config=out.config,
            ))
        else:
            new_outputs.append(out)

    return (
        QuerySpec(
            raw_query=spec.raw_query,
            goal=spec.goal,
            entities=spec.entities,
            operations=new_ops,
            outputs=new_outputs,
            source=spec.source,
            metadata=spec.metadata,
        ),
        repairs,
    )



def _validate_operation_input_roles(spec: QuerySpec) -> None:
    """
    Validate every operation's inputs against OP_CATALOG's input_map before
    handing the spec back to the caller.

    DeterministicPlanner.build() already performs this exact check
    (_map_inputs in planner.py) and raises PlanningError - so a spec that
    fails this check was always going to fail anyway. Duplicating the
    check here means it fails right where the spec was generated, as a
    specific LLMSpecGenerationError naming the missing role, rather than
    as a generic PlanningError raised later out of the planner - the same
    failure, several call frames away from its cause, indistinguishable
    from any other planning error.

    Every input_map key is a required role: the planner has no notion of
    an optional one today (see the real_estate_spatial_enrich note in
    op_catalog.py), so this mirrors _map_inputs's missing_roles check
    exactly rather than inventing separate required/optional semantics.
    """
    for op in spec.operations:
        if not is_supported(op.op):
            continue

        required_roles = get_op(op.op).input_map
        missing_roles = [role for role in required_roles if role not in op.inputs]

        if missing_roles:
            raise LLMSpecGenerationError(
                f"operation {op.op!r} (output={op.output!r}) is missing "
                f"required input role(s) {missing_roles!r}. It supplied "
                f"{sorted(op.inputs)!r}; {op.op!r} requires "
                f"{sorted(required_roles)!r}."
            )


_NEAREST_OPS_WITH_DISTANCE_FIELD = {"spatial_nearest", "nearest_neighbor", "filter_by_distance"}


def _extract_scoring_factors(params: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Pull the raw factor dicts out of a score_features op's params, across
    the shapes _normalize_scoring_spec (in plugins/feature_scoring.py)
    accepts: top-level "factors", or "scoring_spec.factors", or the older
    nested "scoring_spec.scoring.factors".
    """
    factors = params.get("factors")

    if not isinstance(factors, list):
        scoring_spec = params.get("scoring_spec")
        if isinstance(scoring_spec, dict):
            inner = scoring_spec.get("scoring")
            if isinstance(inner, dict) and isinstance(inner.get("factors"), list):
                factors = inner["factors"]
            elif isinstance(scoring_spec.get("factors"), list):
                factors = scoring_spec["factors"]

    if not isinstance(factors, list):
        return []

    return [factor for factor in factors if isinstance(factor, dict)]


def _extract_scoring_factor_fields(params: dict[str, Any]) -> list[str]:
    """
    Pull the "field" name out of every factor a score_features op's
    params declare - see _extract_scoring_factors for the shapes this
    covers.
    """
    return [
        str(factor["field"])
        for factor in _extract_scoring_factors(params)
        if factor.get("field")
    ]


def _op_declared_output_fields(op: OperationSpec) -> list[str]:
    """
    Field names an operation declares it writes, from the two mechanisms
    the system currently has for naming a computed field: an explicit
    "distance_field" param (spatial_nearest/nearest_neighbor/
    filter_by_distance), or enrich_feature_properties's rule targets.

    Deliberately narrow: these are the only field-naming mechanisms an LLM
    plan can currently use, so this stays a precise signal rather than a
    guess. An op not covered here is simply not a candidate producer -
    see _validate_score_features_field_chaining's docstring for why that
    makes this check conservative rather than exhaustive.
    """
    if op.op in _NEAREST_OPS_WITH_DISTANCE_FIELD:
        field = op.params.get("distance_field")
        return [str(field)] if field else []

    if op.op == "enrich_feature_properties":
        rules = op.params.get("rules")
        if isinstance(rules, list):
            return [
                str(rule["target"])
                for rule in rules
                if isinstance(rule, dict) and rule.get("target")
            ]

    return []


def _ancestor_refs(start_ref: str, ops_by_output: dict[str, OperationSpec]) -> set[str]:
    """
    Every ref start_ref transitively depends on, by walking operation
    inputs backward. Stops at any ref that isn't a known operation output
    (an entity or an initial input), rather than erroring - this function
    only needs to answer "was this ref produced upstream of start_ref?".
    """
    seen: set[str] = set()
    stack = [start_ref]

    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        seen.add(ref)

        op = ops_by_output.get(ref)
        if op is None:
            continue

        for dependency_ref in op.inputs.values():
            if dependency_ref not in seen:
                stack.append(str(dependency_ref))

    return seen


# Kept in sync by hand with plugins/feature_scoring.py's _score_factor -
# there is no single source of truth to generate this from (unlike
# OP_CATALOG for operations), so a new factor type added there needs
# adding here too.
_VALID_SCORING_FACTOR_TYPES = {
    "boolean",
    "boolean_bonus",
    "inverse_distance",
    "risk_level",
    "inverse_level",
    "threshold",
    "condition",
    "direct",
    "numeric",
    "inverse_numeric",
}


def _validate_score_features_factor_types(spec: QuerySpec) -> None:
    """
    Catch a score_features factor with no "type" before the plan is
    returned, not when it silently scores everything 0.0.

    plugins/feature_scoring.py's _score_factor has no default for a
    missing "type" (it raises) - but that only fires at execution time,
    several steps and possibly minutes of API-billed retries away from
    the generation step that produced the bad spec. This catches the
    identical condition immediately, with the same "there is no safe
    default" reasoning, right where the LLM's output is still available
    to name in the error.
    """
    for op in spec.operations:
        if op.op != "score_features":
            continue

        for factor in _extract_scoring_factors(op.params):
            factor_type = factor.get("type")
            field_name = factor.get("field") or factor.get("name") or "<unnamed>"

            if not factor_type:
                raise LLMSpecGenerationError(
                    f"score_features (output={op.output!r}) has a factor "
                    f"for field {field_name!r} with no 'type'. There is no "
                    "safe default - every factor must specify one "
                    f"explicitly: {sorted(_VALID_SCORING_FACTOR_TYPES)!r}. "
                    "A distance field almost always wants "
                    "'inverse_distance' with 'max_distance' set."
                )

            if factor_type not in _VALID_SCORING_FACTOR_TYPES:
                raise LLMSpecGenerationError(
                    f"score_features (output={op.output!r}) has a factor "
                    f"for field {field_name!r} with unsupported type "
                    f"{factor_type!r}. Valid types: "
                    f"{sorted(_VALID_SCORING_FACTOR_TYPES)!r}."
                )


def _validate_score_features_field_chaining(spec: QuerySpec) -> None:
    """
    Catch a score_features op reading from a ref that never accumulated a
    field its own scoring factors need.

    This is the fan-out/chaining gap: an LLM asked to score against
    distance to several different amenities routinely computes each
    distance as its own operation off the SAME original vector (a fan-out)
    instead of chaining each computation onto the previous one's output
    (an accumulating chain - see the worked example in _domain_guidance).
    The result plans and executes without error - every required input
    role is present - but every factor referencing a field that landed on
    a sibling branch instead of the scored ref silently scores 0. A
    all-zero/degenerate score on every feature is exactly what that looks
    like from the outside, with no exception to point at the cause.

    Deliberately conservative to avoid false positives: only fields this
    module can trace to a specific producing operation (see
    _op_declared_output_fields) are checked. A factor field that already
    exists on the raw input data, or that this function doesn't recognize
    as a producible field, is left unvalidated rather than guessed at -
    under-catching is the safe failure mode for a check like this, not
    over-catching.
    """
    ops_by_output: dict[str, OperationSpec] = {
        op.output: op for op in spec.operations if op.output
    }

    field_producers: dict[str, list[OperationSpec]] = {}
    for op in spec.operations:
        for field in _op_declared_output_fields(op):
            field_producers.setdefault(field, []).append(op)

    for op in spec.operations:
        if op.op != "score_features":
            continue

        needed_fields = _extract_scoring_factor_fields(op.params)
        if not needed_fields:
            continue

        scored_ref = op.inputs.get("vector")
        if not scored_ref:
            continue

        ancestors = _ancestor_refs(str(scored_ref), ops_by_output)

        for field in needed_fields:
            producers = field_producers.get(field)
            if not producers:
                # Not a field this module can trace to an operation -
                # could be a pre-existing attribute on the raw data.
                continue

            if not any(producer.output in ancestors for producer in producers):
                producer_outputs = [p.output for p in producers]
                raise LLMSpecGenerationError(
                    f"score_features (output={op.output!r}) scores on field "
                    f"{field!r}, but that field is produced by "
                    f"{producer_outputs!r}, none of which is chained into "
                    f"its input {scored_ref!r} (or anything {scored_ref!r} "
                    "depends on). Each computed field must be chained onto "
                    "the previous operation's output before scoring - see "
                    "the 'scoring against MORE THAN ONE computed field' "
                    "guidance - otherwise this field is silently absent and "
                    "its factor scores 0 for every feature."
                )


_FILTER_POINTS_IN_POLYGON_IDENTITY_SIGNALS = [
    "which zone", "which polygon", "which ring", "which band",
    "which distance band", "belongs to", "falls into",
    "group by", "group points", "label each", "keep, for every",
    "for each point", "for every matched",
]


def _validate_filter_points_in_polygon_usage(spec: QuerySpec) -> None:
    """
    Catch filter_points_in_polygon used for a query that actually needs to
    know WHICH polygon/zone/ring matched.

    filter_points_in_polygon returns nothing but a boolean __in_polygon__
    membership flag - it has no way to carry the matched polygon's
    identity. A plan built from a query like "for each point, tell me
    which zone it falls into" plans and executes without error using this
    op, but the output carries no zone information at all - a silent
    failure indistinguishable from success anywhere in the pipeline.

    The _domain_guidance() prompt text alone was not enough to prevent
    this (confirmed against gpt-4o-mini repeating the exact reported
    scenario even with the guidance in place), so this is enforced the
    same way score_features field-chaining and CRS symmetry are: as a
    hard generation-time check that rejects the plan outright rather than
    only hoping the model reads the prompt correctly.

    Deliberately keyword-based and conservative on raw_query, mirroring
    the conservative posture of the other _validate_* checks in this
    module: a query that doesn't match any identity signal is left
    unvalidated rather than guessed at, so this can only ever reject a
    plan for a query that explicitly asked "which zone" (or a synonym)
    and got a boolean-only op in response.
    """
    query_lower = spec.raw_query.lower()

    needs_zone_identity = any(
        signal in query_lower for signal in _FILTER_POINTS_IN_POLYGON_IDENTITY_SIGNALS
    )

    if not needs_zone_identity:
        return

    uses_filter = any(op.op == "filter_points_in_polygon" for op in spec.operations)
    uses_spatial_join = any(op.op == "spatial_join" for op in spec.operations)

    if uses_filter and not uses_spatial_join:
        raise LLMSpecGenerationError(
            "Plan uses filter_points_in_polygon, but the query asks to know "
            "WHICH specific zone/polygon/ring each point belongs to - "
            "filter_points_in_polygon only returns a boolean membership flag "
            "and cannot provide this. Use spatial_join with "
            "include_target_properties=true instead."
        )


# Mirrors plugins/spatial_query_filter.py::VALID_OPERATORS (not imported:
# planning doesn't import plugin modules). tests/test_op_param_shapes.py
# asserts the two sets stay equal.
_FILTER_WHERE_OPERATORS = frozenset({
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains",
    "startswith", "endswith", "regex", "exists", "is_null", "between",
})

# Mirrors plugins/feature_enrichment.py::_apply_transform's accepted names.
_ENRICH_RULE_TRANSFORMS = frozenset({
    "float", "number", "int", "integer", "str", "string",
    "bool", "boolean", "lower", "lowercase", "upper", "uppercase",
})

_FILTER_WHERE_SHAPE_HINT = (
    'Expected a JSON object such as {"field": "amenity", "op": "eq", "value": "hospital"}, '
    'the shortcut {"amenity": "hospital"} / {"beds": {"gte": 100}}, or '
    '{"and": [...]} / {"or": [...]} / {"not": {...}}.'
)

_ENRICH_RULES_SHAPE_HINT = (
    'Expected a non-empty list like [{"target": "distance_to_metro", "source": "distance", '
    '"transform": "float"}, {"target": "flood_risk", "value": "low"}].'
)


def _where_shape_error(where: Any, path: str) -> str | None:
    """
    Structural check of a filter_features ``where`` value, following the
    same dispatch order as plugins/spatial_query_filter.py::_eval_where
    (and/or/not, then canonical field form, then shortcut form). Unlike
    _eval_where, which short-circuits on the data, this walks every branch.
    Returns a message for the first problem found, or None.
    """
    if where is None:
        return None
    if not isinstance(where, dict):
        return f"{path} must be a JSON object or null, got {type(where).__name__} {where!r}."
    if "and" in where or "or" in where:
        key = "and" if "and" in where else "or"
        items = where[key]
        if not isinstance(items, list):
            return f"{path}.{key} must be a list of conditions."
        for idx, item in enumerate(items):
            error = _where_shape_error(item, f"{path}.{key}[{idx}]")
            if error:
                return error
        return None
    if "not" in where:
        return _where_shape_error(where["not"], f"{path}.not")
    if "field" in where:
        field_name = where.get("field")
        if not isinstance(field_name, str) or not field_name.strip():
            return f"{path}.field must be a non-empty property name."
        operator = where.get("op", "eq")
        if not isinstance(operator, str) or operator.strip().lower() not in _FILTER_WHERE_OPERATORS:
            return (
                f"{path}.op {operator!r} is not a supported operator "
                f"({', '.join(sorted(_FILTER_WHERE_OPERATORS))})."
            )
        return None
    if not where:
        return None
    for field_name, expected in where.items():
        if field_name in {"op", "value"}:
            continue
        if isinstance(expected, dict):
            for operator in expected:
                if str(operator).strip().lower() not in _FILTER_WHERE_OPERATORS:
                    return (
                        f"{path}.{field_name} uses unsupported operator {operator!r} "
                        f"({', '.join(sorted(_FILTER_WHERE_OPERATORS))})."
                    )
    return None


def _enrich_rules_shape_error(rules: Any) -> str | None:
    if not isinstance(rules, list) or not rules:
        return f"rules must be a non-empty list of rule objects, got {rules!r}."
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return f"rules[{idx}] must be an object, got {rule!r}."
        target = rule.get("target")
        if not isinstance(target, str) or not target.strip():
            return f'rules[{idx}] needs a non-empty "target" (the property to write), got {rule!r}.'
        transform = rule.get("transform")
        if transform and str(transform).strip().lower() not in _ENRICH_RULE_TRANSFORMS:
            return (
                f"rules[{idx}].transform {transform!r} is not supported "
                f"({', '.join(sorted(_ENRICH_RULE_TRANSFORMS))})."
            )
    return None


def _validate_structured_param_shapes(spec: QuerySpec) -> None:
    """
    Reject a plan whose filter_attribute/sort_limit ``where`` or
    enrich_feature_properties ``rules`` has the wrong shape, at generation
    time with a clear LLMSpecGenerationError, instead of letting it fail
    at execution (``where must be a dict/object or None.`` /
    ``rules[0].target is required.``) after the whole DAG was built.

    Complements the value-shape examples _op_param_shape_reference() puts
    in the prompt: those raise the chance the model gets the shape right;
    this makes a plan that still gets it wrong fail fast and name the
    stage. Only checks shape, never field names (those depend on data the
    plan hasn't loaded yet).
    """
    for op in spec.operations:
        params = op.params or {}
        if op.op in {"filter_attribute", "sort_limit"} and "where" in params:
            error = _where_shape_error(params["where"], "where")
            if error:
                raise LLMSpecGenerationError(
                    f"Operation {op.op!r} (output {op.output!r}) has an invalid where: "
                    f"{error} {_FILTER_WHERE_SHAPE_HINT}"
                )
        if op.op == "enrich_feature_properties" and "rules" in params:
            error = _enrich_rules_shape_error(params["rules"])
            if error:
                raise LLMSpecGenerationError(
                    f"Operation 'enrich_feature_properties' (output {op.output!r}) has invalid "
                    f"rules: {error} {_ENRICH_RULES_SHAPE_HINT}"
                )


# The two vector-bearing input roles for each distance/nearest-neighbor
# operation, in (this-layer, other-layer) order - used to check that both
# were reprojected to the same CRS, not just one of them.
_DISTANCE_OPS_VECTOR_ROLES = {
    "spatial_nearest": ("source", "target"),
    "nearest_neighbor": ("source", "target"),
    "filter_by_distance": ("vector", "reference"),
    "distance_to": ("vector", "target"),
}


def _crs_transform_target_crs(
    ref: str, ops_by_output: dict[str, OperationSpec]
) -> str | None:
    """
    The target_crs a ref was reprojected to - either directly (ref is the
    output of a crs_transform operation), or inherited by walking back
    through a chain of distance/nearest-neighbor operations on their
    source side, since those operations don't reproject and so preserve
    whatever CRS their source input was already in.

    That chain-walk matters for exactly the case
    accessibility_query_spec.py's own multi-amenity chain produces (and
    any equivalent LLM-generated chain): only the FIRST spatial_nearest
    step's source is directly a crs_transform output -
    "sites_metric" -> spatial_nearest(metro) -> "sites_with_metro" ->
    spatial_nearest(schools, source="sites_with_metro") -> ... - every
    later step's source is the previous step's output, not a
    crs_transform output. An earlier version of this function checked
    only the literal ref and treated every one of those later steps as
    "not traceable", which made this validator raise on a plan that was
    completely correct - caught by testing this exact function against
    accessibility_query_spec.py's own generated chain, not by inspection.

    Still deliberately narrow on the OTHER side, though: this only walks
    through the vector-preserving "source" role of a chain of distance
    ops, never through a join, a filter combining two layers, or any
    other operation that doesn't obviously preserve a single input's CRS -
    those remain untraceable (None), which is the conservative direction
    to be wrong in.
    """
    op = ops_by_output.get(ref)
    if op is None:
        return None

    if op.op == "crs_transform":
        target_crs = op.params.get("target_crs")
        return str(target_crs) if target_crs else None

    roles = _DISTANCE_OPS_VECTOR_ROLES.get(op.op)
    if roles is not None:
        this_role, _other_role = roles
        upstream_ref = op.inputs.get(this_role)
        if upstream_ref:
            return _crs_transform_target_crs(str(upstream_ref), ops_by_output)

    return None


def _validate_distance_op_crs_symmetry(spec: QuerySpec) -> None:
    """
    Catch a distance/nearest-neighbor operation whose two vector inputs
    were reprojected asymmetrically - one just-reprojected via
    crs_transform, the other not (or reprojected to a different CRS).

    Distance here is computed from raw geometry coordinates with zero CRS
    awareness (see calculate_distances/find_nearest_neighbors). Reprojecting
    only the source/site layer and leaving the target/amenity layer in its
    original CRS - typically EPSG:4326 from the source upload - produces a
    "distance" between two points in different CRSs: a large, consistent,
    completely bogus number, not an error, because nothing before this
    check ever compared the two layers' actual CRSs.

    Deliberately conservative, same posture as
    _validate_score_features_field_chaining: only fires when this module
    can see BOTH refs' immediate CRS provenance disagree (see
    _crs_transform_target_crs's docstring for exactly what "immediate"
    means here). A ref this module can't trace to a crs_transform output
    at all - already in a metric CRS from its source, for instance - is
    left unvalidated rather than guessed at.
    """
    ops_by_output: dict[str, OperationSpec] = {
        op.output: op for op in spec.operations if op.output
    }

    for op in spec.operations:
        roles = _DISTANCE_OPS_VECTOR_ROLES.get(op.op)
        if roles is None:
            continue

        this_role, other_role = roles
        this_ref = op.inputs.get(this_role)
        other_ref = op.inputs.get(other_role)
        if not this_ref or not other_ref:
            continue

        this_crs = _crs_transform_target_crs(str(this_ref), ops_by_output)
        other_crs = _crs_transform_target_crs(str(other_ref), ops_by_output)

        if this_crs is None and other_crs is None:
            continue

        if this_crs == other_crs:
            continue

        raise LLMSpecGenerationError(
            f"{op.op!r} (output={op.output!r}) reads {this_role}={this_ref!r} "
            f"(reprojected to {this_crs!r}) and {other_role}={other_ref!r} "
            f"(reprojected to {other_crs!r}) - these do not match. Every "
            "layer feeding a distance/nearest-neighbor operation must be "
            "reprojected with crs_transform to the SAME target CRS before "
            "the operation runs, or the resulting distance is computed "
            "between points in two different CRSs and is meaningless. See "
            "the 'any distance/nearest-neighbor operation needs BOTH of "
            "its vector inputs reprojected' guidance."
        )


_CRS_PARAM_NAMES = ("source_crs", "target_crs")


def _crs_param_resolution_error(value: Any) -> str | None:
    """
    None if value resolves to a CRS with pyproj AFTER the same
    normalization crs_transform applies before executing it
    (plugins/crs_transformer.py::_normalize_crs: an integer or all-digit
    value becomes "EPSG:<n>"; a string is stripped, upper-cased and has
    every space removed) - else the reason. Validating the raw value
    instead would pass a PROJ string like "+proj=utm +zone=35 ..." that
    pyproj accepts but crs_transform turns into the unresolvable
    "+PROJ=UTM+ZONE=35...", reopening the execution-time failure this
    check exists to prevent.
    """
    from pyproj import CRS

    if isinstance(value, bool):
        return "a boolean is not a CRS"
    if isinstance(value, int):
        normalized = f"EPSG:{value}"
    elif isinstance(value, str):
        normalized = value.strip().upper().replace(" ", "")
        if normalized.isdigit():
            normalized = f"EPSG:{normalized}"
    else:
        return f"expected a CRS string or EPSG integer, got {type(value).__name__}"

    try:
        CRS.from_user_input(normalized)
    except Exception as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        if normalized != value:
            try:
                CRS.from_user_input(value)
            except Exception:
                pass
            else:
                return (
                    f"crs_transform normalizes it to {normalized!r} (upper-cased, "
                    "spaces removed), which does not resolve - use an authority "
                    "code such as EPSG:<number> instead of a PROJ/WKT string"
                )
        return reason
    return None


def _validate_crs_params_resolve(
    spec: QuerySpec,
    *,
    input_data_extent: InputDataExtent | None = None,
) -> None:
    """
    Reject a plan whose source_crs/target_crs (crs_transform, the distance/
    nearest-neighbor ops, and any other op taking those params) isn't a
    CRS pyproj can resolve - a copied placeholder like "<PROJECTED_CRS>"
    or an invented "EPSG:XXXX" - at generation time, instead of one node
    into DAG execution with a raw "Invalid CRS transformation" PROJ error.

    The corrective message names the CRS computed from the query's own
    data when input_data_extent has one; otherwise it gives the UTM rule,
    never an example code (a literal example is exactly what 0.5.3's
    prompt had and the model copied).

    Skipped silently when pyproj is missing - crs_transform can't do a
    projected reprojection without it anyway, and it will report that
    itself.
    """
    try:
        import pyproj  # noqa: F401
    except ImportError:
        return

    for op in spec.operations:
        params = op.params or {}
        for param in _CRS_PARAM_NAMES:
            value = params.get(param)
            if value is None:
                continue
            reason = _crs_param_resolution_error(value)
            if reason is None:
                continue

            if input_data_extent is not None and input_data_extent.suggested_crs:
                fix = (
                    f"This query's input data (EPSG:4326 extent "
                    f"{', '.join(f'{v:.4f}' for v in input_data_extent.bbox)}) calls for "
                    f"{input_data_extent.suggested_crs} - use that value."
                )
            else:
                fix = (
                    "Use a real CRS identifier chosen for where the data actually is - "
                    "for example the UTM zone of the data's centroid: zone = "
                    "floor((lon + 180) / 6) + 1, then EPSG:(32600 + zone) north of the "
                    "equator or EPSG:(32700 + zone) south of it."
                )
            raise LLMSpecGenerationError(
                f"Operation {op.op!r} (output {op.output!r}) has {param}={value!r}, "
                f"which is not a resolvable CRS ({reason}). A placeholder or made-up "
                f"code cannot be executed. {fix}"
            )


# Ops that write a nearest-neighbor distance field and honor max_distance
# (all find_nearest_neighbors under the hood), and the property-preserving
# ops a later filter on that field can be reached through.
_MAX_DISTANCE_OPS = {"spatial_nearest", "nearest_neighbor", "filter_by_distance"}
_MAX_DISTANCE_SOURCE_ROLE = {
    "spatial_nearest": "source",
    "nearest_neighbor": "source",
    "filter_by_distance": "vector",
}
_FILTER_WHERE_OPS = {"filter_attribute", "sort_limit"}
_WHERE_OPERATOR_ALIASES = {">": "gt", ">=": "gte", "=": "eq", "==": "eq"}


def _as_threshold(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _required_where_conditions(where: Any) -> list[tuple[str, str, Any]]:
    """
    (field, operator, value) conditions every feature a where keeps must
    satisfy - mirroring filter_features' own evaluation order (and, or,
    not, canonical field condition, shortcut). Conditions under "or" or
    "not" are never required on their own, so they're left out: this can
    only under-report, never invent a condition the filter doesn't apply.
    """
    if not isinstance(where, dict):
        return []
    if "and" in where:
        items = where["and"]
        if not isinstance(items, list):
            return []
        conditions: list[tuple[str, str, Any]] = []
        for item in items:
            conditions.extend(_required_where_conditions(item))
        return conditions
    if "or" in where or "not" in where:
        return []
    if "field" in where:
        field = where.get("field")
        operator = where.get("op", "eq")
        if isinstance(field, str) and isinstance(operator, str):
            return [(field.strip(), operator.strip().lower(), where.get("value"))]
        return []

    conditions = []
    for field, expected in where.items():
        if field in {"and", "or", "not", "field", "op", "value"}:
            continue
        if isinstance(expected, dict):
            for operator, value in expected.items():
                conditions.append((str(field), str(operator).strip().lower(), value))
        else:
            conditions.append((str(field), "eq", expected))
    return conditions


def _contradicts_max_distance(operator: str, value: Any, max_distance: float) -> str | None:
    """
    A readable form of the condition if no distance <= max_distance can
    satisfy it, else None.
    """
    operator = _WHERE_OPERATOR_ALIASES.get(operator, operator)
    if operator == "between":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            lower = _as_threshold(value[0])
            if lower is not None and lower > max_distance:
                return f"between {value[0]!r} and {value[1]!r}"
        return None

    threshold = _as_threshold(value)
    if threshold is None:
        return None
    if operator == "gt" and threshold >= max_distance:
        return f"> {value!r}"
    if operator == "gte" and threshold > max_distance:
        return f">= {value!r}"
    if operator == "eq" and threshold > max_distance:
        return f"== {value!r}"
    return None


def _distance_bounds(
    conditions: list[tuple[str, str, Any]], distance_field: str
) -> tuple[list[float], list[float]]:
    """(lower bounds, upper bounds) the conditions put on distance_field."""
    lower: list[float] = []
    upper: list[float] = []
    for field, operator, value in conditions:
        if field != distance_field:
            continue
        operator = _WHERE_OPERATOR_ALIASES.get(operator, operator)
        if operator in {"gt", "gte"}:
            threshold = _as_threshold(value)
            if threshold is not None:
                lower.append(threshold)
        elif operator in {"lt", "lte"}:
            threshold = _as_threshold(value)
            if threshold is not None:
                upper.append(threshold)
        elif operator == "eq":
            threshold = _as_threshold(value)
            if threshold is not None:
                lower.append(threshold)
                upper.append(threshold)
        elif operator == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
            low, high = _as_threshold(value[0]), _as_threshold(value[1])
            if low is not None:
                lower.append(low)
            if high is not None:
                upper.append(high)
    return lower, upper


def _truncation_description(
    consumer: OperationSpec, distance_field: str, max_distance: float
) -> str | None:
    """
    How consumer asks for the FAR end of distance_field without bounding
    it at or below max_distance - "keeps ... > T with no upper limit", or
    "sorts ... descending (farthest first)" - else None.
    """
    params = consumer.params or {}
    conditions = _required_where_conditions(params.get("where"))
    lower, upper = _distance_bounds(conditions, distance_field)
    if upper and min(upper) <= max_distance:
        # An explicit band inside the cap: clearly intended.
        return None

    limit_text = (
        f"an upper limit of {min(upper)!r}, above the cap" if upper else "no upper limit"
    )
    if lower and max(lower) < max_distance:
        return f"keeps {distance_field} > {max(lower)!r} with {limit_text}"

    sort_by = params.get("sort_by")
    sort_order = str(params.get("sort_order") or "asc").strip().lower()
    if isinstance(sort_by, str) and sort_by.strip() == distance_field and sort_order == "desc":
        return f"sorts by {distance_field} descending (farthest first) with {limit_text}"
    return None


def _validate_max_distance_filter_composition(spec: QuerySpec) -> None:
    """
    Reject a plan where a nearest-neighbor step's max_distance makes a
    downstream filter on that step's own distance field unsatisfiable, or
    silently truncates it.

    max_distance drops every candidate farther than it before ranking, so
    every feature leaving the step either has distance <= max_distance or
    no distance at all. A filter that then keeps only distance > T (or
    >= T, == T, between T and ...) with T >= max_distance can never keep
    anything: the features it is looking for are exactly the ones
    max_distance removed, whether that's 1% or 90% of the data - which is
    why 0.5.4's fraction-based execution-time warning can't catch it and
    this has to look at the downstream op.

    The truncation case (enhancements/003 item 2): a cap ABOVE the
    threshold isn't empty but is still wrong for an open-ended "farther
    than T" filter (or a farthest-first sort) - the output is
    T < d <= max_distance instead of d > T, silently missing every feature
    beyond the cap, which for "underserved because the nearest facility is
    too far" are the most important ones. Allowed when the filter states
    an explicit upper bound <= max_distance (a band that is clearly
    intended), and never checked for filter_by_distance, whose whole
    purpose is the cap ("nearer than X") - only for spatial_nearest/
    nearest_neighbor, where a cap is never needed to find the nearest.

    Deliberately narrow, so it can't reject a plan that could return
    features: only follows the step's output into filter_attribute/
    sort_limit ops (directly, or through a chain of them - they preserve
    properties), stops at a later nearest-neighbor step that writes the
    same distance field (it overwrites the value), only uses conditions
    the where requires unconditionally (top level or under "and"), and
    only numeric thresholds on the exact distance field.
    """
    consumers: dict[str, list[OperationSpec]] = {}
    for op in spec.operations:
        for ref in (op.inputs or {}).values():
            if isinstance(ref, str):
                consumers.setdefault(ref, []).append(op)

    for op in spec.operations:
        if op.op not in _MAX_DISTANCE_OPS or not op.output:
            continue
        params = op.params or {}
        max_distance_key = "max_distance" if params.get("max_distance") is not None else "max_distance_m"
        max_distance = _as_threshold(params.get(max_distance_key))
        if max_distance is None:
            continue
        distance_field = str(params.get("distance_field") or "_nearest_distance").strip()

        pending = [op.output]
        seen: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            for consumer in consumers.get(ref, []):
                if consumer.op in _FILTER_WHERE_OPS and consumer.inputs.get("vector") == ref:
                    for field, operator, value in _required_where_conditions(
                        (consumer.params or {}).get("where")
                    ):
                        if field != distance_field:
                            continue
                        condition = _contradicts_max_distance(operator, value, max_distance)
                        if condition is None:
                            continue
                        raise LLMSpecGenerationError(
                            f"Operation {op.op!r} (output {op.output!r}) sets "
                            f"{max_distance_key}={params.get(max_distance_key)!r}, so every "
                            f"feature it outputs has {distance_field} <= "
                            f"{params.get(max_distance_key)!r} or no distance at all - but "
                            f"{consumer.op!r} (output {consumer.output!r}) keeps only "
                            f"{distance_field} {condition}. The two can never both hold, so "
                            "this plan always returns zero features: the features the "
                            f"filter is looking for are exactly the ones {max_distance_key} "
                            f"removed. Remove {max_distance_key} from {op.op!r} and let the "
                            "filter apply the threshold."
                        )
                    truncation = (
                        _truncation_description(consumer, distance_field, max_distance)
                        if op.op != "filter_by_distance"
                        else None
                    )
                    if truncation is not None:
                        raise LLMSpecGenerationError(
                            f"Operation {op.op!r} (output {op.output!r}) sets "
                            f"{max_distance_key}={params.get(max_distance_key)!r}, so every "
                            f"feature whose nearest target is farther than that gets no "
                            f"{distance_field} - but {consumer.op!r} (output "
                            f"{consumer.output!r}) {truncation}. The result would silently "
                            "cover only distances up to the cap and miss every feature "
                            "beyond it - the farthest ones, usually the ones the question is "
                            f"about. Remove {max_distance_key} from {op.op!r}: the filter "
                            "already applies the threshold, and finding the nearest target "
                            "never needs a cap. Only if a distance band is really intended, "
                            f"keep {max_distance_key} and add an explicit upper-bound "
                            f"condition (lt/lte) on {distance_field} to the filter, no "
                            f"greater than {max_distance_key}."
                        )
                    if consumer.output:
                        pending.append(consumer.output)
                elif (
                    consumer.op in _MAX_DISTANCE_OPS
                    and consumer.output
                    and consumer.inputs.get(_MAX_DISTANCE_SOURCE_ROLE[consumer.op]) == ref
                    and str((consumer.params or {}).get("distance_field") or "_nearest_distance").strip()
                    != distance_field
                ):
                    # A later nearest-neighbor step writing a DIFFERENT
                    # distance field preserves this one on its source side.
                    pending.append(consumer.output)


class LLMQuerySpecGenerator:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        max_repair_attempts: int = 0,
    ) -> None:
        """
        max_repair_attempts:
            How many times generate() may re-prompt the LLM after its plan
            is rejected by validation (the rejected plan and the validator's
            message are sent back, asking for a corrected plan). 0 (the
            default here, preserving the original single-call behavior)
            disables repair; s3geo.query() defaults to 1. Only a plan that
            was returned as parseable JSON and then failed validation is
            repaired - an LLM HTTP/auth error or a response with no
            parseable JSON is raised immediately.
        """
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        if isinstance(max_repair_attempts, bool) or not isinstance(max_repair_attempts, int):
            raise ValueError("max_repair_attempts must be a non-negative integer.")
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be a non-negative integer.")
        self.max_repair_attempts = max_repair_attempts
        # Audit record of the most recent generate() call - see generate().
        self.last_attempts: tuple[SpecGenerationAttempt, ...] = ()
        self.last_plan: dict[str, Any] | None = None

    def generate(
        self,
        raw_query: str,
        *,
        context: dict[str, Any] | None = None,
        system_hints: str | None = None,
        input_data_extent: InputDataExtent | None = None,
        input_layers: tuple[InputLayer, ...] | None = None,
    ) -> QuerySpec:
        """
        input_layers:
            The query's input layers as described by
            orchestrator.planning.input_layers.describe_input_layers - their
            names, kinds, bands and fields are stated in the system prompt,
            and a plan whose operations read a layer that is not among them
            (and is not an earlier operation's output) is rejected here, so
            the repair loop can fix it before execution.
        input_data_extent:
            Facts computed from the query's own input layers (see
            orchestrator.planning.input_data_extent.derive_input_data_extent)
            - rendered into the system prompt, and its suggested CRS named
            in the error if the plan's CRS params don't resolve.

        Repair: if the plan fails validation and max_repair_attempts allows
        it, the LLM is re-prompted with its rejected plan and the
        validator's message, and the new plan is validated the same way.
        If the last allowed attempt still fails, its error is raised.

        Audit: after every call (successful or not), self.last_attempts
        holds every attempt (SpecGenerationAttempt, oldest first) and
        self.last_plan the accepted plan's JSON (None on failure). A raised
        LLMSpecGenerationError carries the same via .attempts, .plan and
        .raw_response.
        """
        if not isinstance(raw_query, str) or not raw_query.strip():
            raise LLMSpecGenerationError("raw_query must be non-empty.")

        messages = build_llm_messages(
            raw_query,
            context=context,
            system_hints=system_hints,
            input_data_extent=input_data_extent,
            input_layers=input_layers,
        )

        kwargs: dict[str, Any] = {
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }

        if self.model:
            kwargs["model"] = self.model

        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        self.last_attempts = ()
        self.last_plan = None
        attempts: list[SpecGenerationAttempt] = []
        total_attempts = 1 + self.max_repair_attempts

        for number in range(1, total_attempts + 1):
            text = self.llm_client.complete(messages, **kwargs)

            try:
                data = extract_json_object(text)
            except LLMSpecGenerationError as exc:
                # No plan to repair - not something re-prompting with the
                # validator's message can fix.
                attempts.append(SpecGenerationAttempt(number, None, text, str(exc)))
                self.last_attempts = tuple(attempts)
                exc.raw_response = text
                exc.attempts = self.last_attempts
                raise

            plan = deepcopy(data)
            try:
                spec = _validated_query_spec(
                    data,
                    raw_query=raw_query,
                    context=context,
                    input_data_extent=input_data_extent,
                    input_layers=input_layers,
                )
            except LLMSpecGenerationError as exc:
                attempts.append(SpecGenerationAttempt(number, plan, text, str(exc)))
                self.last_attempts = tuple(attempts)
                if number == total_attempts:
                    exc.plan = plan
                    exc.raw_response = text
                    exc.attempts = self.last_attempts
                    raise
                messages = [
                    *messages,
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": _repair_prompt(str(exc))},
                ]
                continue

            attempts.append(SpecGenerationAttempt(number, plan, text, None))
            self.last_attempts = tuple(attempts)
            self.last_plan = plan
            return spec

        raise AssertionError("unreachable")  # pragma: no cover


def _repair_prompt(error_message: str) -> str:
    return (
        "Your previous plan was rejected by validation, before anything ran:\n"
        f"{error_message}\n"
        "Return a corrected QuerySpec JSON object for the same query that fixes "
        "exactly this problem and keeps everything else that was correct. "
        "Return only the JSON object."
    )


def _validated_query_spec(
    data: dict[str, Any],
    *,
    raw_query: str,
    context: dict[str, Any] | None,
    input_data_extent: InputDataExtent | None,
    input_layers: tuple[InputLayer, ...] | None = None,
) -> QuerySpec:
    """Parsed LLM JSON -> normalized QuerySpec, or LLMSpecGenerationError."""
    data = _pre_normalize_query_spec_json(data, context=context)

    spec = query_spec_from_dict(data, raw_query_fallback=raw_query)
    spec = normalize_llm_query_spec_for_planning(spec)
    _validate_operation_input_roles(spec)
    _validate_score_features_factor_types(spec)
    _validate_score_features_field_chaining(spec)
    _validate_crs_params_resolve(spec, input_data_extent=input_data_extent)
    _validate_distance_op_crs_symmetry(spec)
    _validate_filter_points_in_polygon_usage(spec)
    _validate_structured_param_shapes(spec)
    _validate_max_distance_filter_composition(spec)
    if input_layers:
        problems = unknown_input_refs(spec, input_layers)
        if problems:
            raise LLMSpecGenerationError("; ".join(problems))
    return spec


# ------------------------------------------------------------------ #
# Phase 8.2 Guardrail:
# Remove invalid/no-op LLM-generated enrichment nodes.
# ------------------------------------------------------------------ #

_base_normalize_llm_query_spec_for_planning = normalize_llm_query_spec_for_planning


def normalize_llm_query_spec_for_planning(spec: QuerySpec) -> QuerySpec:
    """
    Extended normalizer.

    First runs the base LLM normalizer, then removes invalid enrichment nodes
    generated by LLM, especially:

        enrich_feature_properties with missing/empty rules

    Such nodes are not executable because the plugin requires rules.
    We safely remove them and rewrite downstream references to the original input.
    """
    normalized = _base_normalize_llm_query_spec_for_planning(spec)

    repairs: list[str] = []
    ref_rewrites: dict[str, str] = {}
    cleaned_ops: list[OperationSpec] = []

    def rewrite_ref(ref: str) -> str:
        seen: set[str] = set()
        current = ref
        while current in ref_rewrites and current not in seen:
            seen.add(current)
            current = ref_rewrites[current]
        return current

    for op in normalized.operations:
        rewritten_inputs = {
            role: rewrite_ref(str(ref))
            for role, ref in op.inputs.items()
        }

        # Remove invalid enrichment nodes with no usable rules.
        if op.op == "enrich_feature_properties":
            rules = op.params.get("rules")
            has_valid_rules = isinstance(rules, list) and len(rules) > 0

            if not has_valid_rules:
                source_ref = rewritten_inputs.get("vector")

                if op.output and source_ref:
                    ref_rewrites[op.output] = source_ref
                    repairs.append(
                        "removed invalid enrich_feature_properties without rules "
                        f"and rewrote {op.output!r} -> {source_ref!r}"
                    )
                else:
                    repairs.append(
                        "removed invalid enrich_feature_properties without rules"
                    )

                continue

        cleaned_ops.append(
            OperationSpec(
                op=op.op,
                inputs=rewritten_inputs,
                params=op.params,
                output=op.output,
            )
        )

    cleaned_outputs: list[OutputSpec] = []
    for output in normalized.outputs:
        source = output.source
        if source:
            source = rewrite_ref(source)

        cleaned_outputs.append(
            OutputSpec(
                kind=output.kind,
                source=source,
                format=output.format,
                config=output.config,
            )
        )

    metadata = dict(normalized.metadata or {})

    if repairs:
        normalization = metadata.get("normalization")
        if not isinstance(normalization, dict):
            normalization = {}

        old_repairs = normalization.get("repairs")
        if not isinstance(old_repairs, list):
            old_repairs = []

        normalization["applied"] = True
        normalization["repairs"] = old_repairs + repairs
        metadata["normalization"] = normalization

    current = QuerySpec(
        raw_query=normalized.raw_query,
        goal=normalized.goal,
        entities=normalized.entities,
        operations=cleaned_ops,
        outputs=cleaned_outputs,
        source=normalized.source,
        metadata=metadata,
    )

    # Phase 9.1: auto-inject enrich_risk
    # Only after base normalization has added/validated scoring_spec.
    # Check: scoring_spec must exist AND reference risk fields.
    # Do NOT inject if default scoring spec was just added in this pass
    # (default scoring already expects risk fields to be pre-enriched).
    # Safe check: scoring_spec must have been provided by LLM explicitly.
    _llm_provided_scoring = any(
        op.op == "score_features"
        and "scoring_spec" in op.params
        and isinstance(op.params["scoring_spec"], dict)
        and any(
            f.get("field") in {"flood_risk", "earthquake_risk", "fire_risk"}
            for f in op.params["scoring_spec"].get("factors", [])
        )
        for op in current.operations
    )

    _has_enrich_risk = any(op.op == "enrich_risk" for op in current.operations)
    _was_default_scoring_added = any(
        "added default scoring_spec" in r
        for r in (current.metadata.get("normalization") or {}).get("repairs", [])
    )

    if _llm_provided_scoring and not _has_enrich_risk and not _was_default_scoring_added:
        current, risk_repairs = _inject_risk_before_scoring(current)
        if risk_repairs:
            nm = dict(current.metadata.get("normalization") or {})
            nm["applied"] = True
            nm["repairs"] = nm.get("repairs", []) + risk_repairs
            current = QuerySpec(
                raw_query=current.raw_query,
                goal=current.goal,
                entities=current.entities,
                operations=current.operations,
                outputs=current.outputs,
                source=current.source,
                metadata={**current.metadata, "normalization": nm},
            )

    # Phase 10D: auto-inject build_report + render_pdf if needed
    if _should_inject_report(current):
        current, report_repairs = _inject_report_pipeline(current)
        if report_repairs:
            nm = dict(current.metadata.get("normalization") or {})
            nm["applied"] = True
            nm["repairs"] = nm.get("repairs", []) + report_repairs
            current = QuerySpec(
                raw_query=current.raw_query,
                goal=current.goal,
                entities=current.entities,
                operations=current.operations,
                outputs=current.outputs,
                source=current.source,
                metadata={**current.metadata, "normalization": nm},
            )

    return current
