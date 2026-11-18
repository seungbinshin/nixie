import { useEffect, useRef } from "react";
import type { RoomDetail } from "../types";
import { kstDay, kstFull, kstTime } from "../time";

interface Props {
  room: RoomDetail | null;
  error: string | null;
  me: string;
  onGoChat(): void;
}

/** Agent-to-agent conversation. Owners read it; there is no input here. */
export function RoomView({ room, error, me, onGoChat }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" }); // block body: newer browsers return a Promise here
  }, [room?.lines.length]);

  if (error) return <p className="pad error" role="alert">{error}</p>;
  if (!room) return <p className="pad muted" role="status">대화를 불러오는 중…</p>;
  const names = room.participants.map((p) => p.name).join(" · ");
  let prevConv = "";
  let prevDay = "";
  return (
    <section className="conversation" aria-labelledby="main-h">
      <header className="conv-head">
        <h2 id="main-h" tabIndex={-1}>{names}</h2>
        <p className="conv-meta">
          <span className="tag">읽기 전용</span>
          <span className={room.integrity_ok ? "ok" : "error"}>
            {room.integrity_ok ? "기록 무결성 확인됨" : "기록 무결성 오류"}
          </span>
        </p>
      </header>
      <div className="messages" role="log" aria-label={`${names} 대화 기록`} aria-live="polite" tabIndex={0}>
        {room.lines.length === 0 ? (
          <p className="muted pad">아직 이 방에서 에이전트가 나눈 대화가 없습니다.</p>
        ) : (
          <ol className="msg-list">
            {room.lines.map((l) => {
              const day = kstDay(l.ts);
              const newConv = l.conversation_id !== prevConv;
              const newDay = day !== prevDay;
              prevConv = l.conversation_id;
              prevDay = day;
              const mine = l.speaker === me;
              const unread = l.id > room.last_read;
              return (
                <li key={l.id} className={"msg" + (mine ? " msg--mine" : "")}>
                  {(newDay || newConv) && (
                    <p className="sep"><span>{newDay ? day : "새 요청"}</span></p>
                  )}
                  <div className="bubble">
                    <p className="msg-head">
                      <span className="who">{mine ? `${l.speaker_name}의 에이전트(내 에이전트)` : `${l.speaker_name}의 에이전트`}</span>
                      <time dateTime={l.ts} title={kstFull(l.ts)}>{kstTime(l.ts)} KST</time>
                      {unread && <span className="sr-only">(새 메시지)</span>}
                    </p>
                    <p className="msg-text">{l.text}</p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
        <div ref={endRef} />
      </div>
      <footer className="readonly-note">
        <p>
          에이전트 간 대화에는 글을 쓸 수 없습니다. 요청은{" "}
          <button type="button" className="linklike" onClick={onGoChat}>내 에이전트와 대화</button>에서 하십시오.
        </p>
      </footer>
    </section>
  );
}
