"""Agent configuration (one YAML file per owner)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass
class Peer:
    name: str
    url: str
    token: str  # shared secret for this agent pair
    scopes: list[str] = field(default_factory=list)  # availability, profile.trusted, meeting.auto_accept


@dataclass
class Fact:
    text: str
    visibility: str = "private"  # private | trusted | public


@dataclass
class LLMSettings:
    base_url: str | None = None  # OpenAI-compatible endpoint, e.g. http://host:8000/v1
    api_key: str | None = None
    model: str = "gpt-4o-mini"


@dataclass
class AgentConfig:
    agent_id: str
    owner_name: str
    host: str
    port: int
    public_url: str
    owner_token: str
    timezone: str
    work_hours: tuple[int, int]
    busy: list[tuple[datetime, datetime]]
    facts: list[Fact]
    peers: dict[str, Peer]
    llm: LLMSettings
    db_path: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _env(value: str | None) -> str | None:
    """Expand ${VAR} references so secrets stay out of the YAML file."""
    if value is None:
        return None
    out = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), str(value))
    return out or None


def load_config(path: str | Path) -> AgentConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    tzname = raw.get("timezone", "Asia/Seoul")
    tz = ZoneInfo(tzname)

    def parse_dt(s: str) -> datetime:
        dt = datetime.fromisoformat(str(s))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)

    busy = [(parse_dt(b["start"]), parse_dt(b["end"])) for b in raw.get("busy", [])]
    facts = [Fact(text=f["text"], visibility=f.get("visibility", "private")) for f in raw.get("facts", [])]
    peers = {
        name: Peer(name=name, url=p["url"].rstrip("/"), token=_env(p["token"]) or "", scopes=list(p.get("scopes", [])))
        for name, p in (raw.get("peers") or {}).items()
    }
    llm_raw = raw.get("llm") or {}
    llm = LLMSettings(
        base_url=_env(llm_raw.get("base_url")) or os.environ.get("NIXIE_LLM_BASE_URL") or None,
        api_key=_env(llm_raw.get("api_key")) or os.environ.get("NIXIE_LLM_API_KEY") or None,
        model=_env(llm_raw.get("model")) or os.environ.get("NIXIE_LLM_MODEL") or "gpt-4o-mini",
    )
    host = raw.get("host", "127.0.0.1")
    port = int(raw.get("port", 8700))
    wh = raw.get("work_hours", [9, 18])
    return AgentConfig(
        agent_id=raw["agent_id"],
        owner_name=raw.get("owner_name", raw["agent_id"]),
        host=host,
        port=port,
        public_url=(_env(raw.get("public_url")) or f"http://{host}:{port}").rstrip("/"),
        owner_token=_env(raw["owner_token"]) or "",
        timezone=tzname,
        work_hours=(int(wh[0]), int(wh[1])),
        busy=busy,
        facts=facts,
        peers=peers,
        llm=llm,
        db_path=_env(raw.get("db_path")) or f"./data/{raw['agent_id']}.db",
    )
