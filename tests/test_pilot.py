"""Pilot: 5 users, 5 rooms of 2 agents, SSO header at /owner, real-time stream, idempotent approval."""

from __future__ import annotations

import concurrent.futures
import json
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from nixie.gateway import GatewaySettings, create_gateway, make_login_link
from nixie.pilot import PlanError, provision
from nixie.sandbox import ProcessBackend, SandboxManager, sandbox_env
from util import ThreadServer, free_port, get, post, wait_until

USERS = ["alice", "bob", "carol", "dave", "erin"]
RING = [["alice", "bob"], ["bob", "carol"], ["carol", "dave"], ["dave", "erin"], ["erin", "alice"]]
PROXY_SECRET = "proxy-secret"
SESSION_KEY = b"k" * 32


def write_plan(path: Path, gw: str, rooms=RING) -> Path:
    plan = {"gateway_url": gw, "timezone": "Asia/Seoul",
            "users": [{"id": u, "name": u.capitalize(), "sso_user": f"{u}@corp.example"} for u in USERS],
            "rooms": rooms}
    path.write_text(yaml.safe_dump(plan), encoding="utf-8")
    return path


def sso(user: str, csrf: bool = True) -> dict[str, str]:
    h = {"X-Auth-Request-User": f"{user}@corp.example", "X-Nixie-Proxy-Secret": PROXY_SECRET}
    if csrf:
        h["X-Nixie-Request"] = "1"
    return h


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    root = tmp_path_factory.mktemp("pilot")
    port = free_port()
    gw = f"http://127.0.0.1:{port}"
    users_root = root / "users"
    out = provision(write_plan(root / "plan.yaml", gw), users_root)
    mgr = SandboxManager(ProcessBackend(), users_root, idle_seconds=3)
    settings = GatewaySettings(auth_mode="both", proxy_secret=PROXY_SECRET, session_secret=SESSION_KEY,
                               cookie_secure=False, stream_poll=0.2, stream_max_age=60, admin_token="adm")
    with ThreadServer(create_gateway(mgr, reap_interval=0.3, settings=settings), port=port):
        yield SimpleNamespace(gw=gw, root=root, users_root=users_root, mgr=mgr, out=out)


def running(p) -> set[str]:
    return set(get(f"{p.gw}/admin/sandboxes", headers={"X-Admin-Token": "adm"}).json()["running"])


class Stream:
    """Collect Server-Sent Events in a background thread."""

    def __init__(self, url: str, headers: dict[str, str]):
        self.events: list[dict] = []
        self.status: int | None = None
        self._t = threading.Thread(target=self._run, args=(url, headers), daemon=True)
        self._t.start()

    def _run(self, url, headers):
        with httpx.stream("GET", url, headers=headers, timeout=90, trust_env=False) as r:
            self.status = r.status_code
            ev: dict = {}
            for line in r.iter_lines():
                if not line:
                    if ev:
                        self.events.append(ev)
                    ev = {}
                elif line.startswith("event: "):
                    ev["event"] = line[7:]
                elif line.startswith("data: "):
                    ev["data"] = json.loads(line[6:])

    def topics(self) -> set[str]:
        return {e["data"]["topic"] for e in self.events if e.get("event") == "change"}


def test_plan_makes_five_rooms_of_two(pilot):
    assert pilot.out["rooms"] == ["alice~bob", "bob~carol", "carol~dave", "dave~erin", "alice~erin"]
    for u in USERS:
        cfg = yaml.safe_load((pilot.users_root / u / "agent.yaml").read_text())
        assert len(cfg["peers"]) == 2 and cfg["sso_user"] == f"{u}@corp.example"
        assert "${" in cfg["owner_token"]  # no secret in agent.yaml
        sec = pilot.users_root / u / "secrets.env"
        assert stat.S_IMODE(sec.stat().st_mode) == 0o600
    a = (pilot.users_root / "alice" / "secrets.env").read_text()
    b = (pilot.users_root / "bob" / "secrets.env").read_text()
    tok = [l for l in a.splitlines() if l.startswith("PEER_TOKEN_BOB=")][0].split("=", 1)[1]
    assert f"PEER_TOKEN_ALICE={tok}" in b  # one shared token per room
    # a room has at most 2 participants
    with pytest.raises(PlanError):
        provision(write_plan(pilot.root / "bad.yaml", pilot.gw, [["alice", "bob", "carol"]]), pilot.root / "x")


