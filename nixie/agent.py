"""Core personal-agent logic: peer intents, scheduling negotiation, owner chat."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import narrate
from .config import AgentConfig, Fact
from .llm import LLM
from .peer import PeerError, send_intent
from .policy import Policy, PolicyError
from .schedule import free_slots, intersect
from .store import Store


def _dt(s: str, tz) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=tz)


class NixieAgent:
    def __init__(
        self,
        cfg: AgentConfig,
        store: Store | None = None,
        llm: LLM | None = None,
        peer_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.policy = Policy(cfg)
        self.llm = llm or LLM(cfg.llm)
        self.peer_transport = peer_transport  # injectable for tests

    # ---------- calendar ----------
    def busy(self) -> list[tuple[datetime, datetime]]:
        booked = [(_dt(e["start"], self.cfg.tz), _dt(e["end"], self.cfg.tz)) for e in self.store.events()]
        return list(self.cfg.busy) + booked

    def my_slots(self, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, datetime]]:
        return free_slots((start, end), timedelta(minutes=minutes), self.busy(), self.cfg.tz, self.cfg.work_hours)

    def _memory_facts(self) -> list[Fact]:
        return [Fact(text=m["text"], visibility=m["visibility"]) for m in self.store.memories()]

    # ---------- outbound ----------
    async def _send(
        self, peer_name: str, intent: str, data: dict[str, Any], conv_id: str | None = None
    ) -> dict[str, Any]:
        """Send one intent. Both lines (request, reply) go to the read-only transcript."""
        peer = self.cfg.peers.get(peer_name)
        if peer is None:
            raise PeerError(f"unknown peer {peer_name!r}; known: {sorted(self.cfg.peers)}")
        conv_id = conv_id or uuid.uuid4().hex[:12]
        text = narrate.request(intent, data, self.cfg.owner_name)
        self.store.audit("out", peer_name, intent, data)
        self.store.add_line(conv_id, self.cfg.agent_id, peer_name, intent, text, data)
        res, reply_text = await send_intent(
            self.cfg.agent_id, peer, intent, data, transport=self.peer_transport, text=text, context_id=conv_id
        )
        self.store.audit("in-reply", peer_name, intent, res)
        self.store.add_line(conv_id, peer_name, peer_name, intent + ".reply", reply_text, res)
        return res

    async def schedule_with(
        self, peer: str, title: str, duration_min: int, window_start: str, window_end: str
    ) -> dict[str, Any]:
        tz = self.cfg.tz
        ws, we = _dt(window_start, tz), _dt(window_end, tz)
        mine = self.my_slots(ws, we, duration_min)
        if not mine:
            return {"status": "no_slot", "detail": "owner has no free slot in window"}
        conv_id = uuid.uuid4().hex[:12]
        avail = await self._send(
            peer, "availability.query",
            {"title": title, "window_start": ws.isoformat(), "window_end": we.isoformat(), "duration_min": duration_min},
            conv_id,
        )
        theirs = [(_dt(s["start"], tz), _dt(s["end"], tz)) for s in avail.get("slots", [])]
        common = intersect(mine, theirs)
        if not common:
            return {"status": "no_common_slot", "mine": len(mine), "theirs": len(theirs), "conversation_id": conv_id}
        start, end = common[0]
        proposal_id = uuid.uuid4().hex[:12]
        payload = {"proposal_id": proposal_id, "conversation_id": conv_id, "title": title,
                   "start": start.isoformat(), "end": end.isoformat()}
        self.store.add_approval("outbound_proposal", peer, payload)
        res = await self._send(peer, "meeting.propose", payload, conv_id)
        if res.get("status") == "accepted":
            self._finalize_outbound(proposal_id, "confirmed")
            return {"status": "confirmed", **payload, "with": peer}
        return {"status": res.get("status", "unknown"), **payload, "with": peer}

    def _find_outbound(self, proposal_id: str) -> dict[str, Any] | None:
        for a in self.store.approvals():
            if a["kind"] == "outbound_proposal" and a["payload"]["proposal_id"] == proposal_id:
                return a
        return None

    def _finalize_outbound(self, proposal_id: str, status: str) -> None:
        a = self._find_outbound(proposal_id)
        if not a or a["status"] != "pending":
            return
        self.store.set_approval_status(a["id"], status)
        if status == "confirmed":
            p = a["payload"]
            self.store.add_event(p["title"], _dt(p["start"], self.cfg.tz), _dt(p["end"], self.cfg.tz), a["peer"])

    async def ask_peer(self, peer: str, question: str) -> str:
        res = await self._send(peer, "agent.ask", {"question": question})
        return res.get("answer", "")

    async def decide(self, approval_id: str, approve: bool) -> dict[str, Any]:
        a = self.store.get_approval(approval_id)
        if not a or a["kind"] != "inbound_proposal" or a["status"] != "pending":
            return {"status": "not_found"}
        p = a["payload"]
        start, end = _dt(p["start"], self.cfg.tz), _dt(p["end"], self.cfg.tz)
        if approve and any(s < end and start < e for s, e in self.busy()):
            approve = False  # slot taken meanwhile
        if approve:
            self.store.add_event(p["title"], start, end, a["peer"])
            self.store.set_approval_status(approval_id, "approved")
            msg = {k: p[k] for k in ("proposal_id", "title", "start", "end")}
            await self._send(a["peer"], "meeting.confirm", msg, p.get("conversation_id"))
            return {"status": "approved", **p}
        self.store.set_approval_status(approval_id, "declined")
        msg = {k: p[k] for k in ("proposal_id", "title", "start", "end")}
        await self._send(a["peer"], "meeting.decline", msg, p.get("conversation_id"))
        return {"status": "declined", **p}

    # ---------- inbound (called by A2A executor) ----------
    async def handle_peer(
        self, from_agent: str, token: str, intent: str, data: dict[str, Any],
        conv_id: str = "", text: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Handle one inbound intent. Return (data result, human-readable reply). Both sides are transcribed."""
        peer = self.policy.authenticate(from_agent, token)
        conv_id = conv_id or uuid.uuid4().hex[:12]
        self.store.add_line(conv_id, peer.name, peer.name, intent, text or narrate.request(intent, data, peer.name), data)
        try:
            self.policy.authorize(peer, intent)
        except PolicyError as e:
            self.store.add_line(conv_id, self.cfg.agent_id, peer.name, intent + ".reply", f"요청을 거부합니다: {e}", {"error": str(e)})
            raise
        self.store.audit("in", peer.name, intent, data)
        result = await self._dispatch(peer, intent, data)
        reply_text = narrate.reply(intent, result, self.cfg.owner_name)
        self.store.add_line(conv_id, self.cfg.agent_id, peer.name, intent + ".reply", reply_text, result)
        return result, reply_text

    async def _dispatch(self, peer, intent: str, data: dict[str, Any]) -> dict[str, Any]:
        tz = self.cfg.tz

        if intent == "availability.query":
            minutes = int(float(data.get("duration_min", 30)))
            slots = self.my_slots(_dt(data["window_start"], tz), _dt(data["window_end"], tz), minutes)
            # share only free slots, never event titles
            return {"slots": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in slots]}

        if intent == "meeting.propose":
            start, end = _dt(data["start"], tz), _dt(data["end"], tz)
            if any(s < end and start < e for s, e in self.busy()):
                return {"status": "rejected", "reason": "slot not free"}
            if self.policy.needs_approval(peer, intent):
                aid = self.store.add_approval("inbound_proposal", peer.name, data)  # decision happens outside the agent conversation
                return {"status": "pending_owner_approval", "approval_id": aid}
            self.store.add_event(data["title"], start, end, peer.name)
            return {"status": "accepted"}

        if intent == "meeting.confirm":
            self._finalize_outbound(data["proposal_id"], "confirmed")
            return {"status": "ok"}

        if intent == "meeting.decline":
            self._finalize_outbound(data["proposal_id"], "declined")
            return {"status": "ok"}

        if intent == "agent.ask":
            facts = self.policy.shareable_facts(peer, self._memory_facts())
            return {"answer": await self._answer_peer(peer.name, data.get("question", ""), facts)}

        raise PolicyError(f"unsupported intent: {intent}")

    async def _answer_peer(self, peer_name: str, question: str, facts: list[str]) -> str:
        if not self.llm.enabled:
            if not facts:
                return f"{self.cfg.owner_name}'s agent has no information it may share with {peer_name}."
            return f"Shareable information about {self.cfg.owner_name}: " + "; ".join(facts)
        system = (
            f"You are the personal agent of {self.cfg.owner_name}. Another person's agent ({peer_name}) asks a question. "
            "Answer ONLY from the facts below. If the facts do not contain the answer, say you cannot share it. "
            "Never invent facts.\nFacts:\n- " + "\n- ".join(facts or ["(none)"])
        )
        msg = await self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": question}])
        return msg.get("content") or ""

    # ---------- owner chat ----------
    TOOLS = [
        {"type": "function", "function": {"name": "schedule_meeting", "description": "Negotiate a meeting with another person's agent",
         "parameters": {"type": "object", "properties": {
             "peer": {"type": "string"}, "title": {"type": "string"}, "duration_min": {"type": "integer"},
             "window_start": {"type": "string", "description": "ISO 8601"}, "window_end": {"type": "string", "description": "ISO 8601"}},
             "required": ["peer", "title", "duration_min", "window_start", "window_end"]}}},
        {"type": "function", "function": {"name": "ask_peer", "description": "Ask another person's agent a question",
         "parameters": {"type": "object", "properties": {"peer": {"type": "string"}, "question": {"type": "string"}},
                        "required": ["peer", "question"]}}},
        {"type": "function", "function": {"name": "remember", "description": "Save a fact about the owner",
         "parameters": {"type": "object", "properties": {"text": {"type": "string"},
                        "visibility": {"type": "string", "enum": ["private", "trusted", "public"]}}, "required": ["text"]}}},
        {"type": "function", "function": {"name": "list_approvals", "description": "List pending meeting requests",
         "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "decide_approval", "description": "Approve or decline a pending request",
         "parameters": {"type": "object", "properties": {"approval_id": {"type": "string"}, "approve": {"type": "boolean"}},
                        "required": ["approval_id", "approve"]}}},
    ]

    async def _run_tool(self, name: str, args: dict[str, Any]) -> Any:
        try:
            if name == "schedule_meeting":
                return await self.schedule_with(**args)
            if name == "ask_peer":
                return {"answer": await self.ask_peer(**args)}
            if name == "remember":
                return {"id": self.store.add_memory(args["text"], args.get("visibility", "private"))}
            if name == "list_approvals":
                return [a for a in self.store.approvals("pending") if a["kind"] == "inbound_proposal"]
            if name == "decide_approval":
                return await self.decide(args["approval_id"], bool(args["approve"]))
        except (PeerError, PolicyError, ValueError, KeyError, TypeError) as e:
            return {"error": str(e)}
        return {"error": f"unknown tool {name}"}

    async def owner_chat(self, text: str) -> str:
        if text.startswith("/") or not self.llm.enabled:
            return await self._command(text)
        now = datetime.now(self.cfg.tz).isoformat(timespec="minutes")
        system = (
            f"You are Nixie, the personal agent of {self.cfg.owner_name}. Now: {now} ({self.cfg.timezone}). "
            f"Known peer agents: {', '.join(sorted(self.cfg.peers)) or 'none'}. "
            "Use tools to act. Reply briefly in the owner's language.\nOwner facts:\n- "
            + "\n- ".join([f.text for f in self.cfg.facts + self._memory_facts()] or ["(none)"])
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        for _ in range(5):
            msg = await self.llm.chat(messages, tools=self.TOOLS)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for c in calls:
                args = json.loads(c["function"].get("arguments") or "{}")
                result = await self._run_tool(c["function"]["name"], args)
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        return "Stopped after 5 tool steps."

    HELP = (
        "Commands:\n"
        "/schedule <peer> <minutes> <window_start> <window_end> <title...>\n"
        "/ask <peer> <question...>\n"
        "/remember [private|trusted|public] <text...>\n"
        "/approvals\n/approve <id>\n/decline <id>\n/events"
    )

    async def _command(self, text: str) -> str:
        parts = text.strip().split()
        if not parts:
            return self.HELP
        cmd, args = parts[0].lstrip("/"), parts[1:]
        try:
            if cmd == "schedule" and len(args) >= 5:
                r = await self.schedule_with(args[0], " ".join(args[4:]), int(args[1]), args[2], args[3])
                return json.dumps(r, ensure_ascii=False)
            if cmd == "ask" and len(args) >= 2:
                return await self.ask_peer(args[0], " ".join(args[1:]))
            if cmd == "remember" and args:
                vis = args[0] if args[0] in ("private", "trusted", "public") else "private"
                body = args[1:] if vis == args[0] else args
                return f"saved {self.store.add_memory(' '.join(body), vis)} ({vis})"
            if cmd == "approvals":
                pend = [a for a in self.store.approvals("pending") if a["kind"] == "inbound_proposal"]
                return json.dumps(pend, ensure_ascii=False) if pend else "no pending requests"
            if cmd in ("approve", "decline") and args:
                return json.dumps(await self.decide(args[0], cmd == "approve"), ensure_ascii=False)
            if cmd == "events":
                return json.dumps(self.store.events(), ensure_ascii=False)
        except (PeerError, PolicyError, ValueError) as e:
            return f"error: {e}"
        if not self.llm.enabled and not text.startswith("/"):
            return "LLM is not configured (set NIXIE_LLM_BASE_URL). " + self.HELP
        return self.HELP
