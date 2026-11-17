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
class ModelTier:
    name: str
    base_url: str | None
    api_key: str | None
    model: str
    extra: dict = field(default_factory=dict)  # e.g. {"reasoning_effort": "high"} for Kimi K3.1 Pro

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)


@dataclass
class ModelSettings:
    tiers: dict[str, ModelTier]  # ordered weakest -> strongest, e.g. fast, pro
    max_tier: str = "pro"
    escalate_after_tool_steps: int = 2
    long_context_chars: int = 12000


@dataclass
class ServiceSettings:
    name: str
    base_url: str | None
    token: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"


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
    home: Path = Path(".")
    models: ModelSettings | None = None
    services: dict[str, ServiceSettings] = field(default_factory=dict)
    shell_allow: list[str] = field(default_factory=list)
    shell_timeout: int = 60
    browser_pool_url: str | None = None
    browser_pool_token: str | None = None
    skills_file: Path | None = None
    persona: str = "hynix"

    @property
    def workspace(self) -> Path:
        return self.home / "workspace"

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
    cfg_path = Path(path).resolve()
    home = Path(_env(raw.get("home")) or os.environ.get("NIXIE_HOME") or cfg_path.parent)

    # model tiers: legacy `llm:` becomes the `fast` tier
    mraw = raw.get("models") or {}
    tiers: dict[str, ModelTier] = {}
    for tname in ("fast", "pro"):
        t = mraw.get(tname)
        if t is None and tname == "fast":
            tiers["fast"] = ModelTier("fast", llm.base_url, llm.api_key, llm.model)
            continue
        if t is None:
            continue
        tiers[tname] = ModelTier(
            tname, _env(t.get("base_url")), _env(t.get("api_key")), _env(t.get("model")) or "",
            dict(t.get("extra") or {}),
        )
    models = ModelSettings(
        tiers=tiers,
        max_tier=mraw.get("max_tier", "pro"),
        escalate_after_tool_steps=int(mraw.get("escalate_after_tool_steps", 2)),
        long_context_chars=int(mraw.get("long_context_chars", 12000)),
    )
    services = {
        n: ServiceSettings(n, _env(v.get("base_url")), _env(v.get("token")),
                           v.get("auth_header", "Authorization"), v.get("auth_scheme", "Bearer"))
        for n, v in (raw.get("services") or {}).items()
    }
    tools = raw.get("tools") or {}
    bp = raw.get("browser_pool") or {}
    skills_file = raw.get("skills_file")
    if skills_file:
        sp = Path(_env(skills_file) or "")
        skills_file = sp if sp.is_absolute() else (cfg_path.parent / sp)
    else:
        default = Path(__file__).resolve().parent / "skills" / "hynix-work.yaml"
        skills_file = default if default.exists() else None

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
        db_path=_env(raw.get("db_path")) or str(home / "data" / "nixie.db"),
        home=home,
        models=models,
        services=services,
        shell_allow=list(tools.get("shell_allow") or []),
        shell_timeout=int(tools.get("shell_timeout", 60)),
        browser_pool_url=_env(bp.get("url")) or os.environ.get("NIXIE_BROWSER_POOL_URL") or None,
        browser_pool_token=_env(bp.get("token")) or os.environ.get("NIXIE_BROWSER_POOL_TOKEN") or None,
        skills_file=skills_file,
        persona=raw.get("persona", "hynix"),
    )
