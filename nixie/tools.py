"""Task execution with a strict preference order: API > CLI (shell) > browser.

A skill (one business task, e.g. "book a meeting room") lists one or more strategies.
The router always tries them in the order api, cli, browser, whatever the order in the YAML file.
The browser is used only when no API or CLI strategy is available for that skill.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from .config import AgentConfig

PRIORITY = {"api": 0, "cli": 1, "browser": 2}
OUTPUT_LIMIT = 8000


class NotAvailable(Exception):
    """Strategy cannot run here (not configured / not installed). Router tries the next one."""


class ToolError(Exception):
    pass


_PARAM = re.compile(r"\{(\w+)\}")
_ENV = re.compile(r"\$\{((?:HYNIX|NIXIE)_\w+)\}")


def render(value: Any, params: dict[str, Any]) -> Any:
    """Fill {param} and ${HYNIX_*}/${NIXIE_*} placeholders. No shell, no eval."""
    if isinstance(value, str):
        def p(m: re.Match) -> str:
            if m.group(1) not in params:
                raise ToolError(f"missing parameter: {m.group(1)}")
            return str(params[m.group(1)])
        out = _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
        return _PARAM.sub(p, out)
    if isinstance(value, list):
        return [render(v, params) for v in value]
    if isinstance(value, dict):
        return {k: render(v, params) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- shell
class ShellTool:
    """Run one allow-listed program inside the user's sandbox workspace. No shell interpretation."""

    def __init__(self, cfg: AgentConfig):
        self.allow = set(cfg.shell_allow)
        self.timeout = cfg.shell_timeout
        self.cwd = cfg.workspace
        self.home = cfg.home

    def available(self, program: str) -> bool:
        return Path(program).name in self.allow and shutil.which(program) is not None

    async def run(self, argv: list[str], timeout: int | None = None) -> dict[str, Any]:
        if not argv or not all(isinstance(a, str) and "\x00" not in a for a in argv):
            raise ToolError("argv must be a non-empty list of strings")
        prog = argv[0]
        if Path(prog).name not in self.allow or "/" in prog:
            raise ToolError(f"program not allowed: {prog!r} (allowed: {sorted(self.allow)})")
        exe = shutil.which(prog)
        if not exe:
            raise NotAvailable(f"program not installed: {prog}")
        self.cwd.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8"}
        env.update({k: v for k, v in os.environ.items() if k.startswith("HYNIX_")})
        proc = await asyncio.create_subprocess_exec(
            exe, *argv[1:], cwd=str(self.cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout or self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ToolError(f"timeout after {timeout or self.timeout}s") from None
        return {"exit_code": proc.returncode, "stdout": out.decode(errors="replace")[:OUTPUT_LIMIT],
                "stderr": err.decode(errors="replace")[:OUTPUT_LIMIT]}


# ---------------------------------------------------------------- api
class ApiTool:
    def __init__(self, cfg: AgentConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.services = cfg.services
        self.transport = transport

    def available(self, service: str) -> bool:
        s = self.services.get(service)
        return bool(s and s.base_url)

    def hosts(self) -> set[str]:
        return {(urlparse(s.base_url).hostname or "").lower() for s in self.services.values() if s.base_url}

    async def call(self, service: str, method: str, path: str, params: dict | None = None,
                   body: Any = None) -> dict[str, Any]:
        s = self.services.get(service)
        if not s or not s.base_url:
            raise NotAvailable(f"service not configured: {service}")
        headers = {}
        if s.token:
            headers[s.auth_header] = f"{s.auth_scheme} {s.token}".strip()
        try:
            async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
                r = await c.request(method.upper(), s.base_url.rstrip("/") + "/" + path.lstrip("/"),
                                    params=params, json=body, headers=headers)
        except httpx.ConnectError as e:
            raise NotAvailable(f"service unreachable: {service}: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = r.text[:OUTPUT_LIMIT]
        if r.status_code >= 400:
            raise ToolError(f"{service} {method} {path} -> HTTP {r.status_code}: {str(data)[:500]}")
        return {"status": r.status_code, "body": data}


# ---------------------------------------------------------------- browser (remote pool)
class BrowserClient:
    def __init__(self, cfg: AgentConfig, user: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = cfg.browser_pool_url
        self.token = cfg.browser_pool_token or ""
        self.user = user
        self.transport = transport

    @property
    def available(self) -> bool:
        return bool(self.url)

    async def run(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.url:
            raise NotAvailable("browser pool not configured")
        async with httpx.AsyncClient(timeout=300, transport=self.transport) as c:
            r = await c.post(self.url.rstrip("/") + "/run", json={"user": self.user, "steps": steps},
                             headers={"X-Pool-Token": self.token})
        if r.status_code != 200:
            raise ToolError(f"browser pool HTTP {r.status_code}: {r.text[:500]}")
        return r.json()


# ---------------------------------------------------------------- skills
@dataclass
class Skill:
    id: str
    title: str
    description: str
    params: dict[str, str]
    strategies: list[dict[str, Any]]
    confirm: bool = False
    model_hint: str | None = None
    optional: list[str] = field(default_factory=list)


def load_skills(path: Path | None) -> dict[str, Skill]:
    if not path or not Path(path).exists():
        return {}
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, Skill] = {}
    for s in raw.get("skills", []):
        strategies = sorted(s.get("strategies", []), key=lambda x: PRIORITY.get(x.get("type"), 9))
        out[s["id"]] = Skill(
            id=s["id"], title=s.get("title", s["id"]), description=s.get("description", ""),
            params=dict(s.get("params") or {}), strategies=strategies, confirm=bool(s.get("confirm", False)),
            model_hint=s.get("model_hint"), optional=list(s.get("optional") or []),
        )
    return out


class TaskRouter:
    def __init__(self, cfg: AgentConfig, user: str, transport: httpx.AsyncBaseTransport | None = None):
        self.shell = ShellTool(cfg)
        self.api = ApiTool(cfg, transport)
        self.browser = BrowserClient(cfg, user, transport)
        self.skills = load_skills(cfg.skills_file)

    def strategy_available(self, st: dict[str, Any]) -> bool:
        t = st.get("type")
        if t == "api":
            return self.api.available(st.get("service", ""))
        if t == "cli":
            return bool(st.get("argv")) and self.shell.available(st["argv"][0])
        if t == "browser":
            return self.browser.available
        return False

    def catalog(self) -> list[dict[str, Any]]:
        out = []
        for s in self.skills.values():
            routes = [st["type"] for st in s.strategies if self.strategy_available(st)]
            out.append({"id": s.id, "title": s.title, "description": s.description, "params": s.params,
                        "needs_owner_approval": s.confirm, "available_routes": routes})
        return out

    async def run_skill(self, skill_id: str, params: dict[str, Any]) -> dict[str, Any]:
        skill = self.skills.get(skill_id)
        if not skill:
            raise ToolError(f"unknown skill {skill_id!r}")
        missing = [p for p in skill.params if p not in params and p not in skill.optional]
        if missing:
            raise ToolError(f"missing parameters: {missing}")
        tried: list[str] = []
        for st in skill.strategies:
            t = st["type"]
            if not self.strategy_available(st):
                tried.append(f"{t}: not available")
                continue
            try:
                if t == "api":
                    res = await self.api.call(st["service"], st.get("method", "GET"), render(st["path"], params),
                                              render(st.get("query"), params), render(st.get("body"), params))
                elif t == "cli":
                    res = await self.shell.run(render(st["argv"], params))
                    if res["exit_code"] != 0:
                        raise ToolError(f"cli exit {res['exit_code']}: {res['stderr'][:300]}")
                else:
                    res = await self.browser.run(render(st["steps"], params))
                return {"status": "done", "skill": skill_id, "route": t, "tried": tried, "result": res}
            except NotAvailable as e:
                tried.append(f"{t}: {e}")
        raise ToolError(f"no route could run {skill_id}: {tried}")

    async def browse(self, steps: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        """Generic browser task. Refused when an API exists for the target host."""
        if not reason:
            raise ToolError("browse needs a reason that explains why API/CLI cannot do it")
        api_hosts = self.api.hosts()
        for s in steps:
            if s.get("action") == "goto":
                host = (urlparse(s.get("url", "")).hostname or "").lower()
                if host in api_hosts:
                    raise ToolError(f"{host} has an API; use the API skill or run_shell instead of the browser")
        return await self.browser.run(steps)
