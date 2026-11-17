"""Core personal-agent logic: peer intents, scheduling negotiation, owner chat."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import narrate
from .config import AgentConfig, Fact
from .models import ModelRouter
from .peer import PeerError, send_intent
from .policy import Policy, PolicyError
from .schedule import free_slots, intersect
from .store import Store
from .tools import NotAvailable, TaskRouter, ToolError


def _dt(s: str, tz) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=tz)


class NixieAgent:
    def __init__(
        self,
        cfg: AgentConfig,
        store: Store | None = None,
        llm: ModelRouter | None = None,
        peer_transport: httpx.AsyncBaseTransport | None = None,
        tool_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.policy = Policy(cfg)
        self.llm = llm or ModelRouter(cfg.models)  # fast by default, pro only when needed
        self.peer_transport = peer_transport  # injectable for tests
        self.user = os.environ.get("NIXIE_USER") or cfg.agent_id
        self.tasks = TaskRouter(cfg, self.user, tool_transport)

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
        if a and a["kind"] == "skill_action" and a["status"] == "pending":
            if not approve:
                self.store.set_approval_status(approval_id, "declined")
                return {"status": "declined", **a["payload"]}
            self.store.set_approval_status(approval_id, "approved")
            try:
                res = await self.tasks.run_skill(a["payload"]["skill"], a["payload"]["params"])
            except (ToolError, NotAvailable) as e:
                self.store.set_approval_status(approval_id, "failed")
                return {"status": "failed", "error": str(e), **a["payload"]}
            self.store.audit("tool", "-", a["payload"]["skill"], {"route": res["route"]})
            return res
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
        msg = await self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": question}],
                                  kind="peer_answer")
        return msg.get("content") or ""

    # ---------- business tasks (API > CLI > browser) ----------
    async def do_task(self, skill: str, params: dict[str, Any]) -> dict[str, Any]:
        sk = self.tasks.skills.get(skill)
        if sk is None:
            raise ToolError(f"unknown skill {skill!r}")
        if sk.confirm:
            aid = self.store.add_approval("skill_action", "-", {"skill": skill, "title": sk.title, "params": params})
            return {"status": "pending_owner_approval", "approval_id": aid, "skill": skill,
                    "detail": f"'{sk.title}' needs owner approval before it runs"}
        res = await self.tasks.run_skill(skill, params)
        self.store.audit("tool", "-", skill, {"route": res["route"]})
        return res

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
        {"type": "function", "function": {"name": "list_skills",
         "description": "List in-house work skills (meeting rooms, leave, approvals, document search, IT tickets) and their routes",
         "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "do_task",
         "description": "Run an in-house work skill. Preferred over run_shell and browse. Irreversible skills wait for owner approval.",
         "parameters": {"type": "object", "properties": {"skill": {"type": "string"}, "params": {"type": "object"}},
                        "required": ["skill", "params"]}}},
        {"type": "function", "function": {"name": "run_shell",
         "description": "Run one allow-listed CLI program (no shell syntax) in the owner's workspace. Use for API/CLI work that no skill covers.",
         "parameters": {"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
                        "required": ["argv"]}}},
        {"type": "function", "function": {"name": "browse",
         "description": "LAST RESORT. Leased shared browser with the owner's cookies. Only for screens that need real clicks and have no API/CLI.",
         "parameters": {"type": "object", "properties": {
             "reason": {"type": "string", "description": "why API/CLI cannot do this"},
             "steps": {"type": "array", "items": {"type": "object"}}}, "required": ["reason", "steps"]}}},
        {"type": "function", "function": {"name": "list_approvals", "description": "List pending requests that need the owner's decision",
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
                return self.pending_approvals()
            if name == "decide_approval":
                return await self.decide(args["approval_id"], bool(args["approve"]))
            if name == "list_skills":
                return self.tasks.catalog()
            if name == "do_task":
                return await self.do_task(args["skill"], dict(args.get("params") or {}))
            if name == "run_shell":
                return await self.tasks.shell.run(list(args["argv"]))
            if name == "browse":
                return await self.tasks.browse(list(args["steps"]), args.get("reason", ""))
        except (PeerError, PolicyError, ToolError, NotAvailable, ValueError, KeyError, TypeError) as e:
            return {"error": str(e)}
        return {"error": f"unknown tool {name}"}

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [a for a in self.store.approvals("pending") if a["kind"] in ("inbound_proposal", "skill_action")]

    def system_prompt(self) -> str:
        now = datetime.now(self.cfg.tz).isoformat(timespec="minutes")
        facts = "\n- ".join([f.text for f in self.cfg.facts + self._memory_facts()] or ["(none)"])
        skills = ", ".join(f"{s.id} ({s.title})" for s in self.tasks.skills.values()) or "none"
        return (
            f"You are Nixie, the in-house work assistant of {self.cfg.owner_name}, an SK hynix employee. "
            f"Now: {now} ({self.cfg.timezone}). You are a daily-work expert, not a coding tool.\n"
            "You handle: colleague scheduling through their agents, meeting rooms, leave and attendance, "
            "approvals, internal document and regulation search, IT helpdesk requests.\n"
            "Rules:\n"
            "1. Prefer do_task with a skill. If no skill fits, use run_shell (API/CLI). Use browse only when a screen "
            "needs real clicks and no API/CLI exists; give the reason.\n"
            "2. Irreversible actions wait for the owner's approval; tell the owner the approval id.\n"
            "3. Never share confidential company information with other people's agents.\n"
            "4. Reply briefly in the owner's language.\n"
            f"Skills: {skills}\nPeer agents: {', '.join(sorted(self.cfg.peers)) or 'none'}\nOwner facts:\n- {facts}"
        )

    async def owner_chat(self, text: str) -> str:
        if text.startswith("/") or not self.llm.enabled:
            return await self._command(text)
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()},
                                          {"role": "user", "content": text}]
        hint: str | None = None
        for step in range(6):
            msg = await self.llm.chat(messages, tools=self.TOOLS, kind="owner_chat", step=step, hint=hint)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content") or ""
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for c in calls:
                args = json.loads(c["function"].get("arguments") or "{}")
                result = await self._run_tool(c["function"]["name"], args)
                if c["function"]["name"] == "do_task":
                    sk = self.tasks.skills.get(args.get("skill", ""))
                    if sk and sk.model_hint == "pro":
                        hint = "pro"  # reading/summarizing this result is worth the stronger model
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
        return "Stopped after 6 tool steps."

    HELP = (
        "Commands:\n"
        "/schedule <peer> <minutes> <window_start> <window_end> <title...>\n"
        "/ask <peer> <question...>\n"
        "/remember [private|trusted|public] <text...>\n"
        "/approvals\n/approve <id>\n/decline <id>\n/events\n"
        "/skills\n/do <skill> key=value ...\n/sh <program> <args...>"
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
                pend = self.pending_approvals()
                return json.dumps(pend, ensure_ascii=False) if pend else "no pending requests"
            if cmd == "skills":
                return json.dumps(self.tasks.catalog(), ensure_ascii=False)
            if cmd == "do" and args:
                params = dict(a.split("=", 1) for a in args[1:] if "=" in a)
                return json.dumps(await self.do_task(args[0], params), ensure_ascii=False, default=str)
            if cmd == "sh" and args:
                return json.dumps(await self.tasks.shell.run(args), ensure_ascii=False)
            if cmd in ("approve", "decline") and args:
                return json.dumps(await self.decide(args[0], cmd == "approve"), ensure_ascii=False)
            if cmd == "events":
                return json.dumps(self.store.events(), ensure_ascii=False)
        except (PeerError, PolicyError, ToolError, NotAvailable, ValueError) as e:
            return f"error: {e}"
        if not self.llm.enabled and not text.startswith("/"):
            return "LLM is not configured (set NIXIE_LLM_BASE_URL). " + self.HELP
        return self.HELP
