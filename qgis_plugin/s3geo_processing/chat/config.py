"""Chat settings (plain Python). The user chooses provider, endpoint, model and key."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

# name -> (base URL of an OpenAI-compatible API, environment variable that usually holds the key)
PRESETS: dict[str, tuple[str, str]] = {
    "OpenAI": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "AvalAI": ("https://api.avalai.ir/v1", "AVALAI_API_KEY"),
    "OpenRouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "Anthropic (OpenAI-compatible endpoint)": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "Ollama (local)": ("http://localhost:11434/v1", ""),
    "LM Studio (local)": ("http://localhost:1234/v1", ""),
    "Custom": ("", "LLM_API_KEY"),
}


@dataclass
class ChatConfig:
    provider: str = "Custom"
    base_url: str = ""
    model: str = ""
    api_key: str = ""            # typed in the dialog (stored in QGIS settings, plain text)
    api_key_env: str = "LLM_API_KEY"   # or read from this environment variable
    temperature: float = 0.1
    max_steps: int = 12
    share_sample_rows: bool = True     # send up to 3 example rows per layer to the LLM
    server_command: str = ""           # override; empty = auto-detect smart-spatial-api
    max_input_mb: int = 50             # MCP server input limit (SMART_SPATIAL_MCP_MAX_INPUT_BYTES)
    request_timeout: int = 120
    extra: dict = field(default_factory=dict)

    def resolve_api_key(self) -> str:
        return (self.api_key or os.environ.get(self.api_key_env or "", "")
                or os.environ.get("LLM_API_KEY", "")).strip()

    def problems(self) -> list[str]:
        out = []
        if not self.base_url.strip():
            out.append("Set the API base URL in the chat settings.")
        if not self.model.strip():
            out.append("Set the model name in the chat settings.")
        local = any(h in self.base_url for h in ("localhost", "127.0.0.1", "[::1]"))
        if not self.resolve_api_key() and not local:
            out.append("No API key: type one in the settings or export "
                       f"{self.api_key_env or 'LLM_API_KEY'} before starting QGIS.")
        return out

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "extra"}
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ChatConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and k != "extra"})
