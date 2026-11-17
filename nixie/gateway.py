"""Always-on gateway. It holds no agent state.

- Routes /u/<user>/... to that user's sandbox and starts the sandbox if it is not running.
- Serves the user's Agent Card from disk without starting the sandbox.
- A reaper task stops sandboxes that stay idle longer than the timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import MessageToDict

from .config import load_config
from .sandbox import SandboxManager, check_user

HOP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "upgrade",
               "proxy-authorization", "te", "trailer"}


def create_gateway(manager: SandboxManager, admin_token: str = "", reap_interval: float = 5) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(manager.reaper(reap_interval))
        app.state.http = httpx.AsyncClient(timeout=180, trust_env=False)
        try:
            yield
        finally:
            task.cancel()
            await app.state.http.aclose()
            await manager.stop_all()

    app = FastAPI(title="Nixie gateway", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/admin/sandboxes")
    async def sandboxes(x_admin_token: str = Header(default="")) -> dict:
        if not admin_token or not hmac.compare_digest(x_admin_token, admin_token):
            raise HTTPException(401, "bad admin token")
        return manager.status()

    @app.get("/u/{user}/.well-known/agent-card.json")
    async def card(user: str) -> JSONResponse:
        """Agent discovery does not need a running sandbox."""
        from .app import agent_card  # local import: heavy a2a imports

        try:
            home = manager.home(user)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        cfg = load_config(home / "agent.yaml")
        return JSONResponse(MessageToDict(agent_card(cfg)))

    @app.api_route("/u/{user}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(user: str, path: str, request: Request) -> Response:
        try:
            check_user(user)
            url = await manager.ensure(user)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        except (RuntimeError, TimeoutError) as e:
            raise HTTPException(503, f"sandbox start failed: {e}") from e
        manager.begin(user)
        try:
            headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
            r = await request.app.state.http.request(
                request.method, f"{url}/{path}", params=request.query_params,
                content=await request.body(), headers=headers,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"sandbox error: {e}") from e
        finally:
            manager.end(user)
        out_headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_HEADERS | {"content-encoding"}}
        return Response(content=r.content, status_code=r.status_code, headers=out_headers)

    return app


def provision_user(users_root: Path, user: str, config_yaml: str) -> Path:
    """Create the user's disk: agent.yaml + data/ + workspace/ + browser/."""
    home = Path(users_root) / check_user(user)
    for d in ("", "data", "workspace", "browser"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / "agent.yaml").write_text(config_yaml, encoding="utf-8")
    return home
