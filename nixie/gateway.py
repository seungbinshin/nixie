"""Always-on gateway. It holds no agent state.

- Routes /u/<user>/... to that user's sandbox and starts the sandbox if it is not running.
- Serves the user's Agent Card from disk without starting the sandbox.
- A reaper task stops sandboxes that stay idle longer than the timeout.
- Pushes real-time changes (/u/<user>/owner/stream, Server-Sent Events) from the user's disk. The stream does
  not wake or keep alive the sandbox, so an agent still stops when idle while the web UI stays open.

Authentication is separate for each path:

| path                      | who                | credential                                                      |
|---------------------------|--------------------|-----------------------------------------------------------------|
| /u/<user>/owner/...       | person (owner)     | SSO proxy header X-Auth-Request-User + X-Nixie-Proxy-Secret,    |
|                           |                    | or nixie_session cookie, or X-Owner-Token (by --auth mode)      |
| /u/<user>/a2a/...         | other agents       | peer token inside the A2A message (checked by the agent)        |
| /u/<user>/ingest/...      | in-house adapters  | X-Ingest-Token (checked by the agent)                           |
| /auth/...                 | person             | as /owner                                                       |
| /admin/...                | operator           | X-Admin-Token                                                   |

The gateway never forwards a person's credentials to a sandbox. For an owner request it sends the identity
(X-Auth-Request-User) together with the per-start gateway secret of that sandbox. Other sandboxes on the same
network do not know that secret, so they cannot forge the identity.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from google.protobuf.json_format import MessageToDict

from .config import AgentConfig, load_config
from .profile import load_profile
from .sandbox import SandboxManager, check_user
from .store import read_changes

log = logging.getLogger("nixie.gateway")

HOP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "upgrade",
               "proxy-authorization", "te", "trailer"}
# Never forwarded to a sandbox: credentials of the person and identity headers from outside.
CREDENTIAL_HEADERS = {"cookie", "authorization", "x-owner-token", "x-auth-request-user", "x-auth-request-email",
                      "x-auth-request-preferred-username", "x-auth-request-groups", "x-auth-request-access-token",
                      "x-forwarded-user", "x-forwarded-email", "x-forwarded-preferred-username",
                      "x-forwarded-access-token", "x-nixie-gateway", "x-nixie-proxy-secret", "x-admin-token"}
SESSION_COOKIE = "nixie_session"
CSRF_HEADER = "x-nixie-request"  # custom header: a cross-site page cannot send it without CORS, which is never allowed
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_MODES = ("token", "sso", "both")

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; "
                                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@dataclass
class Identity:
    user: str  # nixie user id (path segment of /u/<user>)
    sso_user: str  # identity the agent expects in X-Auth-Request-User
    method: str  # sso | session | token


@dataclass
class GatewaySettings:
    auth_mode: str = "token"  # token: dev/pilot before SSO; sso: target; both: transition
    proxy_secret: str = ""  # shared with the SSO proxy; proves that X-Auth-Request-User came from the proxy
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    session_ttl: int = 8 * 3600
    cookie_secure: bool = True
    login_url: str | None = "/oauth2/start"  # where the web UI sends a person without a session
    logout_url: str = "/oauth2/sign_out?rd=%2F"  # ends the SSO proxy session
    web_root: Path | None = None  # static build of the web UI (production: served by the proxy instead)
    admin_token: str = ""
    stream_poll: float = 1.0
    stream_max_age: float = 1800  # the browser reconnects, and the proxy checks the SSO session again

    def __post_init__(self) -> None:
        if self.auth_mode not in AUTH_MODES:
            raise ValueError(f"auth mode must be one of {AUTH_MODES}")
        if self.auth_mode in ("sso", "both") and not self.proxy_secret:
            raise ValueError("SSO mode needs NIXIE_PROXY_SECRET (the proxy sends it with X-Auth-Request-User)")

    @classmethod
    def from_env(cls, **kw) -> GatewaySettings:
        env = os.environ
        if env.get("NIXIE_SESSION_SECRET"):
            kw.setdefault("session_secret", env["NIXIE_SESSION_SECRET"].encode())
        kw.setdefault("proxy_secret", env.get("NIXIE_PROXY_SECRET", ""))
        kw.setdefault("admin_token", env.get("NIXIE_ADMIN_TOKEN", ""))
        return cls(**kw)


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a).encode(), str(b).encode())


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    """Signed, expiring tokens (HMAC-SHA256) for the session cookie and one-time login links."""

    def __init__(self, key: bytes):
        self.key = key

    def sign(self, purpose: str, payload: dict, ttl: int) -> str:
        body = _b64(json.dumps({**payload, "p": purpose, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
        mac = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{mac}"

    def verify(self, purpose: str, token: str | None) -> dict | None:
        if not token or "." not in token:
            return None
        body, mac = token.rsplit(".", 1)
        want = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, want):
            return None
        try:
            data = json.loads(_unb64(body))
        except ValueError:
            return None
        if data.get("p") != purpose or int(data.get("exp", 0)) < time.time():
            return None
        return data


class Directory:
    """Who owns which agent. Read from <users_root>/<user>/agent.yaml (sso_user; default agent_id)."""

    def __init__(self, users_root: Path):
        self.root = Path(users_root)
        self._cache: dict[str, tuple[float, AgentConfig]] = {}

    def config(self, user: str) -> AgentConfig:
        path = self.root / check_user(user) / "agent.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"user {user!r} is not provisioned")
        stamp = max(path.stat().st_mtime, (path.parent / "secrets.env").stat().st_mtime
                    if (path.parent / "secrets.env").exists() else 0)
        hit = self._cache.get(user)
        if hit and hit[0] == stamp:
            return hit[1]
        cfg = load_config(path)
        self._cache[user] = (stamp, cfg)
        return cfg

    def users(self) -> list[str]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            try:
                if (d / "agent.yaml").is_file() and check_user(d.name):
                    out.append(d.name)
            except ValueError:
                continue
        return out

    def user_for_sso(self, sso_user: str) -> str | None:
        for u in self.users():
            try:
                if self.config(u).sso_user == sso_user:
                    return u
            except (OSError, KeyError, ValueError):
                log.exception("bad config for %s", u)
        return None


def create_gateway(manager: SandboxManager, admin_token: str = "", reap_interval: float = 5,
                   settings: GatewaySettings | None = None) -> FastAPI:
    st = settings or GatewaySettings(admin_token=admin_token)
    if admin_token and not st.admin_token:
        st.admin_token = admin_token
    directory = Directory(manager.users_root)
    signer = Signer(st.session_secret)
    used_links: dict[str, float] = {}

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

    app = FastAPI(title="Nixie gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # ------------------------------------------------------------------ authentication
    def authenticate(request: Request, user: str | None = None) -> Identity:
        """Identify the person. `user` is the path user (for the owner token)."""
        h = request.headers
        if st.auth_mode in ("sso", "both"):
            sub = h.get("x-auth-request-user")
            if sub:
                if not _same(h.get("x-nixie-proxy-secret"), st.proxy_secret):
                    log.warning("identity header without proxy secret from %s", request.client)
                    raise HTTPException(403, "identity header not from the SSO proxy")
                owner = directory.user_for_sso(sub)
                if not owner:
                    log.warning("no agent for SSO identity %r (set sso_user in the pilot plan)", sub)
                    raise HTTPException(403, "no agent for this identity")
                return Identity(owner, sub, "sso")
            if st.auth_mode == "sso":
                raise HTTPException(401, "SSO session required")
        sess = signer.verify("session", request.cookies.get(SESSION_COOKIE))
        if sess:
            try:
                cfg = directory.config(sess["u"])
            except (FileNotFoundError, ValueError):
                cfg = None
            if cfg:
                return Identity(sess["u"], cfg.sso_user, "session")
        token = h.get("x-owner-token")
        if token and user:
            try:
                cfg = directory.config(user)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(404, str(e)) from e
            if _same(token, cfg.owner_token):
                return Identity(user, cfg.sso_user, "token")
        raise HTTPException(401, "authentication required")

    def owner_identity(request: Request, user: str) -> Identity:
        ident = authenticate(request, user)
        if ident.user != user:
            raise HTTPException(403, "this agent belongs to another person")
        if request.method in UNSAFE and ident.method in ("sso", "session") and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, f"missing {CSRF_HEADER} header")
        return ident

    def unauthorized(exc: HTTPException) -> JSONResponse:
        body = {"detail": exc.detail}
        if exc.status_code == 401:
            body["login_url"] = st.login_url if st.auth_mode != "token" else None
        return JSONResponse(body, status_code=exc.status_code, headers={"Cache-Control": "no-store"})

    def set_session(resp: Response, user: str) -> None:
        resp.set_cookie(SESSION_COOKIE, signer.sign("session", {"u": user}, st.session_ttl), max_age=st.session_ttl,
                        httponly=True, secure=st.cookie_secure, samesite="strict", path="/")

    # ------------------------------------------------------------------ health / admin / discovery
    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/admin/sandboxes")
    async def sandboxes(x_admin_token: str = Header(default="")) -> dict:
        if not _same(x_admin_token, st.admin_token):
            raise HTTPException(401, "bad admin token")
        return manager.status()

    @app.get("/u/{user}/.well-known/agent-card.json")
    async def card(user: str) -> JSONResponse:
        """Agent discovery does not need a running sandbox."""
        from .app import agent_card  # local import: heavy a2a imports

        try:
            cfg = directory.config(user)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        return JSONResponse(MessageToDict(agent_card(cfg, load_profile(manager.users_root / user))))

    # ------------------------------------------------------------------ session endpoints for the web UI
    @app.get("/auth/me")
    async def me(request: Request) -> Response:
        """Who is signed in. Reads the user's disk only; does not start the sandbox."""
        try:
            ident = authenticate(request)
        except HTTPException as e:
            return unauthorized(e)
        cfg = directory.config(ident.user)
        return JSONResponse({
            "user": ident.user, "sso_user": ident.sso_user, "name": cfg.owner_name, "auth": ident.method,
            "timezone": cfg.timezone, "api": f"/u/{ident.user}/owner", "stream": f"/u/{ident.user}/owner/stream",
            "logout_url": "/auth/logout", "profile": load_profile(manager.users_root / ident.user),
        }, headers={"Cache-Control": "no-store"})

    @app.post("/auth/session")
    async def session(request: Request, user: str, x_owner_token: str = Header(default="")) -> Response:
        """Owner token -> nixie_session cookie (token/both modes; for tools and tests, not a login screen)."""
        if st.auth_mode == "sso":
            raise HTTPException(404, "not available in SSO mode")
        try:
            cfg = directory.config(user)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(401, "authentication required") from e
        if not _same(x_owner_token, cfg.owner_token):
            raise HTTPException(401, "authentication required")
        resp = JSONResponse({"user": user})
        set_session(resp, user)
        return resp

    @app.get("/auth/link")
    async def login_link(t: str = "") -> Response:
        """One-time login link (token/both modes, before SSO is live). `nixie login-link` makes it."""
        if st.auth_mode == "sso":
            raise HTTPException(404, "not available in SSO mode")
        data = signer.verify("link", t)
        now = time.time()
        for k, exp in list(used_links.items()):
            if exp < now:
                used_links.pop(k, None)
        if not data or data.get("n") in used_links:
            raise HTTPException(401, "link is not valid or was already used")
        used_links[data["n"]] = float(data["exp"])
        resp = RedirectResponse("/", status_code=303)
        set_session(resp, data["u"])
        return resp

    @app.get("/auth/logout")
    async def logout() -> Response:
        """End both sessions: nixie_session here, then the SSO proxy session (/oauth2/sign_out)."""
        target = st.logout_url if st.auth_mode != "token" else "/"
        resp = RedirectResponse(target, status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/", secure=st.cookie_secure, httponly=True, samesite="strict")
        resp.headers["Clear-Site-Data"] = '"cache", "storage"'
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ real-time changes (no sandbox wake-up)
    @app.get("/u/{user}/owner/stream")
    async def stream(user: str, request: Request, since: int | None = None) -> Response:
        try:
            check_user(user)
            owner_identity(request, user)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except HTTPException as e:
            return unauthorized(e)
        db = manager.users_root / user / "data" / "nixie.db"
        last_id = request.headers.get("last-event-id")
        start_from = int(last_id) if last_id and last_id.isdigit() else since

        async def events():
            top, _ = await asyncio.to_thread(read_changes, db, 0, 0)
            last = top if start_from is None else int(start_from)
            yield f"retry: 3000\nevent: hello\nid: {top}\ndata: {json.dumps({'seq': top})}\n\n"
            if last > top:  # disk was reset
                last = top
            began = ping = time.monotonic()
            while time.monotonic() - began < st.stream_max_age:
                top, rows = await asyncio.to_thread(read_changes, db, last, 200)
                if top < last:
                    last = top
                    yield f"event: resync\nid: {top}\ndata: {json.dumps({'seq': top})}\n\n"
                for r in rows:
                    last = r["seq"]
                    yield f"event: change\nid: {r['seq']}\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"
                if time.monotonic() - ping > 15:
                    ping = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(st.stream_poll)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ proxy to the sandbox
    @app.api_route("/u/{user}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(user: str, path: str, request: Request) -> Response:
        try:
            check_user(user)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        kind = path.split("/", 1)[0]
        if kind not in ("owner", "a2a", "ingest"):
            raise HTTPException(404, "not found")
        ident: Identity | None = None
        if kind == "owner":
            try:
                ident = owner_identity(request, user)
            except HTTPException as e:
                return unauthorized(e)
        try:
            url = await manager.ensure(user)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        except (RuntimeError, TimeoutError) as e:
            raise HTTPException(503, f"sandbox start failed: {e}") from e
        manager.begin(user)
        try:
            headers = {k: v for k, v in request.headers.items()
                       if k.lower() not in HOP_HEADERS and k.lower() not in CREDENTIAL_HEADERS}
            if ident:
                state = manager.running.get(user)
                headers["X-Nixie-Gateway"] = state.secret if state else ""
                headers["X-Auth-Request-User"] = ident.sso_user
            r = await request.app.state.http.request(
                request.method, f"{url}/{path}", params=request.query_params,
                content=await request.body(), headers=headers,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"sandbox error: {e}") from e
        finally:
            manager.end(user)
        out_headers = {k: v for k, v in r.headers.items()
                       if k.lower() not in HOP_HEADERS | {"content-encoding", "set-cookie"}}
        return Response(content=r.content, status_code=r.status_code, headers=out_headers)

    # ------------------------------------------------------------------ static web UI (optional)
    if st.web_root:
        root = Path(st.web_root).resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def web(path: str) -> Response:
            if path.startswith(("u/", "auth/", "admin/", "oauth2/")):
                raise HTTPException(404, "not found")
            f = (root / path).resolve()
            if not path or not f.is_file() or root not in f.parents:
                f = root / "index.html"  # client-side routes
            headers = dict(SECURITY_HEADERS)
            headers["Cache-Control"] = ("public, max-age=31536000, immutable" if "/assets/" in f"/{path}"
                                        else "no-cache")
            media = "application/manifest+json" if f.suffix == ".webmanifest" else mimetypes.guess_type(f.name)[0]
            return FileResponse(f, headers=headers, media_type=media)

    return app


def make_login_link(users_root: Path, user: str, secret: bytes, base: str = "", ttl: int = 600) -> str:
    """One-time sign-in URL for the pilot before SSO (token/both modes)."""
    Directory(users_root).config(user)  # must exist
    token = Signer(secret).sign("link", {"u": user, "n": secrets.token_urlsafe(9)}, ttl)
    return f"{base.rstrip('/')}/auth/link?t={token}"


def provision_user(users_root: Path, user: str, config_yaml: str, secrets_env: str | None = None) -> Path:
    """Create the user's disk: agent.yaml (+ secrets.env) + data/ + workspace/ + browser/."""
    home = Path(users_root) / check_user(user)
    for d in ("", "data", "workspace", "browser"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / "agent.yaml").write_text(config_yaml, encoding="utf-8")
    if secrets_env is not None:
        p = home / "secrets.env"
        p.write_text(secrets_env, encoding="utf-8")
        p.chmod(0o600)
    return home
