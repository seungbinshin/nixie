"""Model routing: fast by default, in-house pro model (Kimi K3.1 Pro / DeepSeek V4.1 Flash class) only when needed."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml

from nixie.agent import NixieAgent
from nixie.config import load_config
from nixie.models import ModelRouter


def cfg_file(tmp, max_tier="pro", search_url=""):
    raw = {
        "agent_id": "alice", "owner_name": "Alice", "owner_token": "t",
        "models": {
            "fast": {"base_url": "http://fast.llm/v1", "model": "small-ko"},
            "pro": {"base_url": "http://pro.llm/v1", "model": "kimi-k3.1-pro", "extra": {"reasoning_effort": "high"}},
            "max_tier": max_tier, "escalate_after_tool_steps": 3, "long_context_chars": 2000,
        },
        "services": {"search": {"base_url": search_url}} if search_url else {},
    }
    p = tmp / "agent.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(p)


class Recorder:
    def __init__(self, script=None, fail_fast=False):
        self.calls: list[tuple[str, dict]] = []
        self.script = script or (lambda host, body: {"role": "assistant", "content": f"from {host}"})
        self.fail_fast = fail_fast

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        host = request.url.host
        self.calls.append((host, body))
        if self.fail_fast and host == "fast.llm":
            return httpx.Response(500, json={"error": "overloaded"})
        return httpx.Response(200, json={"choices": [{"message": self.script(host, body)}]})


async def test_fast_by_default_pro_when_needed(tmp_path):
    rec = Recorder()
    r = ModelRouter(cfg_file(tmp_path).models, httpx.MockTransport(rec))
    await r.chat([{"role": "user", "content": "내일 회의실 잡아줘"}])
    await r.chat([{"role": "user", "content": "x" * 3000}])                 # long context
    await r.chat([{"role": "user", "content": "보고서"}], kind="synthesis")  # heavy reading/writing
    await r.chat([{"role": "user", "content": "q"}], step=3)                 # long tool loop
    assert [h for h, _ in rec.calls] == ["fast.llm", "pro.llm", "pro.llm", "pro.llm"]
    assert rec.calls[1][1]["model"] == "kimi-k3.1-pro" and rec.calls[1][1]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in rec.calls[0][1]
    assert r.usage == {"fast": 1, "pro": 3}


async def test_escalate_on_failure_and_cap(tmp_path):
    rec = Recorder(fail_fast=True)
    r = ModelRouter(cfg_file(tmp_path).models, httpx.MockTransport(rec))
    msg = await r.chat([{"role": "user", "content": "hi"}])
    assert msg["content"] == "from pro.llm" and [h for h, _ in rec.calls] == ["fast.llm", "pro.llm"]

    capped = ModelRouter(cfg_file(tmp_path, max_tier="fast").models, httpx.MockTransport(Recorder()))
    assert capped.available() == ["fast"]
    assert capped.pick([{"role": "user", "content": "x"}], kind="synthesis") == "fast"
    with pytest.raises(RuntimeError):
        await ModelRouter(cfg_file(tmp_path, max_tier="fast").models,
                          httpx.MockTransport(Recorder(fail_fast=True))).chat([{"role": "user", "content": "x"}])


async def test_owner_chat_escalates_after_pro_hinted_skill(tmp_path):
    def script(host, body):
        last = body["messages"][-1]
        if last["role"] == "user":
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {
                "name": "do_task", "arguments": json.dumps({"skill": "docs.search", "params": {"query": "출장비 규정"}})}}]}
        return {"role": "assistant", "content": f"요약({host})"}

    rec = Recorder(script)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.local":
            return httpx.Response(200, json={"hits": [{"title": "출장 규정 v3"}]})
        return rec(request)

    cfg = cfg_file(tmp_path, search_url="http://search.local")
    t = httpx.MockTransport(handler)
    agent = NixieAgent(cfg, llm=ModelRouter(cfg.models, t), tool_transport=t)
    out = await agent.owner_chat("출장비 규정 찾아서 요약해줘")
    assert out == "요약(pro.llm)"
    assert [h for h, _ in rec.calls] == ["fast.llm", "pro.llm"]
