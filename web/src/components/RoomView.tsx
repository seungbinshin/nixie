import { Fragment, useEffect, useRef, type ReactNode } from "react";
import { Avatar } from "../characters";
import type { RoomDetail } from "../types";
import { Hero } from "./Hero";
import { Markdown } from "./Markdown";
import { kstDay, kstFull, kstTime } from "../time";

interface Props {
  room: RoomDetail | null;
  error: string | null;
  me: string;
  onGoChat(): void;
  onNavigate(href: string): void;
  menuExpanded: boolean;
  onMenu(): void;
  extraTools?: ReactNode;
}

/** Agent-to-agent conversation. Owners read it; there is no input here. */
export function RoomView({ room, error, me, onGoChat, onNavigate, menuExpanded, onMenu, extraTools }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" }); // block body: newer browsers return a Promise here
  }, [room?.lines.length]);

  if (error) return <p className="pad error" role="alert">{error}</p>;
  if (!room) return <p className="pad muted" role="status">대화를 불러오는 중…</p>;
  const names = room.participants.map((p) => p.name).join(" · ");
  const agents = room.participants.map((p) => p.agent_name || `${p.name}님 에이전트`).join(" · ");
  let prevConv = "";
  let prevDay = "";
  let prevSpeaker = "";
  return (
    <section className="conversation" aria-labelledby="main-h">
      <Hero
        avatars={<>{room.participants.map((p) => (
          <Avatar key={p.id} character={p.character} size={60} fallback={p.name}
                  label={`${p.agent_name || "에이전트"} (${p.name}님)`} />
        ))}</>}
        title={agents}
        subtitle={<>
          <span>{names}</span>
          <span className="tag">읽기 전용</span>
          <span className={room.integrity_ok ? "ok" : "error"}>
            {room.integrity_ok ? "기록 무결성 확인됨" : "기록 무결성 오류"}
          </span>
        </>}
        menuExpanded={menuExpanded}
        onMenu={onMenu}
        right={extraTools}
      />
      <div className="messages messages--hero" role="log" aria-label={`${names} 대화 기록`} aria-live="polite" tabIndex={0}>
        {room.lines.length === 0 ? (
          <p className="muted pad">아직 이 방에서 에이전트가 나눈 대화가 없습니다.</p>
        ) : (
          <ol className="msg-list">
            {room.lines.map((l, i) => {
              const day = kstDay(l.ts);
              const newConv = l.conversation_id !== prevConv;
              const newDay = day !== prevDay;
              prevConv = l.conversation_id;
              prevDay = day;
              const mine = l.speaker === me;
              const unread = l.id > room.last_read;
              const who = room.participants.find((p) => p.id === l.speaker);
              const agentName = who?.agent_name || "에이전트";
              const first = newDay || newConv || l.speaker !== prevSpeaker;
              prevSpeaker = l.speaker;
              const next = room.lines[i + 1];
              const last = !next || next.speaker !== l.speaker || next.conversation_id !== l.conversation_id;
              return (
                <Fragment key={l.id}>
                {(newDay || newConv) && (
                  <li className="sep"><span>{newDay ? day : "새 요청"}</span></li>
                )}
                <li className={"msg msg--room" + (mine ? " msg--mine" : "") + (last ? " msg--last" : "")}>
                  <span className="msg-avatar">{first && <Avatar character={who?.character} size={36} fallback={l.speaker_name} />}</span>
                  <div className="msg-col">
                    {first && (
                      <p className="msg-who"><span className="who">{agentName}</span>{" "}
                        <span className="muted">{mine ? "내 에이전트" : `${l.speaker_name}님의 에이전트`}</span></p>
                    )}
                    <div className="bubble">
                      {!first && <span className="sr-only">{agentName}: </span>}
                      <Markdown text={l.text} onNavigate={onNavigate} />
                    </div>
                    {last && (
                      <p className="msg-meta">
                        <time dateTime={l.ts} title={kstFull(l.ts)}>{kstTime(l.ts)} KST</time>
                        {unread && <span className="sr-only">(새 메시지)</span>}
                      </p>
                    )}
                  </div>
                </li>
                </Fragment>
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
