"""OpenAI-compatible chat client (works with vLLM, Ollama, LiteLLM, sLLM gateways, OpenAI)."""

from __future__ import annotations

from typing import Any

import httpx

from .config import LLMSettings


class LLM:
    def __init__(self, settings: LLMSettings, transport: httpx.AsyncBaseTransport | None = None):
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
        body: dict[str, Any] = {"model": self.settings.model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
