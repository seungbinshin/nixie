"""On-demand sandboxes: only the user's disk stays; the agent starts on request and stops when idle."""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml

from nixie.gateway import create_gateway, provision_user
from nixie.sandbox import ContainerBackend, ProcessBackend, SandboxManager
from util import ThreadServer, free_port, get, post, wait_until

ADMIN = {"X-Admin-Token": "adm"}


def user_yaml(user: str, gw: str, peers: list[str], busy: list) -> str:
    return yaml.safe_dump({
        "agent_id": user, "owner_name": user.capitalize(), "owner_token": f"{user}-owner",
        "public_url": f"{gw}/u/{user}", "timezone": "Asia/Seoul", "busy": busy,
        "peers": {p: {"url": f"{gw}/u/{p}", "token": "shared", "scopes": ["availability", "meeting"]} for p in peers},
    })


def status(gw):
    return get(f"{gw}/admin/sandboxes", headers=ADMIN).json()


def test_wake_on_request_stop_when_idle_disk_persists(tmp_path):
    port = free_port()
    gw = f"http://127.0.0.1:{port}"
    provision_user(tmp_path, "alice", user_yaml("alice", gw, ["bob"], [{"start": "2026-11-17T09:00", "end": "2026-11-17T11:00"}]))
    provision_user(tmp_path, "bob", user_yaml("bob", gw, ["alice"], [{"start": "2026-11-17T11:00", "end": "2026-11-17T13:00"}]))
    mgr = SandboxManager(ProcessBackend(), tmp_path, idle_seconds=2)
    with ThreadServer(create_gateway(mgr, admin_token="adm", reap_interval=0.3), port=port):
        # discovery works without starting anything
        card = get(f"{gw}/u/bob/.well-known/agent-card.json").json()
        assert card["supportedInterfaces"][0]["url"] == f"{gw}/u/bob/a2a/jsonrpc"
        assert status(gw)["running"] == {}

        # alice's request wakes alice; alice's A2A call wakes bob through the gateway
        r = post(f"{gw}/u/alice/owner/schedule", headers={"X-Owner-Token": "alice-owner"},
                 json={"peer": "bob", "title": "Lunch", "duration_min": 60,
                       "window_start": "2026-11-17T09:00", "window_end": "2026-11-17T18:00"})
        assert r.status_code == 200 and r.json()["status"] == "pending_owner_approval", r.text
        assert set(status(gw)["running"]) == {"alice", "bob"}

        # both go idle -> both sandboxes stop
        wait_until(lambda: status(gw)["running"] == {}, timeout=15)

        # bob's pending request survived on disk; reading it wakes bob again
        pend = get(f"{gw}/u/bob/owner/approvals", headers={"X-Owner-Token": "bob-owner"}).json()
        assert [p["status"] for p in pend] == ["pending"]
        assert status(gw)["starts"]["bob"] == 2

        # bob approves -> bob's agent tells alice's agent -> alice wakes and books
        d = post(f"{gw}/u/bob/owner/approvals/{pend[0]['id']}/approve", headers={"X-Owner-Token": "bob-owner"}).json()
        assert d["status"] == "approved"
        ev = get(f"{gw}/u/alice/owner/events", headers={"X-Owner-Token": "alice-owner"}).json()
        assert len(ev) == 1 and ev[0]["start"] == "2026-11-17T04:00:00Z"
        conv = get(f"{gw}/u/alice/owner/conversations/{r.json()['conversation_id']}",
                   headers={"X-Owner-Token": "alice-owner"}).json()
        assert len(conv["lines"]) == 6 and conv["integrity_ok"]

        # only disk remains between requests
        assert (tmp_path / "alice" / "data" / "nixie.db").is_file()
        assert get(f"{gw}/u/nobody/owner/status").status_code == 401  # no user enumeration without credentials
        assert get(f"{gw}/u/nobody/owner/status", headers={"X-Owner-Token": "x"}).status_code == 404
        assert get(f"{gw}/u/..%2Fetc/owner/status").status_code == 404


def _docker_ready() -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", os.environ.get("NIXIE_TEST_IMAGE", "nixie:latest")],
                              capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _docker_ready(), reason="docker image nixie:latest not available")
async def test_container_backend(tmp_path):
    """Same lifecycle with a real container (runtime runc here; kata/firecracker use the same code path)."""
    import httpx

    home = provision_user(tmp_path, "carol", yaml.safe_dump({"agent_id": "carol", "owner_token": "c"}))
    mgr = SandboxManager(ContainerBackend(image=os.environ.get("NIXIE_TEST_IMAGE", "nixie:latest")), tmp_path,
                         idle_seconds=1)
    try:
        url = await mgr.ensure("carol")
        async with httpx.AsyncClient(trust_env=False) as c:
            r = await c.post(f"{url}/owner/memories", headers={"X-Owner-Token": "c"}, json={"text": "on disk"})
            assert r.status_code == 200
        assert await mgr.backend.alive("carol")
        import asyncio
        await asyncio.sleep(1.5)
        assert await mgr.reap_once() == ["carol"]
        assert not await mgr.backend.alive("carol")
        url = await mgr.ensure("carol")  # new container, same disk
        async with httpx.AsyncClient(trust_env=False) as c:
            mem = (await c.get(f"{url}/owner/memories", headers={"X-Owner-Token": "c"})).json()
        assert [m["text"] for m in mem] == ["on disk"]
    finally:
        await mgr.stop_all()