def test_sso_header_trusted_only_from_proxy(pilot):
    gw = pilot.gw
    me = get(f"{gw}/auth/me", headers=sso("alice")).json()
    assert me["user"] == "alice" and me["auth"] == "sso" and me["api"] == "/u/alice/owner"
    # forged header without the proxy secret (for example from another sandbox on the network)
    forged = {"X-Auth-Request-User": "alice@corp.example"}
    assert get(f"{gw}/auth/me", headers=forged).status_code == 403
    assert get(f"{gw}/u/alice/owner/me", headers=forged).status_code == 403
    # a signed-in person cannot reach another person's agent
    assert get(f"{gw}/u/bob/owner/me", headers=sso("alice")).status_code == 403
    r = get(f"{gw}/auth/me")
    assert r.status_code == 401 and r.json()["login_url"] == "/oauth2/start"
    # state change from a browser session needs the CSRF header
    assert post(f"{gw}/u/alice/owner/chat", headers=sso("alice", csrf=False), json={"text": "/help"}).status_code == 403
    assert post(f"{gw}/u/alice/owner/chat", headers=sso("alice"), json={"text": "/help"}).status_code == 200
    # only /owner, /a2a, /ingest exist under /u/<user>/
    assert get(f"{gw}/u/alice/", headers=sso("alice")).status_code == 404
    assert get(f"{gw}/u/alice/audit", headers=sso("alice")).status_code == 404


