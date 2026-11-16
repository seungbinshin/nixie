"""HTTP app: A2A endpoints (agent-to-agent) + owner API + minimal web chat."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
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
    AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill, Part, Task, TaskState, TaskStatus,
)
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

from .agent import NixieAgent
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


def agent_card(agent: NixieAgent) -> AgentCard:
    cfg = agent.cfg
    return AgentCard(
        name=f"Nixie agent of {cfg.owner_name}",
        description="Personal agent. Accepts requests only from trusted peer agents.",
        provider=AgentProvider(organization="Nixie", url=cfg.public_url),
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
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


def create_app(agent: NixieAgent) -> FastAPI:
    app = FastAPI(title=f"Nixie ({agent.cfg.agent_id})")
    card = agent_card(agent)
    handler = DefaultRequestHandler(agent_executor=NixieExecutor(agent), task_store=InMemoryTaskStore(), agent_card=card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler=handler, rpc_url="/a2a/jsonrpc"),
    )

    def owner(x_owner_token: str = Header(default="")) -> None:
        if x_owner_token != agent.cfg.owner_token:
            raise HTTPException(401, "bad owner token")

    def wrap_peer_error(e: Exception) -> HTTPException:
        return HTTPException(502, str(e))

    @app.get("/owner/status", dependencies=[Depends(owner)])
    async def status() -> dict[str, Any]:
        return {"agent_id": agent.cfg.agent_id, "owner": agent.cfg.owner_name, "llm": agent.llm.enabled,
                "peers": sorted(agent.cfg.peers)}

    @app.post("/owner/chat", dependencies=[Depends(owner)])
    async def chat(body: ChatIn) -> dict[str, str]:
        return {"reply": await agent.owner_chat(body.text)}

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
    async def approvals() -> list[dict[str, Any]]:
        return agent.store.approvals()

    @app.post("/owner/approvals/{aid}/{decision}", dependencies=[Depends(owner)])
    async def decide(aid: str, decision: str) -> dict[str, Any]:
        if decision not in ("approve", "decline"):
            raise HTTPException(400, "decision must be approve or decline")
        try:
            return await agent.decide(aid, decision == "approve")
        except PeerError as e:
            raise wrap_peer_error(e) from e

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
                "lines": [{"ts": l["ts"], "speaker": l["speaker"], "text": l["text"], "intent": l["intent"],
                           "data": l["data"], "hash": l["hash"]} for l in lines]}

    @app.get("/owner/audit", dependencies=[Depends(owner)])
    async def audit() -> list[dict[str, Any]]:
        return agent.store.audit_log()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML.replace("{{AGENT}}", agent.cfg.owner_name)

    return app


INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Nixie</title>
<style>body{font:15px system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 12px}
#log{border:1px solid #ccc;border-radius:8px;height:60vh;overflow:auto;padding:10px;white-space:pre-wrap}
.me{color:#1a4fd8}.ai{color:#222}input{padding:8px}#t{width:100%;box-sizing:border-box;margin-top:8px}</style></head>
<body><h3>Nixie - agent of {{AGENT}}</h3>
<input id="k" type="password" placeholder="owner token"> <button onclick="ap()">approvals</button>
<button onclick="cv()">agent conversations (read-only)</button>
<div id="log"></div><input id="t" placeholder="message or /help" onkeydown="if(event.key==='Enter')send()">
<script>
const L=document.getElementById('log');
function add(c,s){const d=document.createElement('div');d.className=c;d.textContent=s;L.appendChild(d);L.scrollTop=1e9}
async function call(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'Content-Type':'application/json',
'X-Owner-Token':document.getElementById('k').value},body:b?JSON.stringify(b):undefined});return r.json()}
async function send(){const t=document.getElementById('t');const v=t.value;if(!v)return;t.value='';add('me','> '+v);
try{const j=await call('/owner/chat',{text:v});add('ai',j.reply??JSON.stringify(j))}catch(e){add('ai','error: '+e)}}
async function ap(){add('ai',JSON.stringify(await call('/owner/approvals'),null,1))}
async function cv(){const list=await call('/owner/conversations');for(const c of list){
const t=await call('/owner/conversations/'+c.conv_id);add('ai','--- conversation '+c.conv_id+' with '+c.peer+
' (read-only, integrity '+(t.integrity_ok?'OK':'FAILED')+')');for(const l of t.lines)add('ai','['+l.ts.slice(11,19)+'] '+l.speaker+': '+l.text)}}
</script></body></html>"""
