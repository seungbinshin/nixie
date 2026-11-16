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
        uvicorn.run(create_app(NixieAgent(cfg)), host=cfg.host, port=cfg.port)
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
