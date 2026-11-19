import { useEffect, useMemo, useRef, useState } from "react";
import { Avatar, CHARACTERS, type CharacterId } from "../characters";
import { kstFull, kstTime } from "../time";
import type { ChatMessage, Profile } from "../types";
import { josa } from "../josa";
import { Markdown } from "./Markdown";

export interface Inflight {
  key: string;
  text: string;
  started: number;
}

interface Props {
  owner: string;
  profile: Profile;
  messages: ChatMessage[] | null;
  inflight: Inflight[];
  onSend(text: string): void;
  onCustomize(): void;
  onChoose(c: CharacterId): void;
  onNavigate(href: string): void;
}

const QUICK = [
  { label: "승인 대기 목록", text: "/approvals" },
  { label: "가능한 업무", text: "/skills" },
  { label: "업무 위키 검색", text: "/wiki" },
  { label: "도움말", text: "/help" },
];
const STALE_MS = 10 * 60 * 1000;

function Body({ text, onNavigate }: { text: string; onNavigate(href: string): void }) {
  const t = text.trim();
  if ((t.startsWith("{") || t.startsWith("[")) && t.length > 2) {
    try {
      return <pre className="msg-json">{JSON.stringify(JSON.parse(t), null, 2)}</pre>;
    } catch {
      /* not JSON: Markdown */
    }
  }
  return <Markdown text={text} onNavigate={onNavigate} />;
}

/** Chat between the owner and the owner's own agent. Requests run concurrently: the input never waits. */
export function AgentChat({ owner, profile, messages, inflight, onSend, onCustomize, onChoose, onNavigate }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const count = (messages?.length ?? 0) + inflight.length;
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" }); // block body: newer browsers return a Promise here
  }, [count]);

  // requests without a reply yet (also after a page reload)
  const { answered, byId } = useMemo(() => {
    const answered = new Set<number>();
    const byId = new Map<number, ChatMessage>();
    for (const m of messages ?? []) {
      byId.set(m.id, m);
      if (m.role === "agent" && m.data?.reply_to) answered.add(m.data.reply_to);
    }
    return { answered, byId };
  }, [messages]);

  const submit = (value: string) => {
    const v = value.trim();
    if (!v) return;
    setText("");
    onSend(v);
    inputRef.current?.focus();
  };

  // optimistic lines that the server has not stored yet
  const temps = inflight.filter((f) => !(messages ?? []).some((m) => m.role === "owner" && m.text === f.text &&
    Date.parse(m.ts) >= f.started - 5000));
  const name = profile.agent_name;
  let prevOwner = 0;
  return (
    <section className="conversation" aria-labelledby="main-h">
      <header className="conv-head conv-head--agent">
        <Avatar character={profile.character} size={52} label={`${name} (${CHARACTERS[profile.character].kind})`} />
        <div className="agent-id">
          <h2 id="main-h" tabIndex={-1}>{name}</h2>
          <p className="conv-meta">{owner}님의 업무 에이전트 · 회의실, 휴가, 결재, 문서 검색, IT 요청, 동료 일정 조율</p>
        </div>
        <button type="button" className="btn btn--small" onClick={onCustomize}>꾸미기</button>
      </header>
      <div className="messages" role="log" aria-label={`${josa(name, "와/과")}의 대화`} aria-live="polite" tabIndex={0}>
        {messages === null ? (
          <p className="muted pad" role="status">대화를 불러오는 중…</p>
        ) : messages.length === 0 && temps.length === 0 ? (
          <div className="empty pad">
            {!profile.chosen ? (
              <div className="onboard">
                <p className="onboard-title">함께 일할 에이전트를 골라 주세요</p>
                <div className="char-options">
                  {(Object.keys(CHARACTERS) as CharacterId[]).map((c) => (
                    <button key={c} type="button" className="char-option" onClick={() => onChoose(c)}>
                      <Avatar character={c} size={88} />
                      <span className="char-name">{CHARACTERS[c].name}</span>
                      <span className="char-intro">{CHARACTERS[c].intro}</span>
                    </button>
                  ))}
                </div>
                <p className="muted">이름, 색, 글꼴은 '꾸미기'에서 언제든 바꿀 수 있습니다.</p>
              </div>
            ) : (
              <div className="hello">
                <Avatar character={profile.character} size={96} />
                <p>안녕하세요, {name}입니다! 무엇을 도와 드릴까요?</p>
                <p className="muted">예: "내일 오후 2시에 6명 회의실 찾아 줘", "다음 주에 동료와 1시간 회의 잡아 줘"</p>
              </div>
            )}
          </div>
        ) : (
          <ol className="msg-list">
            {(messages ?? []).map((m) => {
              const mine = m.role === "owner";
              const waiting = mine && !answered.has(m.id) && Date.now() - Date.parse(m.ts) < STALE_MS;
              const replyTo = !mine && m.data?.reply_to ? byId.get(m.data.reply_to) : undefined;
              const quote = replyTo && replyTo.id !== prevOwner ? replyTo.text : null; // reply to an older request
              if (mine) prevOwner = m.id;
              return (
                <li key={m.id} className={"msg" + (mine ? " msg--mine" : "")}>
                  {!mine && <Avatar character={profile.character} size={32} />}
                  <div className="bubble">
                    <p className="msg-head">
                      <span className="who">{mine ? "나" : name}</span>
                      <time dateTime={m.ts} title={kstFull(m.ts)}>{kstTime(m.ts)} KST</time>
                      {waiting && <span className="working">처리 중</span>}
                    </p>
                    {quote && <p className="reply-quote">↪ {quote.slice(0, 80)}</p>}
                    <Body text={m.text} onNavigate={onNavigate} />
                  </div>
                </li>
              );
            })}
            {temps.map((t) => (
              <li key={t.key} className="msg msg--mine">
                <div className="bubble">
                  <p className="msg-head"><span className="who">나</span><span className="working">보내는 중</span></p>
                  <p className="msg-text">{t.text}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        {inflight.length > 0 && (
          <p className="inflight" role="status">
            <span className="spinner" aria-hidden="true" />
            {josa(name, "이/가")} 요청 {inflight.length}건을 처리하고 있습니다. 다른 요청도 바로 보낼 수 있습니다.
          </p>
        )}
        <div className="quick" role="group" aria-label="빠른 요청">
          {QUICK.map((q) => (
            <button key={q.text} type="button" className="chip" onClick={() => submit(q.text)}>{q.label}</button>
          ))}
        </div>
        <label htmlFor="composer-input" className="sr-only">{name}에게 보낼 메시지</label>
        <div className="composer-row">
          <textarea
            id="composer-input"
            ref={inputRef}
            rows={2}
            value={text}
            placeholder={`${name}에게 요청하십시오 (Enter: 보내기, Shift+Enter: 줄 바꿈)`}
            aria-describedby="composer-help"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit(text);
              }
            }}
          />
          <button type="submit" className="btn btn--primary" disabled={!text.trim()}>보내기</button>
        </div>
        <p id="composer-help" className="sr-only">Enter 키로 보내고 Shift+Enter로 줄을 바꿉니다. 처리 중에도 새 요청을 보낼 수 있습니다.</p>
      </form>
    </section>
  );
}
