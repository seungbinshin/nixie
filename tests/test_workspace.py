"""Agent profile (character, name, theme), concurrent requests, read-only work archive (wiki)."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import stat
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from nixie.agent import NixieAgent
from nixie.app import create_app
from nixie.config import load_config
from nixie.gateway import GatewaySettings, create_gateway
from nixie.models import ModelRouter
from nixie.profile import load_profile
from nixie.sandbox import ProcessBackend, SandboxManager


class Mesh(httpx.AsyncBaseTransport):
    """Route http://<agent>/... to that agent's ASGI app (no network)."""

    def __init__(self):
        self.apps: dict[str, object] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await httpx.ASGITransport(app=self.apps[request.url.host]).handle_async_request(request)


def make(tmp: Path, name: str, peers: list[str], mesh: Mesh | None = None, llm=None, services=None) -> NixieAgent:
    home = tmp / name
    home.mkdir(parents=True, exist_ok=True)
    cfg = {"agent_id": name, "owner_name": name.capitalize(), "owner_token": f"{name}-t", "timezone": "Asia/Seoul",
           "public_url": f"http://{name}", "services": services or {},
           "peers": {p: {"url": f"http://{p}", "token": "pair", "scopes": ["availability", "meeting"],
                         "display_name": p.capitalize()} for p in peers}}
    (home / "agent.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    agent = NixieAgent(load_config(home / "agent.yaml"), llm=llm, peer_transport=mesh)
    if mesh is not None:
        mesh.apps[name] = create_app(agent)
    return agent


# ---------------------------------------------------------------- profile
def test_profile_character_name_theme(tmp_path):
    a = make(tmp_path, "alice", [])
    c = TestClient(create_app(a))
    h = {"X-Owner-Token": "alice-t"}
    p = c.get("/owner/profile", headers=h).json()
    assert p["character"] == "hadaeng" and p["agent_name"] == "하댕이" and p["chosen"] is False
    p = c.put("/owner/profile", headers=h, json={"character": "hanyang"}).json()
    assert p["agent_name"] == "하냥이" and p["chosen"]  # default name follows the character
    p = c.put("/owner/profile", headers=h, json={"agent_name": " 콩이 ", "theme": {"accent": "#FF6B8B", "font": "jua"}}).json()
    assert p["agent_name"] == "콩이" and p["theme"] == {"mode": "system", "accent": "#ff6b8b", "font": "jua"}
    assert c.put("/owner/profile", headers=h, json={"character": "hadaeng"}).json()["agent_name"] == "콩이"  # custom stays
    for bad in ({"character": "dragon"}, {"agent_name": "x" * 21}, {"theme": {"accent": "red"}},
                {"theme": {"font": "comic"}}):
        assert c.put("/owner/profile", headers=h, json=bad).status_code == 400
    card = c.get("/.well-known/agent-card.json").json()
    ext = card["capabilities"]["extensions"][0]
    assert ext["params"] == {"agent_name": "콩이", "character": "hadaeng"}
    assert "You are 콩이" in a.system_prompt()


def test_gateway_reads_profile_from_disk_without_waking(tmp_path):
    a = make(tmp_path, "alice", [])
    TestClient(create_app(a)).put("/owner/profile", headers={"X-Owner-Token": "alice-t"}, json={"character": "hanyang"})
    mgr = SandboxManager(ProcessBackend(), tmp_path)
    c = TestClient(create_gateway(mgr, settings=GatewaySettings(auth_mode="both", proxy_secret="s")))
    me = c.get("/auth/me", headers={"X-Auth-Request-User": "alice", "X-Nixie-Proxy-Secret": "s"}).json()
    assert me["profile"]["agent_name"] == "하냥이" and me["profile"]["character"] == "hanyang"
    card = c.get("/u/alice/.well-known/agent-card.json").json()
    assert card["capabilities"]["extensions"][0]["params"]["character"] == "hanyang"
    assert mgr.running == {}


async def test_rooms_show_both_agents_names_and_characters(tmp_path):
    mesh = Mesh()
    alice = make(tmp_path, "alice", ["bob"], mesh)
    bob = make(tmp_path, "bob", ["alice"], mesh)
    TestClient(mesh.apps["bob"]).put("/owner/profile", headers={"X-Owner-Token": "bob-t"},
                                     json={"character": "hanyang", "agent_name": "나비"})
    r = await alice.schedule_with("bob", "Lunch", 60, "2026-11-17T09:00", "2026-11-17T18:00")
    assert r["status"] == "pending_owner_approval"
    room = (await alice.rooms())[0]
    me, peer = room["participants"]
    assert (me["agent_name"], me["character"]) == ("하댕이", "hadaeng")
    assert (peer["agent_name"], peer["character"], peer["name"]) == ("나비", "hanyang", "Bob")
    assert bob.store.room_lines("alice")[0]["text"].startswith("안녕하세요, Alice님의 에이전트 하댕이입니다.")

    # bob approves -> both archives get the meeting with the agent conversation
    aid = bob.pending_approvals()[0]["id"]
    await bob.decide(aid, True)
    b_doc = bob.archive.search("Lunch")[0]
    a_doc = alice.archive.search("Lunch")[0]
    assert b_doc["title"] == "일정 승인: Lunch" and b_doc["topic"] == "Alice님과 일정"
    assert a_doc["title"] == "일정 확정: Lunch" and a_doc["topic"] == "Bob님과 일정"
    assert "## 에이전트 간 대화" in alice.archive.get(a_doc["id"])["body"]


# ---------------------------------------------------------------- concurrent requests
async def test_second_request_runs_while_first_is_busy(tmp_path):
    async def slow_llm(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "done: " + body["messages"][-1]["content"]}}]})

    a = make(tmp_path, "alice", [])
    a.cfg.models.tiers["fast"].base_url = "http://llm/v1"
    a.cfg.models.tiers["fast"].model = "m"
    a.llm = ModelRouter(a.cfg.models, transport=httpx.MockTransport(slow_llm))
    t0 = time.monotonic()
    r1, r2 = await asyncio.gather(a.owner_chat_turn("첫 번째 작업"), a.owner_chat_turn("두 번째 작업"))
    assert time.monotonic() - t0 < 1.8  # not 2 s: the second request did not wait for the first
    assert r1["reply"] == "done: 첫 번째 작업" and r2["reply"] == "done: 두 번째 작업"
    hist = {m["id"]: m for m in a.store.chat_history()}
    assert hist[r1["id"]]["data"]["reply_to"] == r1["reply_to"] and hist[r1["reply_to"]]["text"] == "첫 번째 작업"
    assert hist[r2["reply_to"]]["text"] == "두 번째 작업"


