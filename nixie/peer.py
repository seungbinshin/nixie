"""Outbound A2A calls to other owners' agents."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from google.protobuf.json_format import ParseDict
from google.protobuf import struct_pb2

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

from .config import Peer


class PeerError(Exception):
    pass


def build_message(
    from_agent: str, token: str, intent: str, data: dict[str, Any], text: str = "", context_id: str | None = None
) -> Message:
    parts = []
    if text:
        parts.append(Part(text=text))  # human-readable line of the agent conversation
    parts.append(Part(data=ParseDict({"intent": intent, "data": data}, struct_pb2.Value()), media_type="application/json"))
    msg = Message(
        role=Role.ROLE_USER,
        message_id=str(uuid.uuid4()),
        context_id=context_id or str(uuid.uuid4()),
        parts=parts,
    )
    msg.metadata.update({"from_agent": from_agent, "token": token})
    return msg


async def send_intent(
    from_agent: str,
    peer: Peer,
    intent: str,
    data: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
    text: str = "",
    context_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Send one intent to a peer agent. Return (data artifact, human-readable reply text)."""
    httpx_client = httpx.AsyncClient(timeout=60, transport=transport)
    try:
        client = await create_client(
            peer.url,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )
        request = SendMessageRequest(message=build_message(from_agent, peer.token, intent, data, text, context_id))
        result: dict[str, Any] | None = None
        reply_text = ""
        reason = ""
        async for event in client.send_message(request):
            task = None
            if event.HasField("task"):
                task = event.task
            if task is not None:
                for art in task.artifacts:
                    parts = get_data_parts(art.parts)
                    if parts:
                        result = parts[0]
                    reply_text = " ".join(get_text_parts(art.parts)) or reply_text
                if task.status.state in (TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_FAILED):
                    reason = " ".join(get_text_parts(task.status.message.parts)) or TaskState.Name(task.status.state)
            if event.HasField("artifact_update"):
                parts = get_data_parts(event.artifact_update.artifact.parts)
                if parts:
                    result = parts[0]
            if event.HasField("message"):
                parts = get_data_parts(event.message.parts)
                if parts:
                    result = parts[0]
        await client.close()
    finally:
        await httpx_client.aclose()
    if reason:
        raise PeerError(f"{peer.name}: {reason}")
    if result is None:
        raise PeerError(f"{peer.name}: empty response")
    return result, reply_text
