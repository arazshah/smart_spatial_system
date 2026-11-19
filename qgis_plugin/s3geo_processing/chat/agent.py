"""The chat agent: an LLM with tool calling in front of the s3geo MCP server and QGIS.

Plain Python (no Qt, no qgis imports) so it is unit-tested with fakes. Collaborators:
  llm   - .chat(messages, tools) -> assistant message dict
  mcp   - .list_tools() / .call(name, args) -> (is_error, data)   [McpBridge]
  qgis  - list_layers() / layer_info(name) / layer_geojson(name, selected_only) /
          add_layer(fc, name) -> actual name / zoom_to(name)       [QgisAdapter]

Design rule: geodata never goes through the LLM. The model refers to layers by name
({"qgis_layer": "homes"} or {"result": "nearest_1"}); this class swaps in the GeoJSON, calls the
MCP tool, adds vector results to QGIS as new layers, and gives the model only a summary.
"""

from __future__ import annotations

import difflib
import json
import threading
from collections.abc import Callable
from typing import Any

from .. import core

MAX_TOOL_OUTPUT = 6000

LLM_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "qgis_list_layers",
        "description": "List the vector layers of the open QGIS project (name, geometry, CRS, "
                       "feature count, selected count).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "qgis_layer_info",
        "description": "Fields, CRS, extent and example rows of one layer.",
        "parameters": {"type": "object", "properties": {"layer": {"type": "string"}},
                       "required": ["layer"]}}},
    {"type": "function", "function": {
        "name": "describe_tool",
        "description": "Parameter list of analysis tools from the catalog. Call this before "
                       "using a tool for the first time.",
        "parameters": {"type": "object", "properties": {
            "tools": {"type": "array", "items": {"type": "string"}}}, "required": ["tools"]}}},
    {"type": "function", "function": {
        "name": "run_tool",
        "description": "Run one analysis tool from the catalog. Layer inputs are given as "
                       "{\"qgis_layer\": \"<name>\"} (add \"selected_only\": true for the "
                       "selection) or {\"result\": \"<earlier save_as name>\"}. A vector result "
                       "is added to QGIS as a new layer and summarised. Set metric=true when "
                       "distances, buffers or areas must be in metres.",
        "parameters": {"type": "object", "properties": {
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
            "metric": {"type": "boolean", "description":
                       "reproject the layer inputs to the UTM zone first and return the result "
                       "in EPSG:4326"},
            "save_as": {"type": "string", "description": "name of the result layer"}},
            "required": ["tool", "arguments"]}}},
    {"type": "function", "function": {
        "name": "zoom_to_layer",
        "description": "Zoom the map canvas to a layer.",
        "parameters": {"type": "object", "properties": {"layer": {"type": "string"}},
                       "required": ["layer"]}}},
]

SYSTEM_PROMPT = """You are the S3Geo assistant inside QGIS. You analyse the user's map layers with \
a catalog of spatial analysis tools.

Workflow: 1) qgis_list_layers / qgis_layer_info to see what exists (never invent layer or field \
names). 2) describe_tool for tools you have not used yet. 3) run_tool. 4) Explain the result \
briefly.

Rules:
- Layers are referenced by name, never pasted. Results become new layers in QGIS; the user's own \
layers are never modified.
- For distances, buffers, areas and "within N metres" questions pass metric=true.
- A tool error message says what is wrong: fix the arguments and retry once, then explain the \
problem plainly.
- Report only what the tools returned (counts, numbers, warnings). If a result is empty say so \
and suggest why.
- Answer in the language of the user's message, in a few short sentences. Do not output GeoJSON.

Analysis tool catalog (use describe_tool for parameters):
{catalog}
"""


class ToolArgError(ValueError):
    pass


# ----------------------------------------------------------------------------- helpers


def is_fc(data: Any) -> bool:
    return isinstance(data, dict) and data.get("type") == "FeatureCollection" and isinstance(
        data.get("features"), list)


def _first_sentence(text: str, limit: int = 110) -> str:
    text = " ".join((text or "").split())
    cut = text.split(". ")[0]
    return cut if len(cut) <= limit else cut[: limit - 1] + "…"