# ---------------------------------------------------------------- work archive
async def test_work_is_archived_read_only_and_searchable(tmp_path, mock_hynix):
    a = make(tmp_path, "alice", [], services={"hr": {"base_url": mock_hynix.url}})
    await a.owner_chat_turn("/help")  # no action -> not archived
    assert a.store.archive_docs() == []
    r = await a.owner_chat_turn("/do leave.request kind=annual start=2026-11-20 end=2026-11-20 reason=개인")
    aid = json.loads(r["reply"])["approval_id"]
    await a.decide(aid, True)
    docs = a.store.archive_docs()
    assert [d["title"] for d in docs] == ["승인: 휴가 신청", "휴가 신청 요청"]
    req, dec = docs[1], docs[0]
    assert req["topic"] == dec["topic"] == "휴가 신청" and dec["refs"] == [req["id"]]  # grows as linked documents
    assert "kind=annual" in req["body"] and "승인 대기" in req["body"]

    # read-only: no update, no delete; the file copy is mode 0444 and matches its hash
    db = sqlite3.connect(a.cfg.db_path)
    for sql in ("UPDATE archive SET title='x'", "DELETE FROM archive"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
        db.rollback()
    db.close()
    f = tmp_path / "alice" / "archive" / "docs" / f"{req['id']}.md"
    assert stat.S_IMODE(f.stat().st_mode) == 0o444
    import hashlib
    assert hashlib.sha256(f.read_bytes()).hexdigest() == req["sha256"]
    assert f.read_text().startswith("---\nid: ") and "read_only: true" in f.read_text()

    # a follow-up note is a new document; the original does not change
    note = a.archive.note(req["id"], "다음부터는 금요일 오후 반차로 신청")
    assert note["follows"] == req["id"] and a.store.archive_doc(req["id"])["body"] == req["body"]
    assert {b["id"] for b in a.archive.get(req["id"])["backlinks"]} == {dec["id"], note["id"]}
    assert a.store.verify_archive()

    # search: Korean particles are removed ("휴가를" finds "휴가")
    assert a.archive.search("휴가를 신청한 기록")[0]["topic"] == "휴가 신청"
    assert "휴가 신청 요청" in (await a.owner_chat_turn("/wiki 휴가"))["reply"]

    # local wiki bundle and zip
    b = a.archive.bundle()
    paths = {x["path"] for x in b["files"]}
    assert {f"docs/{req['id']}.md", "index.md", "topics/휴가 신청.md"} <= paths and b["integrity_ok"]
    z = zipfile.ZipFile(io.BytesIO(a.archive.zip_bytes()))
    assert "nixie-wiki/index.md" in z.namelist()


async def test_agent_uses_related_past_work(tmp_path, mock_hynix):
    seen: list[str] = []

    async def llm(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["messages"][0]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "**확인했습니다**"}}]})

    a = make(tmp_path, "alice", [], services={"hr": {"base_url": mock_hynix.url}})
    await a.owner_chat_turn("/do leave.request kind=annual start=2026-11-20 end=2026-11-20 reason=개인")
    a.cfg.models.tiers["fast"].base_url = "http://llm/v1"
    a.llm = ModelRouter(a.cfg.models, transport=httpx.MockTransport(llm))
    await a.owner_chat_turn("지난번 휴가 신청은 어떻게 됐어?")
    doc = a.store.archive_docs()[0]
    assert "Related past work" in seen[0] and doc["id"] in seen[0]
    assert {t["function"]["name"] for t in a.TOOLS} >= {"search_archive", "read_archive"}