def test_sandbox_trusts_identity_only_with_its_gateway_secret(pilot, monkeypatch):
    get(f"{pilot.gw}/u/alice/owner/me", headers=sso("alice"))
    st = pilot.mgr.running["alice"]
    who = {"X-Auth-Request-User": "alice@corp.example"}
    assert get(f"{st.url}/owner/me", headers=who).status_code == 401
    assert get(f"{st.url}/owner/me", headers={**who, "X-Nixie-Gateway": "guess"}).status_code == 401
    assert get(f"{st.url}/owner/me", headers={**who, "X-Nixie-Gateway": st.secret}).status_code == 200
    assert get(f"{st.url}/owner/me", headers={"X-Auth-Request-User": "bob@corp.example",
                                               "X-Nixie-Gateway": st.secret}).status_code == 403
    # gateway secrets never reach a sandbox
    for k in ("NIXIE_ADMIN_TOKEN", "NIXIE_SESSION_SECRET", "NIXIE_PROXY_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("NIXIE_FAST_MODEL", "m")
    env = sandbox_env()
    assert "NIXIE_FAST_MODEL" in env and not {"NIXIE_ADMIN_TOKEN", "NIXIE_SESSION_SECRET", "NIXIE_PROXY_SECRET"} & set(env)


def test_rooms_notifications_realtime_and_single_approval(pilot):
    gw = pilot.gw
    s = Stream(f"{gw}/u/bob/owner/stream", sso("bob", csrf=False))
    wait_until(lambda: s.status == 200 and s.events, timeout=10)
    assert s.events[0]["event"] == "hello"

    r = post(f"{gw}/u/alice/owner/schedule", headers=sso("alice"),
             json={"peer": "bob", "title": "Lunch", "duration_min": 60,
                   "window_start": "2026-11-17T09:00", "window_end": "2026-11-17T18:00"})
    assert r.status_code == 200 and r.json()["status"] == "pending_owner_approval", r.text
    assert r.json()["start"] == "2026-11-17T00:00:00Z"  # 09:00 KST, stored as UTC

    # bob's open page hears about it through the gateway stream
    wait_until(lambda: {"room", "approval", "notification"} <= s.topics(), timeout=10)

    rooms = get(f"{gw}/u/bob/owner/rooms", headers=sso("bob")).json()
    assert [x["room_id"] for x in rooms] == ["alice~bob", "bob~carol"]  # recent activity first
    first = rooms[0]
    assert first["unread"] == 4 and first["lines"] == 4  # availability query + reply, proposal + reply
    assert [p["name"] for p in first["participants"]] == ["Bob", "Alice"]
    assert first["last"]["speaker_name"] == "Bob" and first["last"]["text"].startswith("Bob님께 승인을 요청했습니다")
    assert first["updated"].endswith("Z")
    room = get(f"{gw}/u/bob/owner/rooms/alice~bob", headers=sso("bob")).json()
    assert room["read_only"] and room["integrity_ok"]
    assert room["lines"][2]["text"] == "두 분 모두 가능한 11/17 09:00–10:00 KST에 'Lunch'을(를) 제안합니다."
    assert room["lines"][2]["data"]["start"] == "2026-11-17T00:00:00Z"  # data in UTC, text in KST
    assert post(f"{gw}/u/bob/owner/rooms/alice~bob/read", headers=sso("bob"), json={}).json()["last_read"] > 0
    assert get(f"{gw}/u/bob/owner/rooms", headers=sso("bob")).json()[0]["unread"] == 0
    assert get(f"{gw}/u/bob/owner/rooms/alice~carol", headers=sso("bob")).status_code == 404

    pend = get(f"{gw}/u/bob/owner/approvals?status=pending", headers=sso("bob")).json()
    assert len(pend) == 1 and "Alice님이 11/17 09:00–10:00 KST 'Lunch'" in pend[0]["summary"]
    notes = get(f"{gw}/u/bob/owner/notifications", headers=sso("bob")).json()
    assert notes["unread"] == 1 and notes["items"][0]["ref_id"] == pend[0]["id"]

    # two fast clicks on "approve": the decision runs once
    url = f"{gw}/u/bob/owner/approvals/{pend[0]['id']}/approve"
    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda _: post(url, headers=sso("bob")), range(2)))
    assert [x.status_code for x in res] == [200, 200]
    bodies = [x.json() for x in res]
    assert sorted(bool(b.get("duplicate")) for b in bodies) == [False, True]
    assert post(url, headers=sso("bob")).json()["duplicate"] is True
    assert post(url.replace("/approve", "/decline"), headers=sso("bob")).status_code == 409
    ev = get(f"{gw}/u/alice/owner/events", headers=sso("alice")).json()
    assert len(ev) == 1
    lines = get(f"{gw}/u/alice/owner/rooms/alice~bob", headers=sso("alice")).json()["lines"]
    assert [l["intent"] for l in lines].count("meeting.confirm") == 1
    alice_notes = get(f"{gw}/u/alice/owner/notifications", headers=sso("alice")).json()["items"]
    assert alice_notes[0]["kind"] == "meeting.confirmed"