def catalog_text(tools: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {t['name']}: {_first_sentence(t['description'])}" for t in tools)


def compact_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """A small, model-friendly view of a tool's JSON schema."""
    schema = tool.get("input_schema") or {}
    required = set(schema.get("required", []))
    params: dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        opts = [o for o in prop.get("anyOf", [prop]) if o.get("type") != "null"] or [prop]
        is_layer = any("artifact_id" in (o.get("properties") or {}) for o in opts)
        entry: dict[str, Any] = {"type": "layer" if is_layer else opts[0].get("type", "any")}
        if name in required:
            entry["required"] = True
        for o in opts:
            if "enum" in o:
                entry["enum"] = o["enum"]
            if "default" in o:
                entry["default"] = o["default"]
        desc = prop.get("description", "")
        if desc and "FeatureCollection" not in desc:
            entry["description"] = desc[:160]
        if name == "metadata":
            continue
        params[name] = entry
    return {"tool": tool["name"], "description": " ".join(tool["description"].split())[:300],
            "parameters": params}


def summarize_fc(fc: dict[str, Any], *, sample_rows: bool, max_fields: int = 14) -> dict[str, Any]:
    feats = fc.get("features", [])
    geom_types = sorted({(f.get("geometry") or {}).get("type", "None") for f in feats})
    cols: dict[str, list[Any]] = {}
    for f in feats:
        for k, v in (f.get("properties") or {}).items():
            cols.setdefault(k, []).append(v)
    fields: dict[str, Any] = {}
    for name, values in list(cols.items())[:max_fields]:
        nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) >= 0.8 * len([v for v in values if v is not None] or [0]):
            fields[name] = {"min": round(min(nums), 4), "max": round(max(nums), 4),
                            "mean": round(sum(nums) / len(nums), 4)}
        else:
            fields[name] = "text"
    out: dict[str, Any] = {"feature_count": len(feats), "geometry": geom_types, "fields": fields}
    if len(cols) > max_fields:
        out["more_fields"] = len(cols) - max_fields
    if sample_rows:
        out["sample_rows"] = [
            {k: (v if not isinstance(v, str) else v[:60]) for k, v in
             list((f.get("properties") or {}).items())[:max_fields]} for f in feats[:3]]
    meta = fc.get("metadata") or {}
    if meta.get("warnings"):
        out["warnings"] = meta["warnings"]
    if meta.get("computed_in_crs"):
        out["computed_in_crs"] = meta["computed_in_crs"]
    return out


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TOOL_OUTPUT else text[:MAX_TOOL_OUTPUT] + " …[truncated]"


def _dump(obj: Any) -> str:
    return _clip(json.dumps(obj, ensure_ascii=False, default=str))


# ----------------------------------------------------------------------------- agent


