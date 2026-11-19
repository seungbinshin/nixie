"""SQLite persistence on the user's disk.

Tables: owner memory, booked events, approvals, agent-to-agent transcript (append-only, hash chain), audit log,
owner<->agent chat, room read markers, notification inbox, and a change feed.

All timestamps are UTC ISO 8601 with "Z" (see timeutil.py).
The change feed (`changes`) is read by the gateway to push real-time updates without waking the agent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .timeutil import now_iso, utc_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, text TEXT, visibility TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, title TEXT, start TEXT, end TEXT, with_peer TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, kind TEXT, peer TEXT, payload TEXT, status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS transcript (id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, ts TEXT, speaker TEXT,
  peer TEXT, intent TEXT, text TEXT, data TEXT, prev_hash TEXT, hash TEXT);
CREATE TRIGGER IF NOT EXISTS transcript_no_update BEFORE UPDATE ON transcript BEGIN SELECT RAISE(ABORT, 'transcript is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transcript_no_delete BEFORE DELETE ON transcript BEGIN SELECT RAISE(ABORT, 'transcript is append-only'); END;
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, direction TEXT, peer TEXT, intent TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, role TEXT, text TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS room_reads (peer TEXT PRIMARY KEY, last_line_id INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, ts TEXT, kind TEXT, title TEXT, body TEXT,
  source TEXT, source_url TEXT, ref_type TEXT, ref_id TEXT, read_at TEXT, dedupe_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, topic TEXT, ref TEXT);
CREATE TABLE IF NOT EXISTS archive (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, ts TEXT, kind TEXT,
  title TEXT, topic TEXT, tags TEXT, body TEXT, refs TEXT, follows TEXT, path TEXT, sha256 TEXT, prev_hash TEXT, hash TEXT);
CREATE TRIGGER IF NOT EXISTS archive_no_update BEFORE UPDATE ON archive BEGIN SELECT RAISE(ABORT, 'archive is read-only'); END;
CREATE TRIGGER IF NOT EXISTS archive_no_delete BEFORE DELETE ON archive BEGIN SELECT RAISE(ABORT, 'archive is read-only'); END;
CREATE INDEX IF NOT EXISTS transcript_peer ON transcript (peer, id);
"""

# Columns added after the first release (ALTER TABLE on old disks).
MIGRATIONS = {"approvals": {"decision": "TEXT", "decided_at": "TEXT", "result": "TEXT"}}

# Topics of the change feed. The web UI reloads the matching panel.
TOPIC_ROOM, TOPIC_CHAT, TOPIC_APPROVAL, TOPIC_NOTIFICATION, TOPIC_ARCHIVE = "room", "chat", "approval", "notification", "archive"


