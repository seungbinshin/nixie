import { useRef } from "react";
import type { RoomSummary } from "../types";
import { hrefFor } from "../route";
import { kstFull, kstShort } from "../time";

interface Props {
  rooms: RoomSummary[] | null;
  me: string;
  current: string | null; // room id, or null for "my agent" chat
  chatUnread: boolean;
  onOpen(roomId: string | null): void;
}

/** Arrow keys move between items; Enter opens (links). Home/End jump. */
function onListKey(e: React.KeyboardEvent<HTMLElement>) {
  const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
  if (!keys.includes(e.key)) return;
  const links = Array.from(e.currentTarget.querySelectorAll<HTMLAnchorElement>("a[data-nav]"));
  const i = links.indexOf(document.activeElement as HTMLAnchorElement);
  if (i < 0) return;
  e.preventDefault();
  const next = e.key === "Home" ? 0 : e.key === "End" ? links.length - 1
    : (i + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
  links[next].focus();
}

export function RoomList({ rooms, me, current, chatUnread, onOpen }: Props) {
  const navRef = useRef<HTMLElement>(null);
  const click = (roomId: string | null) => (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // new tab/window keeps the session
    e.preventDefault();
    onOpen(roomId);
  };
  return (
    <nav ref={navRef} className="rooms" aria-labelledby="rooms-h" onKeyDown={onListKey}>
      <h2 id="rooms-h" className="pane-title" tabIndex={-1}>대화방</h2>
      <ul className="room-list">
        <li>
          <a data-nav href={hrefFor({})} className="room-item room-item--agent"
             aria-current={current === null ? "page" : undefined} onClick={click(null)}>
            <span className="room-name">내 에이전트</span>
            <span className="room-last">업무 요청과 질문은 여기에서 합니다</span>
            {chatUnread && <span className="dot" role="img" aria-label="새 응답" />}
          </a>
        </li>
      </ul>
      <h3 className="list-title">에이전트 간 대화 <span className="muted">(읽기 전용)</span></h3>
      {rooms === null ? (
        <p className="muted pad" role="status">불러오는 중…</p>
      ) : rooms.length === 0 ? (
        <p className="muted pad">연결된 에이전트가 없습니다.</p>
      ) : (
        <ul className="room-list">
          {rooms.map((r) => {
            const names = r.participants.map((p) => p.name).join(" · ");
            const peer = r.participants.find((p) => !p.self)?.name ?? r.peer;
            const last = r.last ? `${r.last.speaker === me ? "내 에이전트" : r.last.speaker_name}: ${r.last.text}` : "아직 대화가 없습니다";
            return (
              <li key={r.room_id}>
                <a data-nav href={hrefFor({ room: r.room_id })} onClick={click(r.room_id)}
                   className={"room-item" + (r.unread > 0 ? " room-item--unread" : "")}
                   aria-current={current === r.room_id ? "page" : undefined}>
                  <span className="room-row">
                    <span className="room-name" title={names}>{names}</span>
                    {r.updated && <time className="room-time" dateTime={r.updated} title={kstFull(r.updated)}>{kstShort(r.updated)}</time>}
                  </span>
                  <span className="room-row">
                    <span className="room-last">{last}</span>
                    {r.unread > 0 && (
                      <span className="badge" aria-label={`${peer} 방 읽지 않은 메시지 ${r.unread}개`}>{r.unread > 99 ? "99+" : r.unread}</span>
                    )}
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
