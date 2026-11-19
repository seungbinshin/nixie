import { Avatar } from "../characters";
import { hrefFor } from "../route";
import { kstFull, kstShort } from "../time";
import type { Profile, RoomSummary } from "../types";

interface Props {
  rooms: RoomSummary[] | null;
  me: string;
  profile: Profile;
  current: string | null; // room id; "chat" for my agent; "wiki" for the archive
  chatUnread: boolean;
  onNavigate(href: string): void;
}

/** Arrow keys move between items; Enter opens (links). Home/End jump. */
function onListKey(e: React.KeyboardEvent<HTMLElement>) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
  const links = Array.from(e.currentTarget.querySelectorAll<HTMLAnchorElement>("a[data-nav]"));
  const i = links.indexOf(document.activeElement as HTMLAnchorElement);
  if (i < 0) return;
  e.preventDefault();
  const next = e.key === "Home" ? 0 : e.key === "End" ? links.length - 1
    : (i + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
  links[next].focus();
}

export function RoomList({ rooms, me, profile, current, chatUnread, onNavigate }: Props) {
  const link = (href: string) => (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // new tab/window keeps the session
    e.preventDefault();
    onNavigate(href);
  };
  return (
    <div className="rooms" onKeyDown={onListKey}>
      <ul className="room-list">
        <li>
          <a data-nav href={hrefFor({})} className="room-item room-item--agent" aria-current={current === "chat" ? "page" : undefined}
             onClick={link(hrefFor({}))}>
            <span className="room-row">
              <Avatar character={profile.character} size={36} />
              <span className="room-text">
                <span className="room-name">{profile.agent_name} <span className="muted small">내 에이전트</span></span>
                <span className="room-last">업무 요청과 질문은 여기에서 합니다</span>
              </span>
              {chatUnread && <span className="dot" role="img" aria-label="새 응답" />}
            </span>
          </a>
        </li>
        <li>
          <a data-nav href={hrefFor({ wiki: "" })} className="room-item" aria-current={current === "wiki" ? "page" : undefined}
             onClick={link(hrefFor({ wiki: "" }))}>
            <span className="room-row">
              <span className="avatar avatar--icon" aria-hidden="true">📚</span>
              <span className="room-text">
                <span className="room-name">업무 위키</span>
                <span className="room-last">자동 보관된 업무 기록 (수정 불가)</span>
              </span>
            </span>
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
            const peer = r.participants.find((p) => !p.self);
            const last = r.last ? `${r.last.speaker === me ? profile.agent_name : r.last.speaker_name}: ${r.last.text}` : "아직 대화가 없습니다";
            return (
              <li key={r.room_id}>
                <a data-nav href={hrefFor({ room: r.room_id })} onClick={link(hrefFor({ room: r.room_id }))}
                   className={"room-item" + (r.unread > 0 ? " room-item--unread" : "")}
                   aria-current={current === r.room_id ? "page" : undefined}>
                  <span className="room-row">
                    <Avatar character={peer?.character} size={36} fallback={peer?.name} />
                    <span className="room-text">
                      <span className="room-row">
                        <span className="room-name" title={names}>{names}</span>
                        {r.updated && <time className="room-time" dateTime={r.updated} title={kstFull(r.updated)}>{kstShort(r.updated)}</time>}
                      </span>
                      <span className="room-row">
                        <span className="room-last">{last}</span>
                        {r.unread > 0 && (
                          <span className="badge" aria-label={`${peer?.name ?? r.peer} 방 읽지 않은 메시지 ${r.unread}개`}>{r.unread > 99 ? "99+" : r.unread}</span>
                        )}
                      </span>
                    </span>
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
