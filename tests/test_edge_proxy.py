"""The real nginx edge config (deploy/nginx/templates/nixie.conf.template) in front of the gateway.

A small stand-in replaces oauth2-proxy (same /oauth2/auth contract: 202 + X-Auth-Request-User, or 401).
Checks: the client cannot forge X-Auth-Request-User, internal paths are closed, SSE is not buffered,
logout clears both sessions. Skipped when nginx is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

from nixie.gateway import GatewaySettings, create_gateway
from nixie.pilot import provision
from nixie.sandbox import ProcessBackend, SandboxManager
from util import ThreadServer, free_port

ROOT = Path(__file__).resolve().parents[1]
NGINX = shutil.which("nginx")
pytestmark = pytest.mark.skipif(not NGINX or not shutil.which("openssl"), reason="nginx/openssl not installed")
SECRET = "edge-secret"


def fake_oauth2_proxy() -> FastAPI:
    app = FastAPI()

    @app.get("/oauth2/auth")
    async def auth(request: Request):
        user = request.cookies.get("_oauth2_proxy")
        return Response(status_code=202, headers={"X-Auth-Request-User": user}) if user else Response(status_code=401)

    @app.get("/oauth2/sign_in")
    async def sign_in(rd: str = "/"):
        return RedirectResponse(f"https://idp.example/authorize?rd={rd}", status_code=302)

    @app.get("/oauth2/sign_out")
    async def sign_out(rd: str = "/"):
        r = RedirectResponse(rd, status_code=302)
        r.delete_cookie("_oauth2_proxy")
        return r

    return app


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("edge")
    gw_port, o2_port, https_port, http_port = free_port(), free_port(), free_port(), free_port()
    plan = yaml.safe_load((ROOT / "examples" / "pilot" / "plan.yaml").read_text(encoding="utf-8"))
    plan.pop("defaults", None)
    (tmp / "plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    provision(tmp / "plan.yaml", tmp / "users", f"http://127.0.0.1:{gw_port}")
    web = tmp / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Nixie</title>", encoding="utf-8")
    (web / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (tmp / "tls").mkdir()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(tmp / "tls/tls.key"),
                    "-out", str(tmp / "tls/tls.crt"), "-days", "1", "-subj", "/CN=nixie.local"],
                   check=True, capture_output=True)
    conf = (ROOT / "deploy/nginx/templates/nixie.conf.template").read_text(encoding="utf-8")
    conf = (conf.replace("${NIXIE_SERVER_NAME}", "nixie.local").replace("${NIXIE_PROXY_SECRET}", SECRET)
            .replace("gateway:8080", f"127.0.0.1:{gw_port}").replace("oauth2-proxy:4180", f"127.0.0.1:{o2_port}")
            .replace("/etc/nginx/tls", str(tmp / "tls")).replace("/usr/share/nginx/html", str(web))
            .replace("listen 443 ssl;", f"listen 127.0.0.1:{https_port} ssl;")
            .replace("listen 80;", f"listen 127.0.0.1:{http_port};"))
    out = subprocess.run([NGINX, "-v"], capture_output=True, text=True).stderr
    ver = tuple(int(x) for x in out.split("/")[1].split()[0].split(".")[:3])
    if ver < (1, 25, 1):
        conf = conf.replace("    http2 on;\n", "")  # the deploy image (nginx 1.29) supports it
    (tmp / "conf.d").mkdir()
    (tmp / "conf.d/nixie.conf").write_text(conf, encoding="utf-8")
    mime = "/etc/nginx/mime.types" if Path("/etc/nginx/mime.types").exists() else None
    import os
    run_as = "user root;\n" if os.geteuid() == 0 else ""  # workers must read the pytest tmp dir (mode 700)
    (tmp / "nginx.conf").write_text(
        f"{run_as}daemon off; pid {tmp}/nginx.pid; error_log {tmp}/error.log;\nevents {{}}\n"
        f"http {{ access_log off; {'include ' + mime + ';' if mime else ''} include {tmp}/conf.d/*.conf; }}\n")
    mgr = SandboxManager(ProcessBackend(), tmp / "users", idle_seconds=60)
    st = GatewaySettings(auth_mode="sso", proxy_secret=SECRET, cookie_secure=True, stream_poll=0.3, stream_max_age=5)
    with ThreadServer(create_gateway(mgr, settings=st), port=gw_port), ThreadServer(fake_oauth2_proxy(), port=o2_port):
        proc = subprocess.Popen([NGINX, "-c", str(tmp / "nginx.conf"), "-p", str(tmp)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        base = f"https://127.0.0.1:{https_port}"
        for _ in range(100):
            try:
                httpx.get(base + "/manifest.webmanifest", verify=False, trust_env=False, timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        try:
            yield base
        finally:
            proc.terminate()
            proc.wait(5)


def client(user: str | None = None) -> httpx.Client:
    c = httpx.Client(verify=False, trust_env=False, timeout=60, headers={"Host": "nixie.local"})
    if user:
        c.cookies.set("_oauth2_proxy", user)
    return c


def test_identity_header_cannot_be_forged_through_the_proxy(edge):
    with client("user1") as c:
        assert c.get(f"{edge}/auth/me").json()["user"] == "user1"
        # the client sends someone else's identity: nginx replaces it with the SSO identity
        forged = {"X-Auth-Request-User": "user2", "X-Nixie-Proxy-Secret": SECRET, "X_Auth_Request_User": "user2"}
        assert c.get(f"{edge}/auth/me", headers=forged).json()["user"] == "user1"
        assert c.get(f"{edge}/u/user2/owner/me", headers=forged).status_code == 403
        assert c.get(f"{edge}/u/user1/owner/me").json()["agent_id"] == "user1"
        assert c.post(f"{edge}/u/user1/owner/chat", json={"text": "/help"}).status_code == 403  # CSRF header missing
        assert c.post(f"{edge}/u/user1/owner/chat", json={"text": "/help"},
                      headers={"X-Nixie-Request": "1"}).status_code == 200


def test_internal_paths_are_closed_at_the_edge(edge):
    with client("user1") as c:
        assert c.post(f"{edge}/u/user1/a2a/jsonrpc", json={}).status_code == 404
        for path in ("/u/user1/a2a/jsonrpc", "/u/user1/ingest/notifications", "/u/user1/.well-known/agent-card.json",
                     "/admin/sandboxes", "/auth/session?user=user1", "/auth/link?t=x", "/healthz"):
            assert c.get(edge + path).status_code == 404, path


def test_no_session_api_401_page_redirect_assets_public(edge):
    with client() as c:
        r = c.get(f"{edge}/u/user1/owner/me")
        assert r.status_code == 401 and r.json()["login_url"] == "/oauth2/start"
        r = c.get(f"{edge}/?room=user1~user2")
        assert r.status_code == 302 and r.headers["location"] == "/oauth2/sign_in?rd=https://nixie.local/?room=user1~user2"
        assert c.get(f"{edge}/manifest.webmanifest").status_code == 200  # fetched without cookies by browsers
    with client("user1") as c:
        r = c.get(f"{edge}/?room=user1~user2")
        assert r.status_code == 200 and "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_stream_passes_unbuffered(edge):
    with client("user1") as c, c.stream("GET", f"{edge}/u/user1/owner/stream") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        t0 = time.monotonic()
        first = next(r.iter_lines())
        assert first.startswith("retry:") and time.monotonic() - t0 < 3


def test_logout_clears_both_sessions(edge):
    with client("user1") as c:
        c.cookies.set("nixie_session", "old")
        r = c.get(f"{edge}/auth/logout")
        assert r.status_code == 303 and r.headers["location"] == "/oauth2/sign_out?rd=%2F"
        assert "nixie_session=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
        r = c.get(edge + r.headers["location"])
        assert r.status_code == 302 and "_oauth2_proxy=" in r.headers["set-cookie"]
