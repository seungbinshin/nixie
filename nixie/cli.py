"""Command line: run an agent server, or talk to your own agent."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import httpx


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="nixie")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run an agent")
    s.add_argument("--config", required=True)
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    g = sub.add_parser("gateway", help="always-on gateway; starts per-user sandboxes on demand")
    g.add_argument("--users-root", required=True)
    g.add_argument("--host", default="0.0.0.0")
    g.add_argument("--port", type=int, default=8080)
    g.add_argument("--backend", choices=["process", "container"], default="container")
    g.add_argument("--cli", default="docker", help="docker | nerdctl | podman")
    g.add_argument("--cli-arg", action="append", default=[], help="global CLI arg, e.g. --address=/run/...sock")
    g.add_argument("--runtime", help="runc | io.containerd.kata.v2 | runsc | aws.firecracker")
    g.add_argument("--image", default="nixie:latest")
    g.add_argument("--network")
    g.add_argument("--idle-seconds", type=float, default=300)
    g.add_argument("--max-running", type=int)
    g.add_argument("--auth", choices=["token", "sso", "both"], default=os.environ.get("NIXIE_AUTH_MODE", "token"),
                   help="token: owner token / nixie_session (before SSO); sso: SSO proxy header only; both: transition")
    g.add_argument("--web-root", help="serve the static web UI build from this directory (dev; prod: the proxy)")
    g.add_argument("--login-url", default="/oauth2/start", help="SSO sign-in start URL for the web UI")
    g.add_argument("--logout-url", default="/oauth2/sign_out?rd=%2F", help="SSO proxy sign-out URL")
    g.add_argument("--session-ttl", type=int, default=8 * 3600)
    g.add_argument("--insecure-cookie", action="store_true", help="allow the session cookie over plain HTTP (dev)")
    pl = sub.add_parser("pilot", help="pilot provisioning")
    plsub = pl.add_subparsers(dest="pilot_cmd", required=True)
    pi = plsub.add_parser("init", help="write agent.yaml + secrets.env for each user and room in the plan")
    pi.add_argument("--plan", required=True)
    pi.add_argument("--users-root", required=True)
    pi.add_argument("--env-file", help="add missing gateway/proxy secrets to this deploy env file")
    pi.add_argument("--gateway-url", help="override gateway_url of the plan (e.g. http://127.0.0.1:8080 for dev)")
    ll = sub.add_parser("login-link", help="one-time sign-in URL (token/both modes, before SSO)")
    ll.add_argument("--users-root", required=True)
    ll.add_argument("--user", required=True)
    ll.add_argument("--base", default="")
    ll.add_argument("--ttl", type=int, default=600)
    b = sub.add_parser("browser-pool", help="shared browser worker pool")
    b.add_argument("--users-root", required=True)
    b.add_argument("--host", default="0.0.0.0")
    b.add_argument("--port", type=int, default=8090)
    b.add_argument("--size", type=int, default=2)
    b.add_argument("--allow-domain", action="append", default=[])
    b.add_argument("--chrome", default=os.environ.get("NIXIE_CHROME_PATH"))
    c = sub.add_parser("chat", help="send one message to your agent")
    c.add_argument("--url", default=os.environ.get("NIXIE_URL", "http://127.0.0.1:8700"))
    c.add_argument("--token", default=os.environ.get("NIXIE_OWNER_TOKEN", ""))
    c.add_argument("text", nargs="+")
    a = p.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn

        from .agent import NixieAgent
        from .app import create_app
        from .config import load_config

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        cfg = load_config(a.config)
        uvicorn.run(create_app(NixieAgent(cfg)), host=a.host or cfg.host, port=a.port or cfg.port)
    elif a.cmd == "gateway":
        import uvicorn

        from .gateway import GatewaySettings, create_gateway
        from .sandbox import ContainerBackend, ProcessBackend, SandboxManager

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        if a.backend == "process":
            backend = ProcessBackend()
        else:
            backend = ContainerBackend(image=a.image, cli=a.cli, cli_global_args=a.cli_arg, runtime=a.runtime,
                                       network=a.network)
        mgr = SandboxManager(backend, a.users_root, idle_seconds=a.idle_seconds, max_running=a.max_running)
        settings = GatewaySettings.from_env(
            auth_mode=a.auth, web_root=a.web_root, login_url=a.login_url, logout_url=a.logout_url,
            session_ttl=a.session_ttl, cookie_secure=not a.insecure_cookie)
        uvicorn.run(create_gateway(mgr, settings=settings), host=a.host, port=a.port)
    elif a.cmd == "pilot":
        from .pilot import ensure_gateway_env, provision

        out = provision(a.plan, a.users_root, a.gateway_url)
        if a.env_file:
            out["gateway_env_added"] = ensure_gateway_env(a.env_file)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif a.cmd == "login-link":
        from .gateway import make_login_link

        secret = os.environ.get("NIXIE_SESSION_SECRET")
        if not secret:
            print("NIXIE_SESSION_SECRET must be the gateway's value", file=sys.stderr)
            sys.exit(2)
        print(make_login_link(a.users_root, a.user, secret.encode(), a.base, a.ttl))
    elif a.cmd == "browser-pool":
        import uvicorn

        from .browser_pool import BrowserPool, create_pool_app

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
        pool = BrowserPool(a.users_root, size=a.size, allow_domains=a.allow_domain or None, executable_path=a.chrome)
        uvicorn.run(create_pool_app(pool, os.environ.get("NIXIE_BROWSER_POOL_TOKEN", "")), host=a.host, port=a.port)
    elif a.cmd == "chat":
        r = httpx.post(f"{a.url.rstrip('/')}/owner/chat", json={"text": " ".join(a.text)},
                       headers={"X-Owner-Token": a.token}, timeout=180)
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text}", file=sys.stderr)
            sys.exit(1)
        reply = r.json()["reply"]
        try:
            print(json.dumps(json.loads(reply), ensure_ascii=False, indent=2))
        except ValueError:
            print(reply)


if __name__ == "__main__":
    main()
