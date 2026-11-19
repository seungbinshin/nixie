import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Avatar, CHARACTERS, type CharacterId } from "../characters";
import { kstFull, kstTime } from "../time";
import type { ChatMessage, Profile } from "../types";
import { IconReply } from "../icons";
import { josa } from "../josa";
import { Hero } from "./Hero";
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
  menuExpanded: boolean;
  onMenu(): void;
  extraTools?: ReactNode; // maximize button
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
export function AgentChat({ owner, profile, messages, inflight, onSend, onCustomize, onChoose, onNavigate, menuExpanded, onMenu, extraTools }: Props) {
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
    if (inputRef.current) inputRef.current.style.height = "auto";
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
      <Hero
        avatars={<Avatar character={profile.character} size={72} label={`${name} (${CHARACTERS[profile.character].kind})`} />}
        title={name}
        menuExpanded={menuExpanded}
        onMenu={onMenu}
        right={<>
          <button type="button" className="pill-btn" onClick={onCustomize}>꾸미기</button>
          {extraTools}
        </>}
      />
      <div className="messages messages--hero" role="log" aria-label={`${josa(name, "와/과")}의 대화`} aria-live="polite" tabIndex={0}>
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
                <p>안녕하세요, {name}입니다! 무엇을 도와 드릴까요?</p>
                <p className="muted">{owner}님의 업무 에이전트 · 회의실, 휴가, 결재, 문서 검색, IT 요청, 동료 일정 조율</p>
                <p className="muted">예: "내일 오후 2시에 6명 회의실 찾아 줘", "다음 주에 동료와 1시간 회의 잡아 줘"</p>
              </div>
            )}
          </div>
        ) : (
          <ol className="msg-list">
            {(messages ?? []).map((m, i, all) => {
              const mine = m.role === "owner";
              const waiting = mine && !answered.has(m.id) && Date.now() - Date.parse(m.ts) < STALE_MS;
              const replyTo = !mine && m.data?.reply_to ? byId.get(m.data.reply_to) : undefined;
              const quote = replyTo && replyTo.id !== prevOwner ? replyTo.text : null; // reply to an older request
              if (mine) prevOwner = m.id;
              const next = all[i + 1];
              const lastOfGroup = !next || next.role !== m.role || Date.parse(next.ts) - Date.parse(m.ts) > 5 * 60000;
              return (
                <li key={m.id} className={"msg" + (mine ? " msg--mine" : "") + (lastOfGroup ? " msg--last" : "")}>
                  <div className="msg-col">
                    <div className="bubble">
                      <span className="sr-only">{mine ? "나: " : `${name}: `}</span>
                      {quote && <p className="reply-quote"><IconReply /> {quote.slice(0, 80)}</p>}
                      <Body text={m.text} onNavigate={onNavigate} />
                    </div>
                    {(lastOfGroup || waiting) && (
                      <p className="msg-meta">
                        <time dateTime={m.ts} title={kstFull(m.ts)}>{kstTime(m.ts)} KST</time>
                        {waiting && <span className="working">처리 중</span>}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
            {temps.map((t) => (
              <li key={t.key} className="msg msg--mine msg--last">
                <div className="msg-col">
                  <div className="bubble"><span className="sr-only">나: </span><p className="msg-text">{t.text}</p></div>
                  <p className="msg-meta"><span className="working">보내는 중</span></p>
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
        <div className="composer-pill">
          <textarea
            id="composer-input"
            ref={inputRef}
            rows={1}
            value={text}
            placeholder={`${name}에게 메시지 보내기`}
            aria-describedby="composer-help"
            onChange={(e) => {
              setText(e.target.value);
              const el = e.target; // grow with the text, up to about 6 lines
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                e.preventDefault();
                submit(text);
              }
            }}
          />
          <button type="submit" className="send-btn" disabled={!text.trim()} title="보내기 (Enter)">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
              <path d="M12 19V6M6 11.5 12 5.5l6 6" stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="sr-only">보내기</span>
          </button>
        </div>
        <p id="composer-help" className="sr-only">Enter 키로 보내고 Shift+Enter로 줄을 바꿉니다. 처리 중에도 새 요청을 보낼 수 있습니다.</p>
      </form>
    </section>
  );
}
