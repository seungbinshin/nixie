"""OpenAI-compatible chat client (vLLM, SGLang, Ollama, LiteLLM, in-house LLM gateways, Kimi API, DeepSeek API)."""

from __future__ import annotations

from typing import Any

import httpx


class LLM:
    """One endpoint + model. `settings` needs base_url, api_key, model and optional `extra` (dict)."""

    def __init__(self, settings: Any, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.settings.base_url)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return the assistant message dict (may contain tool_calls)."""
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        body: dict[str, Any] = {"model": self.settings.model, "messages": messages}
        # Model-specific knobs come from config, e.g. reasoning_effort for Kimi K3.1 Pro / DeepSeek V4.1 Flash.
        body.update(getattr(self.settings, "extra", None) or {})
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=300, transport=self._transport) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
