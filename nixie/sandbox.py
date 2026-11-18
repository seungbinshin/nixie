"""On-demand per-user sandboxes.

Only the user's disk (home directory) is permanent. A sandbox (process, container or microVM)
starts when a request for that user arrives, and stops after an idle timeout.

Backends
- ProcessBackend: local subprocess. For development and tests.
- ContainerBackend: any docker-compatible CLI (docker, nerdctl, podman) with a selectable runtime:
    runc (default), io.containerd.kata.v2 (Kata Containers microVM), runsc (gVisor),
    aws.firecracker (firecracker-containerd; use cli="nerdctl" and its containerd socket).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger("nixie.sandbox")

USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def check_user(user: str) -> str:
    if not USER_RE.match(user or ""):
        raise ValueError(f"invalid user id: {user!r}")
    return user


class SandboxBackend(Protocol):
    name: str

    async def start(self, user: str, home: Path, env: dict[str, str] | None = None) -> str:
        """Start the sandbox for this user with extra environment `env`. Return its base URL."""

    async def stop(self, user: str) -> None: ...

    async def alive(self, user: str) -> bool: ...


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Environment that a sandbox may receive. Everything else from the gateway environment is dropped.
PASS_ENV_PREFIXES = ("NIXIE_", "HYNIX_")
# Gateway secrets. A sandbox must never see them (a sandbox could then act as the gateway or the SSO proxy).
GATEWAY_ONLY_ENV = {"NIXIE_ADMIN_TOKEN", "NIXIE_SESSION_SECRET", "NIXIE_PROXY_SECRET", "NIXIE_GATEWAY_SECRET"}
# Per-start secret: the gateway sends it with owner requests; the agent trusts the identity header only with it.
GATEWAY_SECRET_ENV = "NIXIE_GATEWAY_SECRET"


def sandbox_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.startswith(PASS_ENV_PREFIXES) and k not in GATEWAY_ONLY_ENV}
    env.update(extra or {})
    return env


class ProcessBackend:
    name = "process"

    def __init__(self, python: str = sys.executable):
        self.python = python
        self.procs: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, user: str, home: Path, env: dict[str, str] | None = None) -> str:
        port = _free_port()
        env = {"PATH": os.environ.get("PATH", ""),
               **sandbox_env({"NIXIE_USER": user, "NIXIE_HOME": str(home), **(env or {})})}
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        log_file = open(home / "sandbox.log", "ab")  # noqa: SIM115 - kept open for the child
        proc = await asyncio.create_subprocess_exec(
            self.python, "-m", "nixie.cli", "serve", "--config", str(home / "agent.yaml"),
            "--host", "127.0.0.1", "--port", str(port),
            cwd=str(Path(__file__).resolve().parents[1]), env=env, stdout=log_file, stderr=log_file,
        )
        self.procs[user] = proc
        return f"http://127.0.0.1:{port}"

    async def stop(self, user: str) -> None:
        proc = self.procs.pop(user, None)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()

    async def alive(self, user: str) -> bool:
        proc = self.procs.get(user)
        return bool(proc and proc.returncode is None)


@dataclass
class ContainerBackend:
    image: str = "nixie:latest"
    cli: str = "docker"
    cli_global_args: list[str] = field(default_factory=list)  # e.g. ["--address", "/run/firecracker-containerd/containerd.sock"]
    runtime: str | None = None  # None/runc | io.containerd.kata.v2 | runsc | aws.firecracker
    network: str | None = None  # set when the gateway runs in the same container network
    cpus: str = "1"
    memory: str = "1g"
    mount_point: str = "/home/nixie"
    run_as_disk_owner: bool = True  # --user <uid>:<gid> of the user's home dir, so the disk stays writable
    name: str = "container"

    def _cname(self, user: str) -> str:
        return f"nixie-u-{user}"

    async def _run(self, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.cli, *self.cli_global_args, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
        )
        out, err = await proc.communicate()
        if check and proc.returncode != 0:
            raise RuntimeError(f"{self.cli} {' '.join(args[:2])} failed: {err.decode().strip()}")
        return out.decode().strip()

    async def start(self, user: str, home: Path, env: dict[str, str] | None = None) -> str:
        name = self._cname(user)
        await self._run("rm", "-f", name, check=False)
        args = ["run", "-d", "--rm", "--name", name, "--cpus", self.cpus, "--memory", self.memory,
                "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "-v", f"{home}:{self.mount_point}", "-e", f"NIXIE_USER={user}", "-e", f"NIXIE_HOME={self.mount_point}"]
        for k, v in sandbox_env().items():
            args += ["-e", f"{k}={v}"]
        for k in env or {}:
            args += ["-e", k]  # value comes from the CLI process environment, so it is not on the command line
        if self.run_as_disk_owner:
            st = home.stat()
            args += ["--user", f"{st.st_uid}:{st.st_gid}"]
        if self.runtime:
            args += ["--runtime", self.runtime]
        if self.network:
            args += ["--network", self.network]
        else:
            args += ["-p", "127.0.0.1::8700"]
        args += [self.image, f"{self.mount_point}/agent.yaml", "--host", "0.0.0.0", "--port", "8700"]
        await self._run(*args, env=env)
        if self.network:
            return f"http://{name}:8700"
        port = (await self._run("port", name, "8700/tcp")).splitlines()[0].rsplit(":", 1)[1]
        return f"http://127.0.0.1:{port}"

    async def stop(self, user: str) -> None:
        await self._run("stop", "-t", "5", self._cname(user), check=False)

    async def alive(self, user: str) -> bool:
        out = await self._run("inspect", "-f", "{{.State.Running}}", self._cname(user), check=False)
        return out == "true"


@dataclass
class SandboxState:
    url: str
    started: float
    last_used: float
    in_flight: int = 0
    secret: str = ""  # gateway secret of this start (see GATEWAY_SECRET_ENV)


class SandboxManager:
    """Start sandboxes on demand, track activity, stop idle ones."""

    def __init__(self, backend: SandboxBackend, users_root: Path, idle_seconds: float = 300,
                 max_running: int | None = None, start_timeout: float = 60):
        self.backend = backend
        self.users_root = Path(users_root)
        self.idle_seconds = idle_seconds
        self.max_running = max_running
        self.start_timeout = start_timeout
        self.running: dict[str, SandboxState] = {}
        self.starts: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def home(self, user: str) -> Path:
        home = self.users_root / check_user(user)
        if not (home / "agent.yaml").is_file():
            raise FileNotFoundError(f"user {user!r} is not provisioned")
        return home

    async def ensure(self, user: str) -> str:
        home = self.home(user)
        lock = self._locks.setdefault(user, asyncio.Lock())
        async with lock:
            st = self.running.get(user)
            if st and await self.backend.alive(user):
                st.last_used = time.monotonic()
                return st.url
            if st:
                self.running.pop(user, None)
            await self._make_room()
            for d in ("data", "workspace", "browser"):
                (home / d).mkdir(exist_ok=True)
            secret = secrets.token_urlsafe(32)
            url = await self.backend.start(user, home, {GATEWAY_SECRET_ENV: secret})
            await self._wait_ready(user, url)
            now = time.monotonic()
            self.running[user] = SandboxState(url=url, started=now, last_used=now, secret=secret)
            self.starts[user] = self.starts.get(user, 0) + 1
            log.info("sandbox up: %s (%s) at %s", user, self.backend.name, url)
            return url

    async def _wait_ready(self, user: str, url: str) -> None:
        deadline = time.monotonic() + self.start_timeout
        async with httpx.AsyncClient(timeout=2, trust_env=False) as c:
            while time.monotonic() < deadline:
                try:
                    if (await c.get(f"{url}/.well-known/agent-card.json")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                if not await self.backend.alive(user):
                    raise RuntimeError(f"sandbox for {user} exited during start")
                await asyncio.sleep(0.2)
        await self.backend.stop(user)
        raise TimeoutError(f"sandbox for {user} not ready in {self.start_timeout}s")

    async def _make_room(self) -> None:
        if not self.max_running or len(self.running) < self.max_running:
            return
        idle = sorted((s.last_used, u) for u, s in self.running.items() if s.in_flight == 0)
        if idle:
            await self.stop(idle[0][1])

    def begin(self, user: str) -> None:
        st = self.running.get(user)
        if st:
            st.in_flight += 1
            st.last_used = time.monotonic()

    def end(self, user: str) -> None:
        st = self.running.get(user)
        if st:
            st.in_flight = max(0, st.in_flight - 1)
            st.last_used = time.monotonic()

    async def stop(self, user: str) -> None:
        self.running.pop(user, None)
        await self.backend.stop(user)
        log.info("sandbox down: %s", user)

    async def reap_once(self) -> list[str]:
        now = time.monotonic()
        victims = [u for u, s in self.running.items() if s.in_flight == 0 and now - s.last_used > self.idle_seconds]
        for u in victims:
            lock = self._locks.setdefault(u, asyncio.Lock())
            async with lock:
                st = self.running.get(u)
                if st and st.in_flight == 0 and time.monotonic() - st.last_used > self.idle_seconds:
                    await self.stop(u)
        return victims

    async def reaper(self, interval: float = 5) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.reap_once()
            except Exception:  # noqa: BLE001
                log.exception("reaper error")

    async def stop_all(self) -> None:
        for u in list(self.running):
            await self.stop(u)

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "backend": self.backend.name,
            "idle_seconds": self.idle_seconds,
            "running": {u: {"url": s.url, "idle_for": round(now - s.last_used, 1), "in_flight": s.in_flight}
                        for u, s in self.running.items()},
            "starts": dict(self.starts),
        }
