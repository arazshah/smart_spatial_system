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
from dataclasses import asdict
from typing import Any, Protocol

from orchestrator.planning.op_catalog import get_op, is_supported, list_pending_ops, list_supported_ops
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec


class LLMSpecGenerationError(ValueError):
    pass


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


def _pre_normalize_query_spec_json(data: dict[str, Any]) -> dict[str, Any]:
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

    normalized = _inject_query_database_ops_for_database_entities(normalized)

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


def _domain_guidance() -> str:
    supported = ", ".join(list_supported_ops())
    pending = ", ".join(list_pending_ops())

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

Pending operations, not executable yet:
{pending}

Important mappings:
- "nearer than X meters to POI":
  op="filter_by_distance"
  inputs={{"vector": "<source>", "reference": "<poi>"}}
  params={{"max_distance_m": X, "k": 1, "drop_unmatched": true}}

- "inside permitted/buildable polygon":
  op="filter_points_in_polygon"
  inputs={{"vector": "<points>", "polygon": "<polygon_layer>"}}
  params={{"predicate": "within", "drop_outside": true}}

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
) -> list[dict[str, str]]:
    system = _domain_guidance() + "\n" + _schema_hint()

    if isinstance(context, dict) and context.get("semantic_planning_context"):
        system += "\n" + _semantic_planning_context_guidance()

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


def _default_real_estate_scoring_spec() -> dict[str, Any]:
    """
    Default MVP scoring spec for real-estate ranking.

    This is used as a safety fallback when LLM correctly asks for scoring
    but forgets to provide scoring_spec.

    Notes:
        - Some fields may be created by previous plugins or future enrichment plugins.
        - Missing fields simply receive factor score 0 by score_features.
    """
    return {
        "output_field": "investment_score",
        "scale": 100,
        "normalize_weights": True,
        "factors": [
            {
                "name": "near_poi",
                "field": "distance_to_poi",
                "type": "inverse_distance",
                "max_distance": 500,
                "weight": 0.30,
            },
            {
                "name": "inside_buildable_zone",
                "field": "inside_buildable_zone",
                "type": "boolean",
                "weight": 0.25,
            },
            {
                "name": "near_main_road",
                "field": "distance_to_road",
                "type": "inverse_distance",
                "max_distance": 1000,
                "weight": 0.20,
            },
            {
                "name": "low_flood_risk",
                "field": "flood_risk",
                "type": "risk_level",
                "weight": 0.10,
            },
            {
                "name": "low_earthquake_risk",
                "field": "earthquake_risk",
                "type": "risk_level",
                "weight": 0.10,
            },
            {
                "name": "low_fire_risk",
                "field": "fire_risk",
                "type": "risk_level",
                "weight": 0.05,
            },
        ],
    }


def normalize_llm_query_spec_for_planning(spec: QuerySpec) -> QuerySpec:
    """
    Normalize and harden LLM-produced QuerySpec before deterministic planning.

    Why this exists:
        LLMs may produce mostly-correct specs but still add unsupported input roles
        such as:
            score_features.inputs.external_api = risk_api

        DeterministicPlanner should stay strict.
        This function repairs safe, common LLM mistakes before planning.

    What it does:
        - Keeps only supported input roles per op_catalog.
        - Adds fallback scoring_spec for score_features when missing.
        - Aligns rank_features.score_field with previous score_features output_field.
        - Records repairs in metadata["normalization"].
    """
    repairs: list[str] = []
    normalized_ops: list[OperationSpec] = []

    last_score_field: str | None = None

    for op in spec.operations:
        # Unknown operations should remain unchanged so Planner can raise a clear error.
        if not is_supported(op.op):
            normalized_ops.append(op)
            continue

        descriptor = get_op(op.op)

        allowed_roles = set(descriptor.input_map)
        clean_inputs: dict[str, str] = {}

        for role, ref in op.inputs.items():
            if role in allowed_roles:
                clean_inputs[role] = ref
            else:
                repairs.append(
                    f"removed unsupported input role {role!r} from operation {op.op!r}"
                )

        clean_params = dict(op.params)

        if op.op == "score_features":
            if "scoring_spec" not in clean_params and "factors" not in clean_params:
                clean_params["scoring_spec"] = _default_real_estate_scoring_spec()
                repairs.append("added default scoring_spec to score_features")

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

        normalized_ops.append(
            OperationSpec(
                op=op.op,
                inputs=clean_inputs,
                params=clean_params,
                output=op.output,
            )
        )

    metadata = dict(spec.metadata or {})
    if repairs:
        metadata.setdefault("normalization", {})
        normalization = metadata["normalization"]
        if isinstance(normalization, dict):
            normalization["applied"] = True
            normalization["repairs"] = repairs
        else:
            metadata["normalization"] = {
                "applied": True,
                "repairs": repairs,
            }

    return QuerySpec(
        raw_query=spec.raw_query,
        goal=spec.goal,
        entities=spec.entities,
        operations=normalized_ops,
        outputs=spec.outputs,
        source=spec.source,
        metadata=metadata,
    )



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


def normalize_llm_query_spec_for_planning(spec: QuerySpec) -> QuerySpec:
    """
    Harden and improve LLM-produced QuerySpec before deterministic planning.

    Repairs:
        - Remove unsupported input roles.
        - Add default scoring_spec when score_features misses it.
        - Add score_field/rank_field to rank_features when missing.
        - If default scoring is used, inject enrichment nodes after spatial
          operations to create stable semantic scoring fields:
              distance_to_poi
              distance_to_road
              inside_buildable_zone
    """
    repairs: list[str] = []
    normalized_ops: list[OperationSpec] = []

    used_refs: set[str] = set()
    for op in spec.operations:
        if op.output:
            used_refs.add(op.output)

    # Inject enrichment only when LLM did not provide its own scoring spec.
    # This keeps old explicit LLM specs stable, but improves weak/missing specs.
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
                clean_params["scoring_spec"] = _default_real_estate_scoring_spec()
                repairs.append("added default scoring_spec to score_features")

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

    # Find the last rank_features output.
    last_rank_output: str | None = None
    for op in spec.operations:
        if op.op == "rank_features" and op.output:
            last_rank_output = op.output

    if not last_rank_output:
        # Fallback: use the last operation output.
        for op in reversed(spec.operations):
            if op.output:
                last_rank_output = op.output
                break

    if not last_rank_output:
        return spec, []

    new_ops = list(spec.operations)

    # build_report node
    report_output = "report"
    new_ops.append(OperationSpec(
        op="build_report",
        inputs={"vector": last_rank_output},
        params={
            "score_field": "investment_score",
            "rank_field": "rank",
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



class LLMQuerySpecGenerator:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        raw_query: str,
        *,
        context: dict[str, Any] | None = None,
        system_hints: str | None = None,
    ) -> QuerySpec:
        if not isinstance(raw_query, str) or not raw_query.strip():
            raise LLMSpecGenerationError("raw_query must be non-empty.")

        messages = build_llm_messages(
            raw_query,
            context=context,
            system_hints=system_hints,
        )

        kwargs: dict[str, Any] = {
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }

        if self.model:
            kwargs["model"] = self.model

        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        text = self.llm_client.complete(messages, **kwargs)
        data = extract_json_object(text)
        data = _pre_normalize_query_spec_json(data)

        spec = query_spec_from_dict(data, raw_query_fallback=raw_query)
        return normalize_llm_query_spec_for_planning(spec)


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
