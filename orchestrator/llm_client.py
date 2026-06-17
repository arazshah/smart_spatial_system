"""
orchestrator.llm_client

Small OpenAI-compatible LLM client for backend-only runtime checks.

This module intentionally never exposes secrets to frontend/API responses.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMConfigError(RuntimeError):
    """Raised when LLM configuration is incomplete."""


class LLMClientError(RuntimeError):
    """Raised when the LLM provider request fails."""


def get_llm_config() -> dict[str, Any]:
    """
    Read LLM configuration from environment.

    Expected envs:
        LLM_PROVIDER=avalai
        OPENAI_BASE_URL=https://api.avalai.ir/v1
        OPENAI_API_KEY=...
        LLM_FAST_MODEL=gpt-4o-mini
        LLM_STRONG_MODEL=chatgpt4o
        LLM_DEFAULT_MODEL=gpt-4o-mini
    """
    provider = os.getenv("LLM_PROVIDER", "avalai")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("AVALAI_API_KEY")
        or os.getenv("LLM_API_KEY")
    )

    fast_model = os.getenv("LLM_FAST_MODEL") or "gpt-4o-mini"
    strong_model = os.getenv("LLM_STRONG_MODEL") or "chatgpt4o"
    default_model = os.getenv("LLM_DEFAULT_MODEL") or fast_model

    temperature_raw = os.getenv("LLM_TEMPERATURE", "0.1")
    timeout_raw = os.getenv("LLM_TIMEOUT_SECONDS", "60")

    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.1

    try:
        timeout_seconds = int(timeout_raw)
    except ValueError:
        timeout_seconds = 60

    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "fast_model": fast_model,
        "strong_model": strong_model,
        "default_model": default_model,
        "temperature": temperature,
        "timeout_seconds": timeout_seconds,
        "api_key_configured": bool(api_key),
    }


def run_llm_smoke_test(
    *,
    model: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """
    Run a tiny chat-completions request against an OpenAI-compatible provider.

    Returns non-sensitive diagnostic output.
    """
    config = get_llm_config()

    base_url = config["base_url"]
    api_key = config["api_key"]

    if not base_url:
        raise LLMConfigError("OPENAI_BASE_URL or LLM_BASE_URL is not configured.")

    if not api_key:
        raise LLMConfigError("OPENAI_API_KEY / AVALAI_API_KEY / LLM_API_KEY is not configured.")

    selected_model = model or config["fast_model"] or config["default_model"]
    if not selected_model:
        raise LLMConfigError("No LLM model is configured.")

    endpoint = base_url.rstrip("/") + "/chat/completions"

    smoke_prompt = prompt or "Return exactly this word: ok"

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a minimal backend connectivity tester.",
            },
            {
                "role": "user",
                "content": smoke_prompt,
            },
        ],
        "temperature": 0,
        "max_tokens": 8,
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
            timeout=config["timeout_seconds"],
        ) as response:
            raw = response.read().decode("utf-8")
            status_code = response.status
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

    content = ""

    try:
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "")
    except Exception:
        content = ""

    return {
        "ok": True,
        "provider": config["provider"],
        "base_url": base_url,
        "model": selected_model,
        "status_code": status_code,
        "content_preview": content[:120],
        "api_key_configured": True,
    }