def _now() -> str:
    return now_iso()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            for table, cols in MIGRATIONS.items():
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self._db.commit()

    def _exec(self, sql: str, args: tuple = (), bump: tuple[str, str] | None = None) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            if bump and cur.rowcount != 0:
                self._bump(*bump)
            self._db.commit()
            return rows

    def _write(self, sql: str, args: tuple = (), bump: tuple[str, str] | None = None) -> int:
        """Run one write. Return the number of changed rows."""
        with self._lock:
            n = self._db.execute(sql, args).rowcount
            if bump and n:
                self._bump(*bump)
            self._db.commit()
            return n

    def _bump(self, topic: str, ref: str) -> None:
        """Add one entry to the change feed. Call with the lock held, in the same transaction as the write."""
        self._db.execute("INSERT INTO changes (ts, topic, ref) VALUES (?,?,?)", (_now(), topic, ref))

    # ---------------------------------------------------------------- memories
    def add_memory(self, text: str, visibility: str = "private") -> str:
        mid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO memories VALUES (?,?,?,?)", (mid, text, visibility, _now()))
        return mid

    def memories(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM memories ORDER BY created")]

    # ---------------------------------------------------------------- events (start/end in UTC)
    def add_event(self, title: str, start: datetime, end: datetime, with_peer: str | None) -> str:
        eid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO events VALUES (?,?,?,?,?,?)",
                   (eid, title, utc_iso(start), utc_iso(end), with_peer, _now()))
        return eid

    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM events ORDER BY start")]

    # ---------------------------------------------------------------- approvals
    @staticmethod
    def _approval(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d

    def add_approval(self, kind: str, peer: str, payload: dict[str, Any]) -> str:
        aid = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO approvals (id, kind, peer, payload, status, created) VALUES (?,?,?,?,?,?)",
                   (aid, kind, peer, json.dumps(payload, ensure_ascii=False), "pending", _now()),
                   bump=(TOPIC_APPROVAL, aid))
        return aid

    def get_approval(self, aid: str) -> dict[str, Any] | None:
        rows = self._exec("SELECT * FROM approvals WHERE id=?", (aid,))
        return self._approval(rows[0]) if rows else None

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._exec("SELECT * FROM approvals WHERE status=? ORDER BY created", (status,))
        else:
            rows = self._exec("SELECT * FROM approvals ORDER BY created")
        return [self._approval(r) for r in rows]

    def set_approval_status(self, aid: str, status: str) -> None:
        self._write("UPDATE approvals SET status=? WHERE id=?", (status, aid), bump=(TOPIC_APPROVAL, aid))

    def claim_approval(self, aid: str, status: str, decision: str | None = None) -> bool:
        """Atomic pending -> status. Only one caller wins, so one decision runs once."""
        return self._write("UPDATE approvals SET status=?, decision=?, decided_at=? WHERE id=? AND status='pending'",
                           (status, decision, _now(), aid), bump=(TOPIC_APPROVAL, aid)) == 1

    def finish_approval(self, aid: str, status: str, result: dict[str, Any]) -> None:
        self._write("UPDATE approvals SET status=?, result=? WHERE id=?",
                    (status, json.dumps(result, ensure_ascii=False, default=str), aid), bump=(TOPIC_APPROVAL, aid))

    # ---------------------------------------------------------------- agent-to-agent transcript
    def add_line(self, conv_id: str, speaker: str, peer: str, intent: str, text: str, data: Any) -> int:
        """Append one line (owners can read, not write). Return the line id."""
        with self._lock:
            row = self._db.execute("SELECT hash FROM transcript ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            ts = _now()
            body = json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
            h = hashlib.sha256("|".join([prev, conv_id, ts, speaker, peer, intent, text, body]).encode()).hexdigest()
            cur = self._db.execute(
                "INSERT INTO transcript (conv_id, ts, speaker, peer, intent, text, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (conv_id, ts, speaker, peer, intent, text, body, prev, h),
            )
            self._bump(TOPIC_ROOM, peer)
            self._db.commit()
            return int(cur.lastrowid or 0)

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

    # ---------------------------------------------------------------- rooms (one room = one peer pair)
    def room_summaries(self) -> list[dict[str, Any]]:
        """One row per peer: last line, line count, unread count. Newest activity first."""
        rows = self._exec(
            "SELECT t.peer, t.id AS last_id, t.ts AS updated, t.speaker AS last_speaker, t.text AS last_text, "
            "  (SELECT COUNT(*) FROM transcript x WHERE x.peer=t.peer) AS lines, "
            "  (SELECT COUNT(*) FROM transcript x WHERE x.peer=t.peer AND x.id > COALESCE(r.last_line_id, 0)) AS unread "
            "FROM transcript t LEFT JOIN room_reads r ON r.peer=t.peer "
            "WHERE t.id = (SELECT MAX(id) FROM transcript y WHERE y.peer=t.peer) ORDER BY t.id DESC"
        )
        return [dict(r) for r in rows]

    def room_lines(self, peer: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM (SELECT * FROM transcript WHERE peer=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                          (peer, limit))
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def last_read(self, peer: str) -> int:
        rows = self._exec("SELECT last_line_id FROM room_reads WHERE peer=?", (peer,))
        return int(rows[0]["last_line_id"]) if rows else 0

    def mark_room_read(self, peer: str, upto: int | None = None) -> int:
        """Move the read marker forward (never back). Return the new marker."""
        with self._lock:
            top = self._db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM transcript WHERE peer=?", (peer,)).fetchone()["m"]
            want = top if upto is None else min(int(upto), top)
            cur = self._db.execute(
                "INSERT INTO room_reads (peer, last_line_id, ts) VALUES (?,?,?) "
                "ON CONFLICT(peer) DO UPDATE SET last_line_id=excluded.last_line_id, ts=excluded.ts "
                "WHERE excluded.last_line_id > room_reads.last_line_id", (peer, want, _now()))
            if cur.rowcount:
                self._bump(TOPIC_ROOM, peer)
            self._db.commit()
            row = self._db.execute("SELECT last_line_id FROM room_reads WHERE peer=?", (peer,)).fetchone()
            return int(row["last_line_id"]) if row else 0

    # ---------------------------------------------------------------- owner <-> own agent chat
    def add_chat(self, role: str, text: str, data: Any = None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO chat (ts, role, text, data) VALUES (?,?,?,?)",
                                   (_now(), role, text, json.dumps(data, ensure_ascii=False, default=str)))
            self._bump(TOPIC_CHAT, str(cur.lastrowid))
            self._db.commit()
            return int(cur.lastrowid or 0)

    def chat_history(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM (SELECT * FROM chat ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
        return [dict(r) | {"data": json.loads(r["data"]) if r["data"] else None} for r in rows]

    # ---------------------------------------------------------------- notifications
    def add_notification(self, kind: str, title: str, body: str = "", *, source: str = "nixie",
                         source_url: str | None = None, ref_type: str | None = None, ref_id: str | None = None,
                         dedupe_key: str | None = None) -> tuple[str, bool]:
        """Add one notification. Return (id, created). The same dedupe_key is stored once."""
        nid = uuid.uuid4().hex[:12]
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO notifications (id, ts, kind, title, body, source, source_url, ref_type, ref_id, "
                "read_at, dedupe_key) VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                (nid, _now(), kind, title, body, source, source_url, ref_type, ref_id, dedupe_key))
            if cur.rowcount == 0:
                row = self._db.execute("SELECT id FROM notifications WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                self._db.commit()
                return row["id"], False
            self._bump(TOPIC_NOTIFICATION, nid)
            self._db.commit()
            return nid, True

    def notifications(self, unread_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        where = "WHERE read_at IS NULL" if unread_only else ""
        rows = self._exec(f"SELECT * FROM notifications {where} ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def unread_notifications(self) -> int:
        return int(self._exec("SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL")[0]["n"])

    def mark_notification_read(self, nid: str) -> bool:
        return self._write("UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL", (_now(), nid),
                           bump=(TOPIC_NOTIFICATION, nid)) == 1

    def mark_all_notifications_read(self) -> int:
        return self._write("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (_now(),),
                           bump=(TOPIC_NOTIFICATION, "*"))

    # ---------------------------------------------------------------- work archive (append-only, hash chain)
    def add_archive(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Insert one archived document. Fields: id, ts, kind, title, topic, tags, body, refs, follows, path, sha256."""
        with self._lock:
            row = self._db.execute("SELECT hash FROM archive ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            tags, refs = json.dumps(doc["tags"], ensure_ascii=False), json.dumps(doc["refs"])
            h = hashlib.sha256("|".join([prev, doc["id"], doc["ts"], doc["kind"], doc["title"], doc["topic"], tags,
                                         doc["body"], refs, doc.get("follows") or "", doc["sha256"]]).encode()).hexdigest()
            cur = self._db.execute(
                "INSERT INTO archive (id, ts, kind, title, topic, tags, body, refs, follows, path, sha256, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc["id"], doc["ts"], doc["kind"], doc["title"], doc["topic"], tags, doc["body"], refs,
                 doc.get("follows"), doc["path"], doc["sha256"], prev, h))
            self._bump(TOPIC_ARCHIVE, doc["id"])
            self._db.commit()
            return {**doc, "seq": cur.lastrowid, "hash": h}

    @staticmethod
    def _doc(r: sqlite3.Row) -> dict[str, Any]:
        return dict(r) | {"tags": json.loads(r["tags"] or "[]"), "refs": json.loads(r["refs"] or "[]")}

    def archive_docs(self, limit: int = 2000) -> list[dict[str, Any]]:
        """Newest first."""
        return [self._doc(r) for r in self._exec("SELECT * FROM archive ORDER BY seq DESC LIMIT ?", (limit,))]

    def archive_doc(self, doc_id: str) -> dict[str, Any] | None:
        rows = self._exec("SELECT * FROM archive WHERE id=?", (doc_id,))
        return self._doc(rows[0]) if rows else None

    def archive_by_topic(self, topic: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM archive WHERE topic=? ORDER BY seq DESC LIMIT ?", (topic, limit))
        return [self._doc(r) for r in rows]

    def verify_archive(self) -> bool:
        prev = "0" * 64
        for r in self._exec("SELECT * FROM archive ORDER BY seq"):
            h = hashlib.sha256("|".join([prev, r["id"], r["ts"], r["kind"], r["title"], r["topic"], r["tags"], r["body"],
                                         r["refs"], r["follows"] or "", r["sha256"]]).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False
            prev = h
        return True

    # ---------------------------------------------------------------- change feed
    def changes_since(self, seq: int, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM changes WHERE seq > ? ORDER BY seq LIMIT ?", (seq, limit))]

    # ---------------------------------------------------------------- audit
    def audit(self, direction: str, peer: str, intent: str, payload: Any) -> None:
        self._exec(
            "INSERT INTO audit (ts, direction, peer, intent, payload) VALUES (?,?,?,?,?)",
            (_now(), direction, peer, intent, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def read_changes(db_path: Path, since: int, limit: int = 100) -> tuple[int, list[dict[str, Any]]]:
    """Read the change feed from another process (the gateway), read-only. Return (max_seq, new rows).

    The file may not exist yet (user never started); then (0, []).
    """
    if not Path(db_path).is_file():
        return 0, []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        try:
            top = db.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM changes").fetchone()["m"]
            rows = db.execute("SELECT seq, ts, topic, ref FROM changes WHERE seq > ? ORDER BY seq LIMIT ?",
                              (since, limit)).fetchall()
        except sqlite3.OperationalError:  # old disk without the table
            return 0, []
        return int(top), [dict(r) for r in rows]
    finally:
        db.close()
