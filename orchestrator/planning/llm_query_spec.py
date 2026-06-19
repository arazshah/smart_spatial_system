"""
LLM-assisted QuerySpec generation.

LLM is allowed to produce:
    - QuerySpec
    - ScoringSpec inside score_features.params.scoring_spec
    - OutputSpec/report/map configuration

LLM is NOT allowed to execute plugins.
Execution always goes through:
    QuerySpec -> DeterministicPlanner -> DagExecutor -> registered capabilities
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Callable

from orchestrator.planning.op_catalog import (
    is_pending,
    is_supported,
    list_pending_ops,
    list_supported_ops,
)
from orchestrator.planning.spec import EntitySpec, OperationSpec, OutputSpec, QuerySpec


class LLMQuerySpecError(ValueError):
    pass


LLMClient = Callable[[str], str | dict[str, Any]]


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Extract JSON object from LLM output.

    Supports:
        - pure JSON
        - ```json fenced block
        - extra text around JSON
    """
    if not isinstance(text, str):
        raise LLMQuerySpecError("LLM output must be a string.")

    raw = text.strip()
    if not raw:
        raise LLMQuerySpecError("LLM output is empty.")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise LLMQuerySpecError("Top-level JSON must be an object.")
    except json.JSONDecodeError:
        pass

    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            raise LLMQuerySpecError("Top-level JSON must be an object.")
        except json.JSONDecodeError as exc:
            raise LLMQuerySpecError(f"Invalid JSON in fenced block: {exc}") from exc

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            raise LLMQuerySpecError("Top-level JSON must be an object.")
        except json.JSONDecodeError as exc:
            raise LLMQuerySpecError(f"Could not parse JSON object: {exc}") from exc

    raise LLMQuerySpecError("No JSON object found in LLM output.")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LLMQuerySpecError(f"{label} must be an object.")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LLMQuerySpecError(f"{label} must be a list.")
    return value


def _parse_entity(item: Any, index: int) -> EntitySpec:
    obj = _require_dict(item, f"entities[{index}]")

    ref = obj.get("ref")
    kind = obj.get("kind")

    if not isinstance(ref, str) or not ref.strip():
        raise LLMQuerySpecError(f"entities[{index}].ref must be a non-empty string.")
    if not isinstance(kind, str) or not kind.strip():
        raise LLMQuerySpecError(f"entities[{index}].kind must be a non-empty string.")

    binding = obj.get("binding") or {}
    hints = obj.get("hints") or {}

    if not isinstance(binding, dict):
        raise LLMQuerySpecError(f"entities[{index}].binding must be an object.")
    if not isinstance(hints, dict):
        raise LLMQuerySpecError(f"entities[{index}].hints must be an object.")

    return EntitySpec(
        ref=ref.strip(),
        kind=kind.strip(),
        binding=binding,
        hints=hints,
    )


def _parse_operation(
    item: Any,
    index: int,
    *,
    allow_pending_ops: bool,
    validate_supported_ops: bool,
) -> OperationSpec:
    obj = _require_dict(item, f"operations[{index}]")

    op = obj.get("op")
    if not isinstance(op, str) or not op.strip():
        raise LLMQuerySpecError(f"operations[{index}].op must be a non-empty string.")

    op = op.strip()

    if validate_supported_ops:
        supported = is_supported(op)
        pending = is_pending(op)

        if not supported and not (allow_pending_ops and pending):
            raise LLMQuerySpecError(
                f"Unsupported operation {op!r}. "
                f"supported={list_supported_ops()}, pending={list_pending_ops()}"
            )

    inputs = obj.get("inputs") or {}
    params = obj.get("params") or {}
    output = obj.get("output") or ""

    if not isinstance(inputs, dict):
        raise LLMQuerySpecError(f"operations[{index}].inputs must be an object.")
    if not isinstance(params, dict):
        raise LLMQuerySpecError(f"operations[{index}].params must be an object.")
    if not isinstance(output, str):
        raise LLMQuerySpecError(f"operations[{index}].output must be a string.")

    return OperationSpec(
        op=op,
        inputs={str(k): str(v) for k, v in inputs.items()},
        params=params,
        output=output.strip(),
    )


