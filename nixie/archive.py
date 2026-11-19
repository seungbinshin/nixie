"""Work archive: a wiki of what the agent did for its owner.

- The agent writes a document after work: a request with actions, an approval decision, a meeting outcome.
- Documents are read-only: no update or delete (SQLite triggers), a hash chain, files with mode 0444.
- The archive grows without edits: a document links to earlier documents of the same topic, and the owner adds
  a follow-up note as a NEW document that points to the old one.
- Files: <home>/archive/docs/<id>.md (Markdown with front matter). A bundle adds index.md and topics/*.md (hubs)
  for a local wiki folder (any Markdown viewer, for example Obsidian).
- The agent searches the archive (tool `search_archive`, command `/wiki`) and gets related documents automatically.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import Store
from .timeutil import KST, kst, now_iso

PARTICLES = ("에서는", "으로는", "에게서", "에서", "으로", "에게", "께서", "까지", "부터", "보다", "처럼", "하고",
             "이랑", "랑", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "의", "도", "만", "께", "요")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TOKEN = re.compile(r"[\w.\-]+", re.UNICODE)


def _tokens(q: str) -> list[str]:
    out: list[str] = []
    for t in _TOKEN.findall(q.lower()):
        for p in PARTICLES:
            if t.endswith(p) and len(t) - len(p) >= 2:
                t = t[: -len(p)]
                break
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


def topic_file(topic: str) -> str:
    return (_UNSAFE.sub("_", topic).strip(" .") or "기타")[:60] + ".md"


class WorkArchive:
    def __init__(self, store: Store, home: Path, owner: str):
        self.store = store
        self.dir = Path(home) / "archive" / "docs"
        self.owner = owner

    # ------------------------------------------------------------------ write
    def add(self, kind: str, title: str, topic: str, body: str, tags: list[str] | None = None,
            follows: str | None = None) -> dict[str, Any]:
        """Archive one document. It cannot be changed later."""
        ts = now_iso()
        doc_id = f"{kst(ts, '%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        earlier = [d for d in self.store.archive_by_topic(topic, 4) if d["id"] != follows][:3]
        parent = self.store.archive_doc(follows) if follows else None
        refs = [d["id"] for d in earlier]
        titles = {d["id"]: d["title"] for d in earlier}
        if parent:
            titles[parent["id"]] = parent["title"]
        tags = sorted({kind, *(tags or [])})
        doc = {"id": doc_id, "ts": ts, "kind": kind, "title": title.strip()[:120] or kind, "topic": topic,
               "tags": tags, "body": body.strip(), "refs": refs, "follows": follows, "path": f"docs/{doc_id}.md"}
        text = self.render(doc, titles)
        doc["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        saved = self.store.add_archive(doc)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = self.dir / f"{doc_id}.md"
            f.write_text(text, encoding="utf-8")
            f.chmod(0o444)  # read-only copy on the user's disk
        except OSError:
            pass  # the database row is the record; the file is a copy
        return saved

    def note(self, parent_id: str, text: str) -> dict[str, Any]:
        """A follow-up note is a new document that points to the old one (the old one does not change)."""
        parent = self.store.archive_doc(parent_id)
        if not parent:
            raise KeyError(parent_id)
        text = text.strip()
        if not text:
            raise ValueError("empty note")
        return self.add("note", f"후속 메모: {parent['title']}"[:120], parent["topic"],
                        f"{text[:5000]}\n\n원래 문서: [{parent['title']}]({parent['id']}.md)",
                        [t for t in parent["tags"] if t != parent["kind"]], follows=parent_id)

    # ------------------------------------------------------------------ render
    def render(self, doc: dict[str, Any], titles: dict[str, str]) -> str:
        """The Markdown file. Same input -> same bytes (the sha256 is stored)."""
        fm = {"id": doc["id"], "title": doc["title"], "kind": doc["kind"], "topic": doc["topic"],
              "date": kst(doc["ts"], "%Y-%m-%d %H:%M KST"), "created_utc": doc["ts"], "tags": doc["tags"],
              "follows": doc.get("follows"), "related": doc["refs"], "owner": self.owner, "read_only": True}
        lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items()] + ["---", "",
                                                                                                   f"# {doc['title']}", ""]
        lines.append(f"> {fm['date']} · {doc['topic']} · 보관 문서(수정 불가)")
        lines += ["", doc["body"], ""]
        if doc.get("follows") or doc["refs"]:
            lines.append("## 관련 문서")
            if doc.get("follows"):
                lines.append(f"- 이어지는 원문: [{titles.get(doc['follows'], doc['follows'])}]({doc['follows']}.md)")
            for r in doc["refs"]:
                lines.append(f"- [{titles.get(r, r)}]({r}.md)")
            lines.append("")
        return "\n".join(lines)

    def file_text(self, doc: dict[str, Any]) -> str:
        titles = {}
        for r in [*doc["refs"], *([doc["follows"]] if doc.get("follows") else [])]:
            d = self.store.archive_doc(r)
            if d:
                titles[r] = d["title"]
        return self.render(doc, titles)

    # ------------------------------------------------------------------ read
    def search(self, query: str, limit: int = 10, topic: str | None = None) -> list[dict[str, Any]]:
        """Token match with weights (title 3, topic/tags 2, body 1); newest first on ties.

        Korean particles at the end of a word are removed ("회의실을" -> "회의실"), so short words match.
        """
        toks = _tokens(query or "")
        out = []
        for i, d in enumerate(self.store.archive_docs()):
            if topic and d["topic"] != topic:
                continue
            title, meta, body = d["title"].lower(), (d["topic"] + " " + " ".join(d["tags"])).lower(), d["body"].lower()
            score = sum(3 * (t in title) + 2 * (t in meta) + (t in body) for t in toks) if toks else 1
            if score:
                out.append((score, -i, d))
        out.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [self.summary(d, toks) for _, _, d in out[:limit]]

    @staticmethod
    def summary(d: dict[str, Any], toks: list[str] | None = None) -> dict[str, Any]:
        body = d["body"]
        pos = min([body.lower().find(t) for t in (toks or []) if t in body.lower()] or [0])
        snippet = re.sub(r"\s+", " ", body[max(0, pos - 40): pos + 160]).strip()
        return {"id": d["id"], "ts": d["ts"], "kind": d["kind"], "title": d["title"], "topic": d["topic"],
                "tags": d["tags"], "follows": d.get("follows"), "snippet": snippet}

    def get(self, doc_id: str) -> dict[str, Any] | None:
        d = self.store.archive_doc(doc_id)
        if not d:
            return None
        docs = self.store.archive_docs()
        backlinks = [{"id": x["id"], "title": x["title"], "kind": x["kind"]} for x in docs
                     if doc_id in x["refs"] or x.get("follows") == doc_id]
        refs = [{"id": r, "title": (self.store.archive_doc(r) or {}).get("title", r)} for r in d["refs"]]
        follows = None
        if d.get("follows"):
            p = self.store.archive_doc(d["follows"])
            follows = {"id": d["follows"], "title": p["title"] if p else d["follows"]}
        return {**d, "refs": refs, "follows": follows, "backlinks": backlinks, "read_only": True}

    def topics(self) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for d in self.store.archive_docs():
            t = out.setdefault(d["topic"], {"topic": d["topic"], "count": 0, "last": d["ts"]})
            t["count"] += 1
        return sorted(out.values(), key=lambda t: t["last"], reverse=True)

    # ------------------------------------------------------------------ local wiki folder
    def bundle(self) -> dict[str, Any]:
        """All files of the local wiki: docs (read-only) + hubs (index.md, topics/*.md; rebuilt each time)."""
        docs = list(reversed(self.store.archive_docs()))  # oldest first
        files = []
        for d in docs:
            text = self.file_text(d)
            files.append({"path": d["path"], "content": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "read_only": True})
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for d in docs:
            by_topic.setdefault(d["topic"], []).append(d)
        for topic, items in by_topic.items():
            lines = [f"# {topic}", "", f"[← 업무 위키](../index.md)", ""]
            for d in items:
                pre = "  - ↳ " if d.get("follows") else "- "
                lines.append(f"{pre}{kst(d['ts'], '%Y-%m-%d %H:%M')} [{d['title']}](../{d['path']})")
            files.append(self._hub(f"topics/{topic_file(topic)}", "\n".join(lines) + "\n"))
        now = datetime.now(timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        idx = [f"# {self.owner}님의 업무 위키", "",
               "에이전트가 수행한 업무를 자동으로 보관한 문서입니다. 보관 문서는 수정할 수 없습니다.",
               "내용을 보충하려면 웹 화면에서 '후속 메모'를 추가하십시오(새 문서로 보관됩니다).", "",
               f"마지막 동기화: {now} · 문서 {len(docs)}개", "", "## 주제", ""]
        for t in sorted(by_topic, key=lambda k: by_topic[k][-1]["ts"], reverse=True):
            idx.append(f"- [{t}](topics/{topic_file(t).replace(' ', '%20')}) ({len(by_topic[t])})")
        idx += ["", "## 최근 문서", ""]
        for d in list(reversed(docs))[:30]:
            idx.append(f"- {kst(d['ts'], '%Y-%m-%d %H:%M')} [{d['title']}]({d['path']}) · {d['topic']}")
        files.append(self._hub("index.md", "\n".join(idx) + "\n"))
        seq = max([0, *[int(d["seq"]) for d in docs]])
        return {"seq": seq, "count": len(docs), "files": files, "integrity_ok": self.store.verify_archive()}

    @staticmethod
    def _hub(path: str, text: str) -> dict[str, Any]:
        return {"path": path, "content": text, "sha256": hashlib.sha256(text.encode()).hexdigest(), "read_only": False}

    def zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in self.bundle()["files"]:
                info = zipfile.ZipInfo(f"nixie-wiki/{f['path']}", date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = (0o444 if f["read_only"] else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f["content"])
        return buf.getvalue()
