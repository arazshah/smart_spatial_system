"""Minimal OpenAI-compatible chat client (stdlib only): works with OpenAI, AvalAI, OpenRouter,
Ollama, LM Studio, vLLM and any other server that speaks /chat/completions with tool calls."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, model: str, *, temperature: float = 0.1,
                 timeout: int = 120, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json",
             "User-Agent": "s3geo-qgis"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:600]
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
                    last = exc
                    continue
                hint = {401: " (check the API key)", 403: " (key not allowed for this model/endpoint)",
                        404: " (check the base URL and the model name)"}.get(exc.code, "")
                raise LLMError(f"LLM HTTP {exc.code}{hint}: {body}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(f"Cannot reach {self.base_url}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise LLMError("The LLM endpoint did not return JSON (wrong base URL?).") from exc
        raise LLMError(f"LLM request failed: {last}")

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
             ) -> dict[str, Any]:
        """One completion; returns the assistant message dict (content, tool_calls)."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        out = self._request("POST", "/chat/completions", payload)
        try:
            return out["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {json.dumps(out)[:400]}") from exc

    def list_models(self) -> list[str]:
        out = self._request("GET", "/models")
        items = out.get("data", out) if isinstance(out, dict) else out
        return sorted({m.get("id") if isinstance(m, dict) else str(m) for m in items} - {None})