def test_stream_does_not_keep_agent_alive(pilot):
    gw = pilot.gw
    get(f"{gw}/u/carol/owner/me", headers=sso("carol"))
    s = Stream(f"{gw}/u/carol/owner/stream", sso("carol", csrf=False))
    wait_until(lambda: s.events, timeout=10)
    wait_until(lambda: "carol" not in running(pilot), timeout=20)  # idle stop while the page is open
    starts = get(f"{gw}/admin/sandboxes", headers={"X-Admin-Token": "adm"}).json()["starts"]["carol"]
    # an adapter pushes a notification -> carol wakes, the open page gets the change
    tok = [l for l in (pilot.users_root / "carol" / "secrets.env").read_text().splitlines()
           if l.startswith("INGEST_TOKEN=")][0].split("=", 1)[1]
    item = {"title": "결재 요청: 장비 구매", "source": "approval", "external_id": "A-9",
            "source_url": "https://approval.example/doc/A-9"}
    r = post(f"{gw}/u/carol/ingest/notifications", headers={"X-Ingest-Token": tok}, json={"items": [item]})
    assert r.status_code == 200 and r.json()["results"][0]["created"]
    wait_until(lambda: "notification" in s.topics(), timeout=10)
    again = post(f"{gw}/u/carol/ingest/notifications", headers={"X-Ingest-Token": tok}, json={"items": [item]})
    assert again.json()["results"][0]["created"] is False  # same source item once
    bad = post(f"{gw}/u/carol/ingest/notifications", headers={"X-Ingest-Token": tok},
               json={"items": [{"title": "x", "source_url": "javascript:alert(1)"}]})
    assert bad.status_code == 400
    assert post(f"{gw}/u/carol/ingest/notifications", headers=sso("carol"), json={"items": []}).status_code == 401
    items = get(f"{gw}/u/carol/owner/notifications", headers=sso("carol")).json()["items"]
    assert items[0]["source_url"] == "https://approval.example/doc/A-9"
    assert get(f"{gw}/admin/sandboxes", headers={"X-Admin-Token": "adm"}).json()["starts"]["carol"] == starts + 1


def test_login_link_session_cookie_and_logout(pilot):
    gw = pilot.gw
    link = make_login_link(pilot.users_root, "dave", SESSION_KEY, gw)
    with httpx.Client(trust_env=False, timeout=60) as c:
        r = c.get(link)
        assert r.status_code == 303 and "nixie_session" in r.cookies
        assert c.get(link).status_code == 401  # one-time
        me = c.get(f"{gw}/auth/me").json()
        assert me["user"] == "dave" and me["auth"] == "session"
        assert c.post(f"{gw}/u/dave/owner/chat", json={"text": "/help"}).status_code == 403  # CSRF header missing
        assert c.post(f"{gw}/u/dave/owner/chat", json={"text": "/help"}, headers={"X-Nixie-Request": "1"}).status_code == 200
        assert c.get(f"{gw}/u/erin/owner/me").status_code == 403
        hist = c.get(f"{gw}/u/dave/owner/chat").json()["messages"]
        assert [m["role"] for m in hist[-2:]] == ["owner", "agent"] and hist[-1]["ts"].endswith("Z")
        out = c.get(f"{gw}/auth/logout")
        assert out.status_code == 303 and out.headers["location"].startswith("/oauth2/sign_out")
        assert 'nixie_session=""' in out.headers["set-cookie"] and "Max-Age=0" in out.headers["set-cookie"]
        assert c.get(f"{gw}/auth/me").status_code == 401


def test_sso_mode_refuses_token_and_cookie(tmp_path):
    provision(write_plan(tmp_path / "plan.yaml", "http://gw"), tmp_path / "users")
    mgr = SandboxManager(ProcessBackend(), tmp_path / "users")
    app = create_gateway(mgr, settings=GatewaySettings(auth_mode="sso", proxy_secret=PROXY_SECRET))
    c = TestClient(app)
    token = [l for l in (tmp_path / "users" / "alice" / "secrets.env").read_text().splitlines()
             if l.startswith("OWNER_TOKEN=")][0].split("=", 1)[1]
    assert c.get("/u/alice/owner/me", headers={"X-Owner-Token": token}).status_code == 401
    assert c.post("/auth/session?user=alice", headers={"X-Owner-Token": token}).status_code == 404
    assert c.get("/auth/me", headers=sso("alice")).json()["user"] == "alice"
    with pytest.raises(ValueError):
        GatewaySettings(auth_mode="sso")  # SSO without a proxy secret is refused at start
