"""Shared browser worker pool.

Agent sandboxes never run Chromium. When a task really needs clicks, the agent leases a worker
from this pool for one job:
  1. take a free worker (shared Chromium process),
  2. open a fresh, isolated browser context with ONLY that user's profile (cookies + localStorage,
     <users_root>/<user>/browser/storage_state.json),
  3. run the steps,
  4. save the profile back to the user's disk, close the context, return the worker.

Steps are a small fixed DSL (no arbitrary JavaScript). Network requests outside `allow_domains`
are blocked inside the context.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .sandbox import check_user

log = logging.getLogger("nixie.browser")

ACTIONS = {"goto", "click", "fill", "press", "wait_for", "text", "title", "url", "select"}
MAX_STEPS = 50


class Step(BaseModel):
    action: str
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    key: str | None = None
    timeout_ms: int = 15000


class RunIn(BaseModel):
    user: str
    steps: list[Step] = Field(max_length=MAX_STEPS)


def host_allowed(url: str, allow: list[str] | None) -> bool:
    if not allow:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allow)


class BrowserPool:
    def __init__(self, users_root: Path, size: int = 2, allow_domains: list[str] | None = None,
                 executable_path: str | None = None, headless: bool = True):
        self.users_root = Path(users_root)
        self.size = size
        self.allow = [d.lower() for d in allow_domains] if allow_domains else None
        self.executable_path = executable_path
        self.headless = headless
        self._pw = None
        self._free: asyncio.Queue | None = None
        self._browsers: list[Any] = []
        self.leases = 0
        self.in_use = 0
        self.waiting = 0

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._free = asyncio.Queue()
        for i in range(self.size):
            b = await self._pw.chromium.launch(headless=self.headless, executable_path=self.executable_path)
            self._browsers.append(b)
            self._free.put_nowait(i)
        log.info("browser pool ready: %d workers", self.size)

    async def close(self) -> None:
        for b in self._browsers:
            await b.close()
        if self._pw:
            await self._pw.stop()

    def profile_path(self, user: str) -> Path:
        d = self.users_root / check_user(user) / "browser"
        d.mkdir(parents=True, exist_ok=True)
        return d / "storage_state.json"

    async def run(self, user: str, steps: list[Step]) -> dict[str, Any]:
        for s in steps:
            if s.action not in ACTIONS:
                raise ValueError(f"unsupported action {s.action!r}")
            if s.action == "goto" and not (s.url and host_allowed(s.url, self.allow)):
                raise PermissionError(f"domain not allowed: {s.url}")
        profile = self.profile_path(user)
        self.waiting += 1
        worker = await self._free.get()
        self.waiting -= 1
        self.in_use += 1
        self.leases += 1
        t0 = time.monotonic()
        ctx = None
        try:
            browser = self._browsers[worker]
            ctx = await browser.new_context(storage_state=str(profile) if profile.exists() else None)
            if self.allow:
                async def guard(route):
                    if host_allowed(route.request.url, self.allow) or route.request.url.startswith(("data:", "about:")):
                        await route.continue_()
                    else:
                        await route.abort()
                await ctx.route("**/*", guard)
            page = await ctx.new_page()
            out: list[Any] = []
            for s in steps:
                t = s.timeout_ms
                if s.action == "goto":
                    await page.goto(s.url, timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "click":
                    await page.click(s.selector, timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "fill":
                    await page.fill(s.selector, s.value or "", timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "select":
                    await page.select_option(s.selector, s.value, timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "press":
                    await page.press(s.selector, s.key or "Enter", timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "wait_for":
                    await page.wait_for_selector(s.selector, timeout=t)
                    out.append({"status": "ok"})
                elif s.action == "text":
                    out.append({"text": (await page.inner_text(s.selector, timeout=t)).strip()})
                elif s.action == "title":
                    out.append({"text": await page.title()})
                elif s.action == "url":
                    out.append({"text": page.url})
            tmp = profile.with_suffix(".tmp")
            await ctx.storage_state(path=str(tmp))
            os.replace(tmp, profile)  # profile goes back to the user's disk
            return {"worker": worker, "results": out, "elapsed_s": round(time.monotonic() - t0, 2)}
        finally:
            if ctx is not None:
                await ctx.close()
            self.in_use -= 1
            self._free.put_nowait(worker)

    def status(self) -> dict[str, Any]:
        return {"workers": self.size, "in_use": self.in_use, "waiting": self.waiting, "leases": self.leases,
                "allow_domains": self.allow}


def create_pool_app(pool: BrowserPool, token: str) -> FastAPI:
    import contextlib

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await pool.start()
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Nixie browser pool", lifespan=lifespan)

    def auth(x_pool_token: str) -> None:
        if not token or not hmac.compare_digest(x_pool_token or "", token):
            raise HTTPException(401, "bad pool token")

    @app.post("/run")
    async def run(body: RunIn, x_pool_token: str = Header(default="")) -> dict[str, Any]:
        auth(x_pool_token)
        try:
            return await pool.run(body.user, body.steps)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 - playwright timeouts etc.
            raise HTTPException(422, f"browser step failed: {type(e).__name__}: {str(e)[:300]}") from e

    @app.get("/status")
    async def status(x_pool_token: str = Header(default="")) -> dict[str, Any]:
        auth(x_pool_token)
        return pool.status()

    return app
