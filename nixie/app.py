"""HTTP app of one agent sandbox.

Three entry paths, each with its own authentication:
- /a2a/...    agent-to-agent (A2A JSON-RPC). Peer token in the message metadata.
- /owner/...  the owner (a person). Either the gateway's identity header (X-Auth-Request-User, trusted only
              with this sandbox's gateway secret) or the owner token (X-Owner-Token, development and CLI).
- /ingest/... in-house collection adapters (mail, approval system). Service token X-Ingest-Token.
The web UI is a separate static build; this app serves no HTML.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel

from a2a.helpers import get_data_parts
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentProvider, AgentSkill, Part, Task, TaskState,
    TaskStatus,
)
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from .agent import ConflictError, NixieAgent
from .profile import PROFILE_EXTENSION, ProfileError, load_profile, save_profile
from .peer import PeerError
from .policy import PolicyError

log = logging.getLogger("nixie")


class NixieExecutor(AgentExecutor):
    def __init__(self, agent: NixieAgent):
        self.agent = agent

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id or "", context.context_id or "").cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if not msg or not context.task_id or not context.context_id:
            return
        await event_queue.enqueue_event(Task(
            id=context.task_id, context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED), history=[msg],
        ))
        up = TaskUpdater(event_queue=event_queue, task_id=context.task_id, context_id=context.context_id)
        meta = MessageToDict(msg.metadata) if msg.HasField("metadata") else {}
        data_parts = get_data_parts(msg.parts)
        envelope: dict[str, Any] = data_parts[0] if data_parts and isinstance(data_parts[0], dict) else {}
        intent = envelope.get("intent") or "agent.ask"
        data = envelope.get("data") or {"question": context.get_user_input()}

        text = " ".join(p.text for p in msg.parts if p.HasField("text"))
        try:
            result, reply_text = await self.agent.handle_peer(
                meta.get("from_agent", ""), meta.get("token", ""), intent, data, context.context_id, text
            )
        except PolicyError as e:
            log.warning("rejected %s from %s: %s", intent, meta.get("from_agent"), e)
            await up.reject(up.new_agent_message(parts=[Part(text=str(e))]))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("intent %s failed", intent)
            await up.failed(up.new_agent_message(parts=[Part(text=f"internal error: {e}")]))
            return
        await up.add_artifact(
            parts=[Part(text=reply_text),
                   Part(data=ParseDict(result, struct_pb2.Value()), media_type="application/json")],
            name="result", last_chunk=True,
        )
        await up.complete()


def agent_card(cfg, profile: dict | None = None) -> AgentCard:
    prof = profile or load_profile(cfg.home)
    ext = AgentExtension(uri=PROFILE_EXTENSION, description="Agent name and character shown to people",
                         required=False, params=ParseDict({"agent_name": prof["agent_name"],
                                                           "character": prof["character"]}, struct_pb2.Struct()))
    return AgentCard(
        name=f"Nixie agent of {cfg.owner_name}",
        description="Personal agent. Accepts requests only from trusted peer agents.",
        provider=AgentProvider(organization="Nixie", url=cfg.public_url),
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False, extensions=[ext]),
        default_input_modes=["application/json", "text"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(id="availability.query", name="Availability", description="Return free slots in a window",
                       tags=["calendar"], input_modes=["application/json"], output_modes=["application/json"]),
            AgentSkill(id="meeting.propose", name="Meeting proposal", description="Propose a meeting; may need owner approval",
                       tags=["calendar"], input_modes=["application/json"], output_modes=["application/json"]),
            AgentSkill(id="agent.ask", name="Ask", description="Ask about the owner; answers use only shareable facts",
                       tags=["profile"], input_modes=["application/json", "text"], output_modes=["application/json"]),
        ],
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=f"{cfg.public_url}/a2a/jsonrpc"),
        ],
    )


class ChatIn(BaseModel):
    text: str


class ScheduleIn(BaseModel):
    peer: str
    title: str
    duration_min: int
    window_start: str
    window_end: str


class AskIn(BaseModel):
    peer: str
    question: str


class MemoryIn(BaseModel):
    text: str
    visibility: str = "private"


class ReadIn(BaseModel):
    upto: int | None = None


class ThemeIn(BaseModel):
    mode: str | None = None
    accent: str | None = None
    font: str | None = None


class ProfileIn(BaseModel):
    character: str | None = None
    agent_name: str | None = None
    theme: ThemeIn | None = None


class NoteIn(BaseModel):
    text: str


class IngestItem(BaseModel):
    title: str
    body: str = ""
    kind: str = "external"
    source: str = "external"
    source_url: str | None = None
    external_id: str | None = None


class IngestIn(BaseModel):
    items: list[IngestItem]


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def create_app(agent: NixieAgent) -> FastAPI:
    app = FastAPI(title=f"Nixie ({agent.cfg.agent_id})")

    @app.get("/.well-known/agent-card.json")
    async def live_card() -> JSONResponse:
        """Registered before the A2A routes, so the card shows the current agent name and character."""
        return JSONResponse(MessageToDict(agent_card(agent.cfg, agent.profile())))

    card = agent_card(agent.cfg)
    handler = DefaultRequestHandler(agent_executor=NixieExecutor(agent), task_store=InMemoryTaskStore(), agent_card=card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler=handler, rpc_url="/a2a/jsonrpc"),
    )

    gateway_secret = os.environ.get("NIXIE_GATEWAY_SECRET", "")  # unique per sandbox start, known only to the gateway

    def owner(x_owner_token: str = Header(default=""), x_nixie_gateway: str = Header(default=""),
              x_auth_request_user: str = Header(default="")) -> None:
        # 1. Gateway path: the gateway authenticated the person (SSO proxy or session) and says who it is.
        #    The header is trusted only together with this sandbox's gateway secret, so other sandboxes
        #    on the same network cannot forge it.
        if _same(x_nixie_gateway, gateway_secret):
            if _same(x_auth_request_user, agent.cfg.sso_user):
                return
            raise HTTPException(403, "identity does not own this agent")
        # 2. Owner token (development, CLI). An empty configured token never matches.
        if _same(x_owner_token, agent.cfg.owner_token):
            return
        raise HTTPException(401, "owner authentication required")

    def ingest_auth(x_ingest_token: str = Header(default="")) -> None:
        if not _same(x_ingest_token, agent.cfg.ingest_token):
            raise HTTPException(401, "bad ingest token")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith(("/owner", "/ingest")):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    def wrap_peer_error(e: Exception) -> HTTPException:
        return HTTPException(502, str(e))

    @app.get("/owner/status", dependencies=[Depends(owner)])
    async def status() -> dict[str, Any]:
        return {"agent_id": agent.cfg.agent_id, "owner": agent.cfg.owner_name, "llm": agent.llm.enabled,
                "model_tiers": agent.llm.available(), "model_usage": agent.llm.usage,
                "peers": sorted(agent.cfg.peers), "skills": len(agent.tasks.skills)}

    @app.get("/owner/me", dependencies=[Depends(owner)])
    async def me() -> dict[str, Any]:
        return {"agent_id": agent.cfg.agent_id, "owner_name": agent.cfg.owner_name, "sso_user": agent.cfg.sso_user,
                "timezone": agent.cfg.timezone, "llm": agent.llm.enabled, "profile": agent.profile(),
                "peers": [{"id": p.name, "name": p.label, "room_id": agent.room_id(p.name)}
                          for p in sorted(agent.cfg.peers.values(), key=lambda p: p.name)]}

    @app.get("/owner/profile", dependencies=[Depends(owner)])
    async def get_profile() -> dict[str, Any]:
        return agent.profile()

    @app.put("/owner/profile", dependencies=[Depends(owner)])
    async def put_profile(body: ProfileIn) -> dict[str, Any]:
        """Character (hadaeng | hanyang), agent name (free, 1-20 chars), UI theme (mode, accent, font)."""
        patch = body.model_dump(exclude_none=True)
        if "theme" in patch:
            patch["theme"] = {k: v for k, v in patch["theme"].items() if v is not None}
        try:
            return save_profile(agent.cfg.home, patch)
        except ProfileError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/owner/skills", dependencies=[Depends(owner)])
    async def skills() -> list[dict[str, Any]]:
        return agent.tasks.catalog()

    @app.post("/owner/chat", dependencies=[Depends(owner)])
    async def chat(body: ChatIn) -> dict[str, Any]:
        """Requests run concurrently. The reply carries reply_to (id of the request line)."""
        if not body.text.strip():
            raise HTTPException(400, "empty message")
        return await agent.owner_chat_turn(body.text)

    @app.get("/owner/chat", dependencies=[Depends(owner)])
    async def chat_history(limit: int = 200) -> dict[str, Any]:
        return {"messages": agent.store.chat_history(min(max(limit, 1), 500))}

    @app.post("/owner/schedule", dependencies=[Depends(owner)])
    async def schedule(body: ScheduleIn) -> dict[str, Any]:
        try:
            return await agent.schedule_with(**body.model_dump())
        except PeerError as e:
            raise wrap_peer_error(e) from e

    @app.post("/owner/ask", dependencies=[Depends(owner)])
    async def ask(body: AskIn) -> dict[str, str]:
        try:
            return {"answer": await agent.ask_peer(body.peer, body.question)}
        except PeerError as e:
            raise wrap_peer_error(e) from e

    @app.get("/owner/memories", dependencies=[Depends(owner)])
    async def memories() -> list[dict[str, Any]]:
        return agent.store.memories()

    @app.post("/owner/memories", dependencies=[Depends(owner)])
    async def add_memory(body: MemoryIn) -> dict[str, str]:
        return {"id": agent.store.add_memory(body.text, body.visibility)}

    @app.get("/owner/events", dependencies=[Depends(owner)])
    async def events() -> list[dict[str, Any]]:
        return agent.store.events()

    @app.get("/owner/approvals", dependencies=[Depends(owner)])
    async def approvals(status: str | None = None) -> list[dict[str, Any]]:
        if status == "pending":
            return agent.pending_approvals()  # what the owner must decide, with a readable summary
        return agent.store.approvals(status)

    @app.post("/owner/approvals/{aid}/{decision}", dependencies=[Depends(owner)])
    async def decide(aid: str, decision: str) -> dict[str, Any]:
        """Idempotent: a second click with the same decision runs nothing and returns the first result."""
        if decision not in ("approve", "decline"):
            raise HTTPException(400, "decision must be approve or decline")
        try:
            res = await agent.decide(aid, decision == "approve")
        except ConflictError as e:
            raise HTTPException(409, str(e)) from e
        except PeerError as e:
            raise wrap_peer_error(e) from e
        if res.get("status") == "not_found":
            raise HTTPException(404, "no such approval")
        return res

    # Rooms: one per agent pair (max 2 participants). Owners read; they cannot write lines.
    @app.get("/owner/rooms", dependencies=[Depends(owner)])
    async def rooms() -> list[dict[str, Any]]:
        return await agent.rooms()

    @app.get("/owner/rooms/{room_id}", dependencies=[Depends(owner)])
    async def room(room_id: str) -> dict[str, Any]:
        try:
            return await agent.room(room_id)
        except KeyError as e:
            raise HTTPException(404, "no such room") from e

    @app.post("/owner/rooms/{room_id}/read", dependencies=[Depends(owner)])
    async def room_read(room_id: str, body: ReadIn | None = None) -> dict[str, Any]:
        """Move the owner's read marker. This does not write into the conversation."""
        try:
            return agent.mark_room_read(room_id, body.upto if body else None)
        except KeyError as e:
            raise HTTPException(404, "no such room") from e

    @app.get("/owner/notifications", dependencies=[Depends(owner)])
    async def notifications(unread: bool = False, limit: int = 100) -> dict[str, Any]:
        return {"items": agent.store.notifications(unread, min(max(limit, 1), 500)),
                "unread": agent.store.unread_notifications()}

    @app.post("/owner/notifications/read-all", dependencies=[Depends(owner)])
    async def notifications_read_all() -> dict[str, int]:
        return {"marked": agent.store.mark_all_notifications_read()}

    @app.post("/owner/notifications/{nid}/read", dependencies=[Depends(owner)])
    async def notification_read(nid: str) -> dict[str, bool]:
        return {"marked": agent.store.mark_notification_read(nid)}

    # Work archive: read-only wiki. No endpoint changes or deletes a document.
    @app.get("/owner/archive", dependencies=[Depends(owner)])
    async def archive_list(q: str = "", topic: str | None = None, limit: int = 50) -> dict[str, Any]:
        return {"items": agent.archive.search(q, min(max(limit, 1), 200), topic), "topics": agent.archive.topics(),
                "integrity_ok": agent.store.verify_archive()}

    @app.get("/owner/archive/export.zip", dependencies=[Depends(owner)])
    async def archive_zip() -> Response:
        return Response(agent.archive.zip_bytes(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="nixie-wiki.zip"'})

    @app.get("/owner/archive/bundle", dependencies=[Depends(owner)])
    async def archive_bundle() -> dict[str, Any]:
        """All files of the local wiki folder (docs are read-only; index.md and topics/ are rebuilt)."""
        return agent.archive.bundle()

    @app.get("/owner/archive/{doc_id}", dependencies=[Depends(owner)])
    async def archive_get(doc_id: str) -> dict[str, Any]:
        d = agent.archive.get(doc_id)
        if not d:
            raise HTTPException(404, "no such document")
        return d

    @app.post("/owner/archive/{doc_id}/notes", dependencies=[Depends(owner)])
    async def archive_note(doc_id: str, body: NoteIn) -> dict[str, Any]:
        """Add a follow-up note as a NEW document. The original stays as it is."""
        try:
            return agent.archive.note(doc_id, body.text)
        except KeyError as e:
            raise HTTPException(404, "no such document") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/ingest/notifications", dependencies=[Depends(ingest_auth)])
    async def ingest(body: IngestIn) -> dict[str, Any]:
        """Entry point for in-house collection adapters (implemented in-house; mail from mailbrief)."""
        try:
            return {"results": agent.ingest([i.model_dump() for i in body.items])}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # Agent-to-agent conversations: owners can READ only. There is no endpoint to post, edit or delete lines.
    @app.get("/owner/conversations", dependencies=[Depends(owner)])
    async def conversations() -> list[dict[str, Any]]:
        return agent.store.conversations()

    @app.get("/owner/conversations/{conv_id}", dependencies=[Depends(owner)])
    async def conversation(conv_id: str) -> dict[str, Any]:
        lines = agent.store.conversation(conv_id)
        if not lines:
            raise HTTPException(404, "no such conversation")
        return {"conversation_id": conv_id, "read_only": True, "integrity_ok": agent.store.verify_transcript(),
                "room_id": agent.room_id(lines[0]["peer"]),
                "lines": [{"ts": l["ts"], "speaker": l["speaker"], "text": l["text"], "intent": l["intent"],
                           "data": l["data"], "hash": l["hash"]} for l in lines]}

    @app.get("/owner/audit", dependencies=[Depends(owner)])
    async def audit() -> list[dict[str, Any]]:
        return agent.store.audit_log()

    return app
