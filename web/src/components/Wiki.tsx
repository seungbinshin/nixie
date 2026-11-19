import { useEffect, useRef, useState } from "react";
import { ApiError, request } from "../api";
import { hrefFor } from "../route";
import { kstFull, kstShort } from "../time";
import { IconBack, IconFollowUp } from "../icons";
import { Markdown } from "./Markdown";
import type { WikiDoc, WikiItem, WikiList } from "../types";
import { canWrite, chooseFolder, folderSupported, savedFolder, syncBundle, type Bundle, type SyncResult } from "../wikiSync";

const KIND: Record<string, string> = { task: "작업", approval: "승인", meeting: "일정", note: "후속 메모" };

interface Props {
  api: string;
  docId: string | null;
  version: number; // changes when the archive changes (real-time)
  onNavigate(href: string): void;
  say(text: string): void;
}

/** Work archive as a wiki. Documents are read-only; a follow-up note becomes a new document. */
export function Wiki({ api, docId, version, onNavigate, say }: Props) {
  const [q, setQ] = useState("");
  const [topic, setTopic] = useState<string>("");
  const [list, setList] = useState<WikiList | null>(null);
  const [doc, setDoc] = useState<WikiDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [sync, setSync] = useState<SyncResult | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [folder, setFolder] = useState<string | null>(null);
  const timer = useRef<number | undefined>();

  // list (debounced search)
  useEffect(() => {
    if (docId) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const qs = new URLSearchParams({ q, ...(topic ? { topic } : {}) });
      request<WikiList>(`${api}/archive?${qs}`).then(setList).catch(() => setError("목록을 불러오지 못했습니다."));
    }, 250);
    return () => window.clearTimeout(timer.current);
  }, [api, docId, q, topic, version]);

  useEffect(() => {
    if (!docId) {
      setDoc(null);
      return;
    }
    setError(null);
    request<WikiDoc>(`${api}/archive/${encodeURIComponent(docId)}`).then(setDoc).catch((e) =>
      setError(e instanceof ApiError && e.status === 404 ? "이 문서를 찾을 수 없습니다." : "문서를 불러오지 못했습니다."));
  }, [api, docId, version]);

  // local folder: show the saved folder; sync by itself when the archive changes and permission is still granted
  useEffect(() => {
    if (!folderSupported()) return;
    void savedFolder().then(async (h) => {
      if (!h) return;
      setFolder(h.name);
      if (version > 0 && (await canWrite(h, false))) {
        try {
          setSync(await syncBundle(h, await request<Bundle>(`${api}/archive/bundle`)));
        } catch {
          /* the next manual sync reports the error */
        }
      }
    });
  }, [api, version]);

  const runSync = async (pick: boolean) => {
    setSyncing(true);
    try {
      let h = pick ? null : await savedFolder();
      if (!h) h = await chooseFolder();
      if (!(await canWrite(h, true))) {
        say("폴더 쓰기 권한이 없습니다.");
        return;
      }
      setFolder(h.name);
      const r = await syncBundle(h, await request<Bundle>(`${api}/archive/bundle`));
      setSync(r);
      say(`로컬 폴더 '${r.folder}'에 동기화했습니다. 새 파일 ${r.written}개, 원본으로 되돌린 파일 ${r.restored}개.`);
    } catch (e) {
      if ((e as DOMException)?.name !== "AbortError") say("로컬 폴더에 저장하지 못했습니다.");
    } finally {
      setSyncing(false);
    }
  };

  const addNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!doc || !note.trim()) return;
    setSaving(true);
    try {
      const n = await request<{ id: string }>(`${api}/archive/${encodeURIComponent(doc.id)}/notes`, { method: "POST", body: { text: note } });
      setNote("");
      say("후속 메모를 새 문서로 보관했습니다.");
      onNavigate(hrefFor({ wiki: n.id }));
    } catch {
      say("메모를 보관하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const go = (id: string) => (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    onNavigate(hrefFor({ wiki: id }));
  };

  const tools = (
    <div className="wiki-tools">
      {folderSupported() ? (
        <>
          <button type="button" className="btn btn--small" disabled={syncing} aria-busy={syncing} onClick={() => void runSync(false)}>
            {folder ? "로컬 폴더 동기화" : "로컬 폴더에 저장"}
          </button>
          {folder && <button type="button" className="btn btn--small btn--ghost" onClick={() => void runSync(true)}>폴더 바꾸기</button>}
        </>
      ) : null}
      <a className="btn btn--small btn--ghost" href={`${api}/archive/export.zip`} download="nixie-wiki.zip">ZIP 내려받기</a>
      {sync && (
        <p className="muted small" role="status">
          '{sync.folder}' 동기화 {kstShort(sync.at)} · 새 파일 {sync.written} · 되돌림 {sync.restored} · 변경 없음 {sync.unchanged}
        </p>
      )}
    </div>
  );

  if (docId) {
    return (
      <section className="conversation wiki" aria-labelledby="main-h">
        <header className="conv-head">
          <p><a href={hrefFor({ wiki: "" })} onClick={(e) => { e.preventDefault(); onNavigate(hrefFor({ wiki: "" })); }}><IconBack /> 업무 위키</a></p>
          <h2 id="main-h" tabIndex={-1}>{doc ? doc.title : "업무 위키 문서"}</h2>
          {doc && (
            <p className="conv-meta">
              <span className="tag">{KIND[doc.kind] ?? doc.kind}</span>
              <span>{doc.topic}</span>
              <time dateTime={doc.ts}>{kstFull(doc.ts)}</time>
              <span className="tag tag--lock">보관됨 · 수정 불가</span>
            </p>
          )}
        </header>
        <div className="messages wiki-doc" tabIndex={0} aria-label="문서 본문">
          {error ? <p className="error" role="alert">{error}</p> : !doc ? <p className="muted" role="status">불러오는 중…</p> : (
            <>
              <Markdown text={doc.body} headingShift={1} onNavigate={onNavigate} />
              {(doc.follows || doc.refs.length > 0) && (
                <section aria-labelledby="refs-h" className="wiki-links">
                  <h3 id="refs-h">관련 문서</h3>
                  <ul>
                    {doc.follows && <li>이어지는 원문: <a href={hrefFor({ wiki: doc.follows.id })} onClick={go(doc.follows.id)}>{doc.follows.title}</a></li>}
                    {doc.refs.map((r) => <li key={r.id}><a href={hrefFor({ wiki: r.id })} onClick={go(r.id)}>{r.title}</a></li>)}
                  </ul>
                </section>
              )}
              {doc.backlinks.length > 0 && (
                <section aria-labelledby="back-h" className="wiki-links">
                  <h3 id="back-h">이 문서를 이어 가는 문서</h3>
                  <ul>{doc.backlinks.map((b) => <li key={b.id}><a href={hrefFor({ wiki: b.id })} onClick={go(b.id)}>{b.title}</a> <span className="muted">({KIND[b.kind] ?? b.kind})</span></li>)}</ul>
                </section>
              )}
            </>
          )}
        </div>
        {doc && (
          <form className="composer" onSubmit={addNote}>
            <label htmlFor="note-input">후속 메모 (원문은 바뀌지 않고, 메모가 새 문서로 보관됩니다)</label>
            <div className="composer-row">
              <textarea id="note-input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
              <button type="submit" className="btn btn--primary" disabled={saving || !note.trim()} aria-busy={saving}>보관</button>
            </div>
          </form>
        )}
      </section>
    );
  }

  return (
    <section className="conversation wiki" aria-labelledby="main-h">
      <header className="conv-head">
        <h2 id="main-h" tabIndex={-1}>업무 위키</h2>
        <p className="conv-meta">에이전트가 수행한 업무를 자동으로 보관합니다 · 보관 문서는 수정할 수 없습니다
          {list && !list.integrity_ok && <span className="error"> · 무결성 오류</span>}</p>
        {tools}
      </header>
      <div className="messages">
        <div className="wiki-search">
          <label htmlFor="wiki-q" className="sr-only">업무 위키 검색</label>
          <input id="wiki-q" type="search" placeholder="검색 (예: 회의실, 휴가, 사용자1)" value={q} onChange={(e) => setQ(e.target.value)} />
          {list && list.topics.length > 0 && (
            <div className="quick" role="group" aria-label="주제">
              <button type="button" className="chip" aria-pressed={!topic} onClick={() => setTopic("")}>전체</button>
              {list.topics.map((t) => (
                <button key={t.topic} type="button" className="chip" aria-pressed={topic === t.topic} onClick={() => setTopic(topic === t.topic ? "" : t.topic)}>
                  {t.topic} <span className="muted">{t.count}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {error ? <p className="error" role="alert">{error}</p> : list === null ? <p className="muted" role="status">불러오는 중…</p> :
          list.items.length === 0 ? <p className="muted">{q || topic ? "맞는 문서가 없습니다." : "아직 보관된 업무가 없습니다. 에이전트에게 업무를 맡기면 자동으로 보관됩니다."}</p> : (
            <ul className="wiki-list">
              {list.items.map((d: WikiItem) => (
                <li key={d.id}>
                  <a className="wiki-item" href={hrefFor({ wiki: d.id })} onClick={go(d.id)}>
                    <span className="room-row">
                      <span className="room-name">{d.follows && <><IconFollowUp /><span className="sr-only">후속 메모: </span></>}{d.title}</span>
                      <time className="room-time" dateTime={d.ts} title={kstFull(d.ts)}>{kstShort(d.ts)}</time>
                    </span>
                    <span className="wiki-meta"><span className="tag">{KIND[d.kind] ?? d.kind}</span> {d.topic}</span>
                    <span className="room-last">{d.snippet}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
      </div>
    </section>
  );
}