def _parse_output(item: Any, index: int) -> OutputSpec:
    obj = _require_dict(item, f"outputs[{index}]")

    kind = obj.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise LLMQuerySpecError(f"outputs[{index}].kind must be a non-empty string.")

    source = obj.get("source") or ""
    fmt = obj.get("format") or ""
    config = obj.get("config") or {}

    if not isinstance(source, str):
        raise LLMQuerySpecError(f"outputs[{index}].source must be a string.")
    if not isinstance(fmt, str):
        raise LLMQuerySpecError(f"outputs[{index}].format must be a string.")
    if not isinstance(config, dict):
        raise LLMQuerySpecError(f"outputs[{index}].config must be an object.")

    return OutputSpec(
        kind=kind.strip(),
        source=source.strip(),
        format=fmt.strip(),
        config=config,
    )


def query_spec_from_dict(
    data: dict[str, Any],
    *,
    default_raw_query: str = "",
    source: str = "llm",
    allow_pending_ops: bool = False,
    validate_supported_ops: bool = True,
) -> QuerySpec:
    obj = _require_dict(data, "QuerySpec JSON")

    raw_query = obj.get("raw_query") or default_raw_query
    goal = obj.get("goal") or ""

    if not isinstance(raw_query, str):
        raise LLMQuerySpecError("raw_query must be a string.")
    if not isinstance(goal, str) or not goal.strip():
        raise LLMQuerySpecError("goal must be a non-empty string.")

    entities = [
        _parse_entity(item, idx)
        for idx, item in enumerate(_require_list(obj.get("entities"), "entities"))
    ]

    operations = [
        _parse_operation(
            item,
            idx,
            allow_pending_ops=allow_pending_ops,
            validate_supported_ops=validate_supported_ops,
        )
        for idx, item in enumerate(_require_list(obj.get("operations"), "operations"))
    ]

    outputs = [
        _parse_output(item, idx)
        for idx, item in enumerate(_require_list(obj.get("outputs"), "outputs"))
    ]

    metadata = obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise LLMQuerySpecError("metadata must be an object.")

    return QuerySpec(
        raw_query=raw_query,
        goal=goal.strip(),
        entities=entities,
        operations=operations,
        outputs=outputs,
        source=source,
        metadata=metadata,
    )


def query_spec_to_dict(spec: QuerySpec) -> dict[str, Any]:
    return asdict(spec)


