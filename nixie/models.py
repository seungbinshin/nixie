"""Model router: use the light model by default, escalate to the in-house high-performance model only when needed.

Tiers (weakest -> strongest): fast, pro.
- fast: small in-house model (any OpenAI-compatible endpoint).
- pro:  in-house high-performance model, up to Kimi K3.1 Pro or DeepSeek V4.1 Flash class.
`max_tier` caps the strongest tier the agent may use.

Escalate to pro when:
- the caller asks for it (kind in PRO_KINDS, or hint="pro", e.g. a skill with model_hint: pro),
- the conversation is long (more than long_context_chars),
- the tool loop needs many steps (step >= escalate_after_tool_steps),
- the fast tier fails (HTTP error or a malformed tool call).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import ModelSettings
from .llm import LLM

log = logging.getLogger("nixie.models")

ORDER = ["fast", "pro"]
PRO_KINDS = {"synthesis", "report"}


class ModelRouter:
    def __init__(self, settings: ModelSettings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._clients = {n: LLM(t, transport) for n, t in settings.tiers.items()}
        self.usage: dict[str, int] = {n: 0 for n in ORDER}
        self.last_tier: str | None = None

    def available(self) -> list[str]:
        cap = ORDER.index(self.settings.max_tier) if self.settings.max_tier in ORDER else len(ORDER) - 1
        return [n for n in ORDER[: cap + 1] if n in self.settings.tiers and self.settings.tiers[n].enabled]

    @property
    def enabled(self) -> bool:
        return bool(self.available())

    def pick(self, messages: list[dict[str, Any]], *, kind: str = "chat", step: int = 0, hint: str | None = None) -> str:
        avail = self.available()
        if not avail:
            raise RuntimeError("no model tier configured")
        want = "fast"
        size = sum(len(str(m.get("content") or "")) for m in messages)
        if hint == "pro" or kind in PRO_KINDS or size > self.settings.long_context_chars \
                or step >= self.settings.escalate_after_tool_steps:
            want = "pro"
        # strongest available tier that is not above `want`; else the weakest available one
        below = [n for n in avail if ORDER.index(n) <= ORDER.index(want)]
        return below[-1] if below else avail[0]

    def _stronger(self, tier: str) -> str | None:
        for n in self.available():
            if ORDER.index(n) > ORDER.index(tier):
                return n
        return None

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   kind: str = "chat", step: int = 0, hint: str | None = None) -> dict[str, Any]:
        tier: str | None = self.pick(messages, kind=kind, step=step, hint=hint)
        last_err: Exception | None = None
        while tier:
            try:
                msg = await self._clients[tier].chat(messages, tools)
                for c in msg.get("tool_calls") or []:
                    json.loads(c["function"].get("arguments") or "{}")  # malformed -> escalate
                self.usage[tier] += 1
                self.last_tier = tier
                return msg
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last_err = e
                nxt = self._stronger(tier)
                log.warning("tier %s failed (%s); escalate to %s", tier, e, nxt)
                tier = nxt
        raise RuntimeError(f"all model tiers failed: {last_err}")
