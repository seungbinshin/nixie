"""Owner rules: who may ask what, and which actions need owner approval."""

from __future__ import annotations

import hmac

from .config import AgentConfig, Fact, Peer

SCOPE_AVAILABILITY = "availability"
SCOPE_PROFILE_TRUSTED = "profile.trusted"
SCOPE_AUTO_ACCEPT = "meeting.auto_accept"
SCOPE_MEETING = "meeting"

INTENT_SCOPE = {
    "availability.query": SCOPE_AVAILABILITY,
    "meeting.propose": SCOPE_MEETING,
    "meeting.confirm": SCOPE_MEETING,
    "meeting.decline": SCOPE_MEETING,
    "agent.ask": None,  # any trusted peer; answer limited by fact visibility
}


class PolicyError(Exception):
    pass


class Policy:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg

    def authenticate(self, from_agent: str, token: str) -> Peer:
        peer = self.cfg.peers.get(from_agent)
        if peer is None or not peer.token or not hmac.compare_digest(peer.token, token or ""):
            raise PolicyError(f"unknown or unauthenticated agent: {from_agent!r}")
        return peer

    def authorize(self, peer: Peer, intent: str) -> None:
        if intent not in INTENT_SCOPE:
            raise PolicyError(f"unsupported intent: {intent}")
        scope = INTENT_SCOPE[intent]
        if scope is None:
            return
        if scope == SCOPE_MEETING:
            if not ({SCOPE_MEETING, SCOPE_AUTO_ACCEPT} & set(peer.scopes)):
                raise PolicyError(f"{peer.name} may not send meeting requests")
            return
        if scope not in peer.scopes:
            raise PolicyError(f"{peer.name} lacks scope {scope}")

    def needs_approval(self, peer: Peer, intent: str) -> bool:
        return intent == "meeting.propose" and SCOPE_AUTO_ACCEPT not in peer.scopes

    def shareable_facts(self, peer: Peer, extra: list[Fact] | None = None) -> list[str]:
        allowed = {"public"}
        if SCOPE_PROFILE_TRUSTED in peer.scopes:
            allowed.add("trusted")
        facts = list(self.cfg.facts) + list(extra or [])
        return [f.text for f in facts if f.visibility in allowed]