class ChatAgent:
    def __init__(self, llm, mcp, qgis, *, max_steps: int = 12, share_sample_rows: bool = True,
                 on_event: Callable[..., None] | None = None) -> None:
        self.llm = llm
        self.mcp = mcp
        self.qgis = qgis
        self.max_steps = max_steps
        self.share_sample_rows = share_sample_rows
        self.on_event = on_event or (lambda kind, **kw: None)
        self.tools = mcp.list_tools()
        self._by_name = {t["name"]: t for t in self.tools}
        self.results: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(catalog=catalog_text(self.tools))}]
        self.results.clear()

    # ------------------------------------------------------------- the loop
    def run_turn(self, user_text: str, cancel: threading.Event | None = None) -> str:
        self.messages.append({"role": "user", "content": user_text})
        for step in range(self.max_steps):
            if cancel is not None and cancel.is_set():
                return "Stopped."
            self.on_event("thinking", step=step)
            msg = self.llm.chat(self._trimmed(), LLM_TOOLS)
            calls = msg.get("tool_calls") or []
            entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                entry["tool_calls"] = calls
            self.messages.append(entry)
            if not calls:
                answer = (msg.get("content") or "").strip()
                self.on_event("answer", text=answer)
                return answer
            if msg.get("content"):
                self.on_event("note", text=msg["content"])
            for call in calls:
                if cancel is not None and cancel.is_set():
                    self._close_calls(calls)
                    return "Stopped."
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    self.on_event("tool_start", name=name, args=args)
                    result = self._dispatch(name, args)
                except Exception as exc:  # noqa: BLE001 - the model gets the error text
                    result = _dump({"error": f"{type(exc).__name__}: {exc}"})
                self.on_event("tool_end", name=name, result=result)
                self.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                      "content": result})
        msg = f"I stopped after {self.max_steps} steps without a final answer."
        self.on_event("answer", text=msg)
        return msg

    def _close_calls(self, calls: list[dict[str, Any]]) -> None:
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for c in calls:
            if c.get("id") not in answered:
                self.messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                                      "content": '{"error": "cancelled by the user"}'})

    def _trimmed(self) -> list[dict[str, Any]]:
        """Keep the context bounded: old tool outputs are replaced by a stub."""
        total = sum(len(str(m.get("content", ""))) for m in self.messages)
        if total < 90_000:
            return self.messages
        out = []
        tools_idx = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        keep = set(tools_idx[-6:])
        for i, m in enumerate(self.messages):
            if m.get("role") == "tool" and i not in keep:
                m = {**m, "content": '{"omitted": "old tool output"}'}
            out.append(m)
        return out

    # ------------------------------------------------------------- tools
    def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        if name == "qgis_list_layers":
            return _dump(self.qgis.list_layers())
        if name == "qgis_layer_info":
            info = self.qgis.layer_info(args["layer"])
            if not self.share_sample_rows:
                info.pop("sample_rows", None)
            return _dump(info)
        if name == "zoom_to_layer":
            self.qgis.zoom_to(args["layer"])
            return _dump({"ok": True})
        if name == "describe_tool":
            names = args.get("tools") or []
            if isinstance(names, str):
                names = [names]
            out = [compact_schema(self._by_name[n]) if n in self._by_name else
                   {"tool": n, "error": "unknown tool", "did_you_mean": self._suggest(n)}
                   for n in names[:6]]
            return _dump(out)
        if name == "run_tool":
            return self._run_tool(args)
        return _dump({"error": f"unknown function {name!r}"})

    def _suggest(self, name: str) -> list[str]:
        return difflib.get_close_matches(name, list(self._by_name), n=4, cutoff=0.5)

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict):
            if "qgis_layer" in value and set(value) <= {"qgis_layer", "selected_only"}:
                return self.qgis.layer_geojson(value["qgis_layer"],
                                               bool(value.get("selected_only")))
            if set(value) == {"result"}:
                try:
                    return self.results[value["result"]]
                except KeyError:
                    raise ToolArgError(f"no result named {value['result']!r}; available: "
                                       f"{sorted(self.results)}") from None
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _map_fcs(self, value: Any, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
        if is_fc(value):
            return fn(value)
        if isinstance(value, dict):
            return {k: self._map_fcs(v, fn) for k, v in value.items()}
        if isinstance(value, list):
            return [self._map_fcs(v, fn) for v in value]
        return value

    def _collect_fcs(self, value: Any, acc: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if is_fc(value):
            acc.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                self._collect_fcs(v, acc)
        elif isinstance(value, list):
            for v in value:
                self._collect_fcs(v, acc)
        return acc

    def _mcp(self, tool: str, arguments: dict[str, Any]) -> Any:
        is_err, data = self.mcp.call(tool, arguments)
        if is_err:
            raise RuntimeError(str(data)[:800])
        return data

    def _run_tool(self, args: dict[str, Any]) -> str:
        tool = args.get("tool", "")
        if tool not in self._by_name:
            return _dump({"error": f"unknown tool {tool!r}", "did_you_mean": self._suggest(tool)})
        arguments = self._resolve(args.get("arguments") or {})
        metric = bool(args.get("metric"))
        crs = None
        if metric:
            fcs = self._collect_fcs(arguments, [])
            if fcs:
                crs = core.utm_epsg(*fcs)
                arguments = self._map_fcs(arguments, lambda fc: self._mcp("transform_vector_crs", {
                    "features": fc, "source_crs": "EPSG:4326", "target_crs": crs}))
        data = self._mcp(tool, arguments)
        if crs and is_fc(data):
            meta = dict(data.get("metadata") or {})
            data = self._mcp("transform_vector_crs", {"features": data, "source_crs": crs,
                                                      "target_crs": "EPSG:4326"})
            data["metadata"] = {**meta, "computed_in_crs": crs}
        if is_fc(data):
            self._counter += 1
            save_as = (args.get("save_as") or f"{tool}_{self._counter}").strip()
            self.results[save_as] = data
            shown = self.qgis.add_layer(data, save_as) if data["features"] else save_as
            summary = summarize_fc(data, sample_rows=self.share_sample_rows)
            self.on_event("layer_added", name=shown, count=len(data["features"]))
            return _dump({"ok": True, "added_layer": shown if data["features"] else None,
                          "result_name": save_as, **summary})
        if isinstance(data, dict) and data.get("type") == "artifact":
            return _dump({"ok": True, "artifact": data})
        return _dump({"ok": True, "result": data})
