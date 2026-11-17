"""Test helpers: run an ASGI app in a background thread."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ThreadServer:
    def __init__(self, app, port: int | None = None):
        self.port = port or free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        for _ in range(300):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(30)


def wait_until(fn, timeout: float = 20, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def get(url: str, **kw) -> httpx.Response:
    return httpx.get(url, timeout=60, trust_env=False, **kw)


def post(url: str, **kw) -> httpx.Response:
    return httpx.post(url, timeout=120, trust_env=False, **kw)