class QuerySpecPromptBuilder:
    """
    Builds strict prompt for converting natural language to QuerySpec JSON.
    """

    def build(
        self,
        user_query: str,
        *,
        context: dict[str, Any] | None = None,
        executable_only: bool = True,
    ) -> str:
        if not isinstance(user_query, str) or not user_query.strip():
            raise LLMQuerySpecError("user_query must be a non-empty string.")

        supported_ops = list_supported_ops()
        pending_ops = list_pending_ops()

        mode_instruction = (
            "Use ONLY supported operations."
            if executable_only
            else "Prefer supported operations. Pending operations are allowed only when unavoidable."
        )

        example = {
            "raw_query": user_query,
            "goal": "rank_real_estate_investment_options",
            "entities": [
                {
                    "ref": "properties",
                    "kind": "vector",
                    "binding": {"source": "project_layer_or_upload"},
                    "hints": {"geometry": "Point", "description": "candidate properties"},
                },
                {
                    "ref": "poi",
                    "kind": "vector",
                    "binding": {"source": "database_or_loaded_layer"},
                    "hints": {"categories": ["metro", "mall"]},
                },
                {
                    "ref": "buildable_zone",
                    "kind": "vector",
                    "binding": {"source": "land_use_layer"},
                    "hints": {"geometry": "Polygon"},
                },
            ],
            "operations": [
                {
                    "op": "filter_by_distance",
                    "inputs": {"vector": "properties", "reference": "poi"},
                    "params": {"max_distance_m": 500, "k": 1, "drop_unmatched": True},
                    "output": "near_poi_properties",
                },
                {
                    "op": "filter_points_in_polygon",
                    "inputs": {"vector": "near_poi_properties", "polygon": "buildable_zone"},
                    "params": {"predicate": "within", "drop_outside": True},
                    "output": "buildable_properties",
                },
                {
                    "op": "score_features",
                    "inputs": {"vector": "buildable_properties"},
                    "params": {
                        "scoring_spec": {
                            "output_field": "investment_score",
                            "scale": 100,
                            "factors": [
                                {
                                    "name": "inside_buildable_zone",
                                    "field": "__in_polygon__",
                                    "type": "boolean",
                                    "weight": 0.4,
                                }
                            ],
                        }
                    },
                    "output": "scored_properties",
                },
                {
                    "op": "rank_features",
                    "inputs": {"vector": "scored_properties"},
                    "params": {
                        "score_field": "investment_score",
                        "rank_field": "investment_rank",
                    },
                    "output": "ranked_properties",
                },
            ],
            "outputs": [
                {
                    "kind": "vector",
                    "source": "ranked_properties",
                    "format": "",
                    "config": {
                        "map_style": {
                            "color_by": "investment_score",
                            "label_field": "investment_rank",
                        }
                    },
                }
            ],
            "metadata": {
                "language": "fa",
                "requires_user_confirmation": False,
            },
        }

        return f"""
You are a GIS planning assistant.

Task:
Convert the user's natural-language spatial analysis request into a strict JSON QuerySpec.

Rules:
- Return JSON only. No markdown. No explanation.
- {mode_instruction}
- Do not execute tools or plugins.
- Do not invent plugin results.
- LLM can infer intent, scoring logic, report/map styling and output configuration.
- Put user-requested styling/report/table/map requirements into outputs[].config.
- Put decision scoring into score_features.params.scoring_spec.
- For "nearer than X meters", use filter_by_distance with max_distance_m, k=1, drop_unmatched=true.
- For "inside allowed/buildable area", use filter_points_in_polygon.
- For final ranking, use score_features then rank_features.
- If a needed operation is not supported, do not invent a new executable operation unless pending operations are allowed.
- If risk data is described but no executable risk enrichment is available, put risk assumptions in metadata or scoring only if fields are expected to exist.

Supported operations:
{_json_dumps(supported_ops)}

Pending operations:
{_json_dumps(pending_ops)}

Available context/data hints:
{_json_dumps(context or {})}

Required JSON shape example:
{_json_dumps(example)}

User query:
{user_query}
""".strip()


class LLMQuerySpecGenerator:
    """
    Generates QuerySpec using an injected LLM client.

    llm_client signature:
        client(prompt: str) -> str | dict
    """

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt_builder: QuerySpecPromptBuilder | None = None,
        allow_pending_ops: bool = False,
        validate_supported_ops: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder or QuerySpecPromptBuilder()
        self.allow_pending_ops = allow_pending_ops
        self.validate_supported_ops = validate_supported_ops

    def generate(
        self,
        user_query: str,
        *,
        context: dict[str, Any] | None = None,
        executable_only: bool = True,
    ) -> QuerySpec:
        prompt = self.prompt_builder.build(
            user_query,
            context=context,
            executable_only=executable_only,
        )

        llm_output = self.llm_client(prompt)

        if isinstance(llm_output, dict):
            data = llm_output
        elif isinstance(llm_output, str):
            data = extract_json_object(llm_output)
        else:
            raise LLMQuerySpecError("LLM client must return str or dict.")

        return query_spec_from_dict(
            data,
            default_raw_query=user_query,
            source="llm",
            allow_pending_ops=self.allow_pending_ops,
            validate_supported_ops=self.validate_supported_ops,
        )
