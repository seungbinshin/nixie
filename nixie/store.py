"""SQLite persistence: owner memory, booked events, approvals, A2A audit log."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, text TEXT, visibility TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, title TEXT, start TEXT, end TEXT, with_peer TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, kind TEXT, peer TEXT, payload TEXT, status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS transcript (id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, ts TEXT, speaker TEXT,
  peer TEXT, intent TEXT, text TEXT, data TEXT, prev_hash TEXT, hash TEXT);
CREATE TRIGGER IF NOT EXISTS transcript_no_update BEFORE UPDATE ON transcript BEGIN SELECT RAISE(ABORT, 'transcript is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transcript_no_delete BEFORE DELETE ON transcript BEGIN SELECT RAISE(ABORT, 'transcript is append-only'); END;
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, direction TEXT, peer TEXT, intent TEXT, payload TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.fetchall()

    # memories
    def add_memory(self, text: str, visibility: str = "private") -> str:
        mid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO memories VALUES (?,?,?,?)", (mid, text, visibility, _now()))
        return mid

    def memories(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM memories ORDER BY created")]

    # events
    def add_event(self, title: str, start: datetime, end: datetime, with_peer: str | None) -> str:
        eid = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (eid, title, start.isoformat(), end.isoformat(), with_peer, _now()),
        )
        return eid

    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM events ORDER BY start")]

    # approvals
    def add_approval(self, kind: str, peer: str, payload: dict[str, Any]) -> str:
        aid = uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?)",
            (aid, kind, peer, json.dumps(payload, ensure_ascii=False), "pending", _now()),
        )
        return aid

    def get_approval(self, aid: str) -> dict[str, Any] | None:
        rows = self._exec("SELECT * FROM approvals WHERE id=?", (aid,))
        if not rows:
            return None
        d = dict(rows[0])
        d["payload"] = json.loads(d["payload"])
        return d

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._exec("SELECT * FROM approvals WHERE status=? ORDER BY created", (status,))
        else:
            rows = self._exec("SELECT * FROM approvals ORDER BY created")
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def set_approval_status(self, aid: str, status: str) -> None:
        self._exec("UPDATE approvals SET status=? WHERE id=?", (status, aid))

    # agent-to-agent transcript (append-only, hash-chained; owners can read, not write)
    def add_line(self, conv_id: str, speaker: str, peer: str, intent: str, text: str, data: Any) -> None:
        with self._lock:
            row = self._db.execute("SELECT hash FROM transcript ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            ts = _now()
            body = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
            h = hashlib.sha256("|".join([prev, conv_id, ts, speaker, peer, intent, text, body]).encode()).hexdigest()
            self._db.execute(
                "INSERT INTO transcript (conv_id, ts, speaker, peer, intent, text, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (conv_id, ts, speaker, peer, intent, text, body, prev, h),
            )
            self._db.commit()

    def conversations(self) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT conv_id, peer, MIN(ts) AS started, MAX(ts) AS updated, COUNT(*) AS lines "
            "FROM transcript GROUP BY conv_id, peer ORDER BY updated DESC"
        )
        return [dict(r) for r in rows]

    def conversation(self, conv_id: str) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM transcript WHERE conv_id=? ORDER BY id", (conv_id,))
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def verify_transcript(self) -> bool:
        prev = "0" * 64
        for r in self._exec("SELECT * FROM transcript ORDER BY id"):
            h = hashlib.sha256("|".join([prev, r["conv_id"], r["ts"], r["speaker"], r["peer"], r["intent"],
                                         r["text"], r["data"]]).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False
            prev = h
        return True

    # audit
    def audit(self, direction: str, peer: str, intent: str, payload: Any) -> None:
        self._exec(
            "INSERT INTO audit (ts, direction, peer, intent, payload) VALUES (?,?,?,?,?)",
            (_now(), direction, peer, intent, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