def test_archive_api(tmp_path, mock_hynix):
    a = make(tmp_path, "alice", [], services={"hr": {"base_url": mock_hynix.url}})
    c = TestClient(create_app(a))
    h = {"X-Owner-Token": "alice-t"}
    r = c.post("/owner/chat", headers=h, json={"text": "/do leave.request kind=annual start=2026-11-20 end=2026-11-20 reason=개인"})
    assert r.status_code == 200 and r.json()["reply_to"] > 0
    lst = c.get("/owner/archive", headers=h).json()
    assert lst["integrity_ok"] and lst["topics"][0]["topic"] == "휴가 신청"
    doc_id = lst["items"][0]["id"]
    d = c.get(f"/owner/archive/{doc_id}", headers=h).json()
    assert d["read_only"] and d["kind"] == "task"
    n = c.post(f"/owner/archive/{doc_id}/notes", headers=h, json={"text": "메모"}).json()
    assert n["follows"] == doc_id
    for method in ("PUT", "PATCH", "DELETE"):
        assert c.request(method, f"/owner/archive/{doc_id}", headers=h, json={"body": "x"}).status_code == 405
    assert c.get("/owner/archive/export.zip", headers=h).headers["content-type"] == "application/zip"
    assert c.get("/owner/archive/nope", headers=h).status_code == 404
    assert load_profile(tmp_path / "alice")["chosen"] is False
