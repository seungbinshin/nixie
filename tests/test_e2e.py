"""End-to-end: two real agent servers talk over A2A (JSON-RPC over HTTP)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_cfg(tmp: Path, name: str, port: int, peers: dict, busy: list, facts: list, llm_url: str = "") -> Path:
    import yaml

    cfg = {
        "agent_id": name, "owner_name": name.capitalize(), "host": "127.0.0.1", "port": port,
        "owner_token": f"{name}-owner", "timezone": "Asia/Seoul", "work_hours": [9, 18],
        "db_path": str(tmp / f"{name}.db"), "busy": busy, "facts": facts, "peers": peers,
        "llm": {"base_url": llm_url},
    }
    p = tmp / f"{name}.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def start(cfg: Path, port: int) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NIXIE_LLM")}
    proc = subprocess.Popen([sys.executable, "-m", "nixie.cli", "serve", "--config", str(cfg)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError(proc.stdout.read().decode())


class FakeLLM(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible server: first turn calls schedule_meeting, then answers."""

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        if body.get("tools") and msgs[-1]["role"] == "user":
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c1", "type": "function", "function": {"name": "schedule_meeting", "arguments": json.dumps({
                    "peer": "bob", "title": "LLM lunch", "duration_min": 60,
                    "window_start": "2026-11-18T12:00", "window_end": "2026-11-18T15:00"})}}]}
        elif msgs[-1]["role"] == "tool":
            msg = {"role": "assistant", "content": "done: " + msgs[-1]["content"]}
        else:
            msg = {"role": "assistant", "content": "LLM answer using facts: " + msgs[0]["content"][-80:]}
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def cluster(tmp_path):
    pa, pb, pc, pl = free_port(), free_port(), free_port(), free_port()
    llm = HTTPServer(("127.0.0.1", pl), FakeLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    a = write_cfg(tmp_path, "alice", pa,
                  {"bob": {"url": f"http://127.0.0.1:{pb}", "token": "ab", "scopes": ["availability", "meeting", "profile.trusted"]},
                   "carol": {"url": f"http://127.0.0.1:{pc}", "token": "ac", "scopes": []}},
                  [{"start": "2026-11-17T09:00", "end": "2026-11-17T11:00"}],
                  [{"text": "Alice likes noodles", "visibility": "trusted"}],
                  llm_url=f"http://127.0.0.1:{pl}/v1")
    b = write_cfg(tmp_path, "bob", pb,
                  {"alice": {"url": f"http://127.0.0.1:{pa}", "token": "ab", "scopes": ["availability", "meeting", "profile.trusted"]}},
                  [{"start": "2026-11-17T11:00", "end": "2026-11-17T13:00"}],
                  [{"text": "Bob is vegetarian", "visibility": "trusted"},
                   {"text": "Bob works in Icheon", "visibility": "public"},
                   {"text": "Bob secret plan", "visibility": "private"}])
    # carol knows alice but alice did not grant carol any scope; carol also uses a wrong token for bob
    c = write_cfg(tmp_path, "carol", pc,
                  {"alice": {"url": f"http://127.0.0.1:{pa}", "token": "ac", "scopes": []},
                   "bob": {"url": f"http://127.0.0.1:{pb}", "token": "guess", "scopes": []}}, [], [])
    procs = [start(a, pa), start(b, pb), start(c, pc)]
    yield {"alice": pa, "bob": pb, "carol": pc}
    for p in procs:
        p.terminate()
        p.wait(5)
    llm.shutdown()


def owner(port: int, name: str, method: str, path: str, body=None):
    r = httpx.request(method, f"http://127.0.0.1:{port}{path}", json=body,
                      headers={"X-Owner-Token": f"{name}-owner"}, timeout=30)
    r.raise_for_status()
    return r.json()


def test_agent_card(cluster):
    card = httpx.get(f"http://127.0.0.1:{cluster['bob']}/.well-known/agent-card.json").json()
    assert card["name"] == "Nixie agent of Bob"
    assert {s["id"] for s in card["skills"]} >= {"availability.query", "meeting.propose", "agent.ask"}


def test_schedule_with_owner_approval(cluster):
    A, B = cluster["alice"], cluster["bob"]
    r = owner(A, "alice", "POST", "/owner/schedule", {"peer": "bob", "title": "Lunch", "duration_min": 60,
              "window_start": "2026-11-17T09:00", "window_end": "2026-11-17T18:00"})
    # alice busy 09-11, bob busy 11-13 -> first common hour is 13:00
    assert r["status"] == "pending_owner_approval"
    assert r["start"].startswith("2026-11-17T13:00")
    pend = [a for a in owner(B, "bob", "GET", "/owner/approvals") if a["status"] == "pending"]
    assert len(pend) == 1
    d = owner(B, "bob", "POST", f"/owner/approvals/{pend[0]['id']}/approve")
    assert d["status"] == "approved"
    ea, eb = owner(A, "alice", "GET", "/owner/events"), owner(B, "bob", "GET", "/owner/events")
    assert len(ea) == 1 and len(eb) == 1 and ea[0]["start"] == eb[0]["start"]
    # second request must skip the booked slot
    r2 = owner(A, "alice", "POST", "/owner/schedule", {"peer": "bob", "title": "Sync", "duration_min": 60,
               "window_start": "2026-11-17T13:00", "window_end": "2026-11-17T18:00"})
    assert r2["start"].startswith("2026-11-17T14:00")


def test_ask_respects_visibility(cluster):
    ans = owner(cluster["alice"], "alice", "POST", "/owner/ask", {"peer": "bob", "question": "food?"})["answer"]
    assert "vegetarian" in ans and "Icheon" in ans and "secret" not in ans


def test_untrusted_agents_rejected(cluster):
    # wrong token
    r = httpx.post(f"http://127.0.0.1:{cluster['carol']}/owner/ask", json={"peer": "bob", "question": "hi"},
                   headers={"X-Owner-Token": "carol-owner"}, timeout=30)
    assert r.status_code == 502 and "unauthenticated" in r.text
    # valid token, missing scope
    r = httpx.post(f"http://127.0.0.1:{cluster['carol']}/owner/schedule",
                   json={"peer": "alice", "title": "x", "duration_min": 30,
                         "window_start": "2026-11-17T13:00", "window_end": "2026-11-17T18:00"},
                   headers={"X-Owner-Token": "carol-owner"}, timeout=30)
    assert r.status_code == 502 and "lacks scope availability" in r.text


def test_owner_token_required(cluster):
    r = httpx.get(f"http://127.0.0.1:{cluster['alice']}/owner/events", headers={"X-Owner-Token": "nope"})
    assert r.status_code == 401


def test_llm_tool_calling_chat(cluster):
    reply = owner(cluster["alice"], "alice", "POST", "/owner/chat", {"text": "Book lunch with Bob on Wednesday"})["reply"]
    assert reply.startswith("done:") and "pending_owner_approval" in reply and "2026-11-18T12:00" in reply


def test_command_mode_without_llm(cluster):
    reply = owner(cluster["bob"], "bob", "POST", "/owner/chat", {"text": "/remember public Bob plays tennis"})["reply"]
    assert reply.startswith("saved")
    ans = owner(cluster["alice"], "alice", "POST", "/owner/ask", {"peer": "bob", "question": "hobby?"})["answer"]
    assert "tennis" in ans


def test_agent_conversation_is_readable_and_read_only(cluster, tmp_path):
    A, B = cluster["alice"], cluster["bob"]
    r = owner(A, "alice", "POST", "/owner/schedule", {"peer": "bob", "title": "Lunch", "duration_min": 60,
              "window_start": "2026-11-17T09:00", "window_end": "2026-11-17T18:00"})
    cid = r["conversation_id"]
    pend = [a for a in owner(B, "bob", "GET", "/owner/approvals") if a["status"] == "pending"]
    owner(B, "bob", "POST", f"/owner/approvals/{pend[0]['id']}/approve")

    ta = owner(A, "alice", "GET", f"/owner/conversations/{cid}")
    tb = owner(B, "bob", "GET", f"/owner/conversations/{cid}")
    # both owners see the same full dialogue in plain language
    assert [l["text"] for l in ta["lines"]] == [l["text"] for l in tb["lines"]]
    assert [l["speaker"] for l in ta["lines"]] == ["alice", "bob", "alice", "bob", "bob", "alice"]
    texts = "\n".join(l["text"] for l in ta["lines"])
    assert "가능한 시간을 알려 주세요" in texts and "승인을 요청했습니다" in texts and "확정합니다" in texts
    assert ta["read_only"] and ta["integrity_ok"] and tb["integrity_ok"]

    # owners have no way to write into the conversation
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        resp = httpx.request(method, f"http://127.0.0.1:{A}/owner/conversations/{cid}",
                             json={"text": "owner override"}, headers={"X-Owner-Token": "alice-owner"})
        assert resp.status_code == 405
    # storage rejects edits too
    import sqlite3
    db = sqlite3.connect(str(tmp_path / "alice.db"))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE transcript SET text='tampered'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM transcript")


def test_rejected_request_is_transcribed(cluster):
    r = httpx.post(f"http://127.0.0.1:{cluster['carol']}/owner/schedule",
                   json={"peer": "alice", "title": "x", "duration_min": 30,
                         "window_start": "2026-11-17T13:00", "window_end": "2026-11-17T18:00"},
                   headers={"X-Owner-Token": "carol-owner"}, timeout=30)
    assert r.status_code == 502
    convs = owner(cluster["alice"], "alice", "GET", "/owner/conversations")
    lines = owner(cluster["alice"], "alice", "GET", f"/owner/conversations/{convs[0]['conv_id']}")["lines"]
    assert lines[0]["speaker"] == "carol" and lines[-1]["text"].startswith("요청을 거부합니다")
