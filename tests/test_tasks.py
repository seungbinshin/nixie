"""Task routing: API first, then CLI (shell), browser last. Irreversible skills wait for the owner."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from nixie.agent import NixieAgent
from nixie.config import load_config
from nixie.tools import ToolError

FAKE_CLI = """#!/usr/bin/env python3
import json, sys
print(json.dumps({"cli": True, "argv": sys.argv[1:], "rooms": [{"id": "C-12"}]}))
"""


def make_agent(tmp: Path, services: dict, pool_url: str | None = None, shell_allow=None) -> NixieAgent:
    cfg = {
        "agent_id": "alice", "owner_name": "Alice", "owner_token": "t", "timezone": "Asia/Seoul",
        "services": services, "tools": {"shell_allow": shell_allow or []},
        "browser_pool": {"url": pool_url or "", "token": "pool-secret"},
    }
    (tmp / "agent.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return NixieAgent(load_config(tmp / "agent.yaml"))


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    exe = b / "hynix-room"
    exe.write_text(FAKE_CLI)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    return exe


async def test_api_preferred_over_cli(tmp_path, mock_hynix, fake_cli, calls):
    a = make_agent(tmp_path, {"room": {"base_url": mock_hynix.url}}, shell_allow=["hynix-room"])
    r = await a.do_task("room.search", {"date": "2026-11-18", "start": "10:00", "end": "11:00", "size": "6"})
    assert r["route"] == "api" and r["result"]["body"]["rooms"][0]["id"] == "M-301"
    assert ("GET", "/v1/rooms/available") in calls


async def test_cli_when_no_api(tmp_path, fake_cli):
    a = make_agent(tmp_path, {}, shell_allow=["hynix-room"])
    r = await a.do_task("room.search", {"date": "2026-11-18", "start": "10:00", "end": "11:00", "size": "6"})
    assert r["route"] == "cli" and '"cli": true' in r["result"]["stdout"]
    assert r["tried"] == ["api: not available"]


async def test_browser_only_when_no_api_or_cli(tmp_path, mock_hynix, browser_pool, monkeypatch):
    srv, _ = browser_pool
    monkeypatch.setenv("HYNIX_PORTAL_URL", mock_hynix.url)
    a = make_agent(tmp_path, {}, pool_url=srv.url)
    r = await a.do_task("leave.balance", {})
    assert r["route"] == "browser"
    # with an HR API configured the same skill never touches the browser
    b = make_agent(tmp_path, {"hr": {"base_url": mock_hynix.url}}, pool_url=srv.url)
    with pytest.raises(ToolError):  # mock has no /v1/leave-balance -> API error is reported, no browser fallback
        await b.do_task("leave.balance", {})


async def test_irreversible_skill_waits_for_owner(tmp_path, mock_hynix, calls):
    a = make_agent(tmp_path, {"hr": {"base_url": mock_hynix.url}})
    r = await a.do_task("leave.request", {"kind": "annual", "start": "2026-11-20", "end": "2026-11-20", "reason": "개인"})
    assert r["status"] == "pending_owner_approval"
    assert not [c for c in calls if c[1] == "/v1/leave-requests"]
    done = await a.decide(r["approval_id"], True)
    assert done["route"] == "api" and done["result"]["body"]["request_id"] == "LV-API-1"


async def test_browser_route_submits_portal_form(tmp_path, mock_hynix, browser_pool, monkeypatch, users_root):
    """No HR API: the leave form is filled in the shared browser with the user's SSO cookie."""
    srv, _ = browser_pool
    monkeypatch.setenv("HYNIX_PORTAL_URL", mock_hynix.url)
    monkeypatch.setenv("NIXIE_USER", "dave")
    a = make_agent(tmp_path, {}, pool_url=srv.url)
    await a.tasks.browser.run([{"action": "goto", "url": f"{mock_hynix.url}/sso"},
                               {"action": "click", "selector": "#sso"}, {"action": "wait_for", "selector": "#balance"}])
    r = await a.do_task("leave.request", {"kind": "half_am", "start": "2026-11-21", "end": "2026-11-21", "reason": "병원"})
    res = await a.decide(r["approval_id"], True)
    assert res["route"] == "browser"
    assert res["result"]["results"][-1]["text"].startswith("접수 완료 LV-WEB-7 (half_am")
    assert (users_root / "dave" / "browser" / "storage_state.json").is_file()


async def test_shell_and_browse_guards(tmp_path, mock_hynix, fake_cli, browser_pool):
    srv, _ = browser_pool
    a = make_agent(tmp_path, {"room": {"base_url": mock_hynix.url}}, pool_url=srv.url, shell_allow=["hynix-room"])
    with pytest.raises(ToolError):
        await a.tasks.shell.run(["sh", "-c", "id"])
    with pytest.raises(ToolError):
        await a.tasks.shell.run(["/bin/hynix-room"])
    ok = await a.tasks.shell.run(["hynix-room", "search", "; rm -rf /"])  # no shell: argument stays literal
    assert ok["exit_code"] == 0 and "; rm -rf /" in ok["stdout"]
    with pytest.raises(ToolError):  # host has an API -> browser refused
        await a.tasks.browse([{"action": "goto", "url": f"{mock_hynix.url}/hr/leave"}], reason="try")


async def test_catalog_and_persona(tmp_path, mock_hynix):
    a = make_agent(tmp_path, {"search": {"base_url": mock_hynix.url}})
    cat = {s["id"]: s for s in a.tasks.catalog()}
    assert {"room.search", "room.book", "leave.request", "approval.pending", "docs.search", "it.ticket"} <= set(cat)
    assert cat["docs.search"]["available_routes"] == ["api"] and cat["leave.request"]["needs_owner_approval"]
    sp = a.system_prompt()
    assert "SK hynix" in sp and "not a coding tool" in sp
