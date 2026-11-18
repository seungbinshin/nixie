import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import { kstFull, kstTime } from "../time";

interface Props {
  name: string;
  messages: ChatMessage[] | null;
  sending: boolean;
  onSend(text: string): Promise<boolean>;
}

const QUICK = [
  { label: "승인 대기 목록", text: "/approvals" },
  { label: "가능한 업무", text: "/skills" },
  { label: "내 일정", text: "/events" },
  { label: "도움말", text: "/help" },
];

function Body({ text }: { text: string }) {
  const t = text.trim();
  if ((t.startsWith("{") || t.startsWith("[")) && t.length > 2) {
    try {
      return <pre className="msg-json">{JSON.stringify(JSON.parse(t), null, 2)}</pre>;
    } catch {
      /* plain text */
    }
  }
  return <p className="msg-text">{text}</p>;
}

/** Chat between the owner and the owner's own agent. The only place where the owner types. */
export function AgentChat({ name, messages, sending, onSend }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" }); // block body: newer browsers return a Promise here
  }, [messages?.length, sending]);

  const submit = async (value: string) => {
    const v = value.trim();
    if (!v || sending) return;
    setText("");
    const ok = await onSend(v);
    if (!ok) setText(v); // keep the text so the owner can send it again
    inputRef.current?.focus();
  };

  return (
    <section className="conversation" aria-labelledby="main-h">
      <header className="conv-head">
        <h2 id="main-h" tabIndex={-1}>내 에이전트</h2>
        <p className="conv-meta">{name}님의 업무 에이전트 · 회의실, 휴가, 결재, 문서 검색, IT 요청, 동료 일정 조율</p>
      </header>
      <div className="messages" role="log" aria-label="내 에이전트와의 대화" aria-live="polite" tabIndex={0}>
        {messages === null ? (
          <p className="muted pad" role="status">대화를 불러오는 중…</p>
        ) : messages.length === 0 ? (
          <div className="empty pad">
            <p>무엇을 도와 드릴까요? 예: "내일 오후 2시에 6명 회의실 찾아 줘", "다음 주 Bob과 1시간 회의 잡아 줘".</p>
          </div>
        ) : (
          <ol className="msg-list">
            {messages.map((m) => (
              <li key={m.id} className={"msg" + (m.role === "owner" ? " msg--mine" : "")}>
                <div className="bubble">
                  <p className="msg-head">
                    <span className="who">{m.role === "owner" ? "나" : "에이전트"}</span>
                    <time dateTime={m.ts} title={kstFull(m.ts)}>{kstTime(m.ts)} KST</time>
                  </p>
                  <Body text={m.text} />
                </div>
              </li>
            ))}
          </ol>
        )}
        {sending && <p className="typing" role="status">에이전트가 응답을 준비하고 있습니다…</p>}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); void submit(text); }}>
        <div className="quick" role="group" aria-label="빠른 요청">
          {QUICK.map((q) => (
            <button key={q.text} type="button" className="chip" disabled={sending} onClick={() => void submit(q.text)}>
              {q.label}
            </button>
          ))}
        </div>
        <label htmlFor="composer-input" className="sr-only">에이전트에게 보낼 메시지</label>
        <div className="composer-row">
          <textarea
            id="composer-input"
            ref={inputRef}
            rows={2}
            value={text}
            placeholder="메시지를 입력하십시오 (Enter: 보내기, Shift+Enter: 줄 바꿈)"
            aria-describedby="composer-help"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void submit(text);
              }
            }}
          />
          <button type="submit" className="btn btn--primary" disabled={sending || !text.trim()} aria-busy={sending}>
            보내기
          </button>
        </div>
        <p id="composer-help" className="sr-only">Enter 키로 보내고 Shift+Enter로 줄을 바꿉니다.</p>
      </form>
    </section>
  );
}
