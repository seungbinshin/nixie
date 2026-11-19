import { useState } from "react";
import type { Notice } from "../types";
import { classifyLink } from "../api";
import { kstFull, kstShort } from "../time";

interface Props {
  items: Notice[] | null;
  unread: number;
  onOpen(n: Notice): void; // in-app target (room, approval)
  onInternalLink(url: URL, n: Notice): void;
  onRead(n: Notice): void;
  onReadAll(): void;
}

const SOURCE: Record<string, string> = { nixie: "Nixie", mail: "메일", approval: "결재", external: "사내 시스템" };

/** Notification inbox. Source links: same-origin -> this tab; other systems -> new tab (cookies keep sign-in). */
export function Inbox({ items, unread, onOpen, onInternalLink, onRead, onReadAll }: Props) {
  const [onlyUnread, setOnlyUnread] = useState(false);
  const shown = (items ?? []).filter((n) => !onlyUnread || !n.read_at);
  return (
    <>
      <div className="panel-tools">
        <label className="check">
          <input type="checkbox" checked={onlyUnread} onChange={(e) => setOnlyUnread(e.target.checked)} />
          읽지 않은 알림만
        </label>
        <button type="button" className="btn btn--small" onClick={onReadAll} disabled={unread === 0}>모두 읽음</button>
      </div>
      {items === null ? (
        <p className="muted pad" role="status">불러오는 중…</p>
      ) : shown.length === 0 ? (
        <p className="muted pad">알림이 없습니다.</p>
      ) : (
        <ul className="notices">
          {shown.map((n) => {
            const link = classifyLink(n.source_url);
            const inApp = n.ref_type === "room" || n.ref_type === "approval";
            return (
              <li key={n.id} className={"notice" + (n.read_at ? "" : " notice--unread")}>
                <p className="notice-head">
                  {!n.read_at && <span className="dot" role="img" aria-label="읽지 않음" />}
                  <span className="notice-title">{n.title}</span>
                </p>
                {n.body && <p className="notice-body">{n.body}</p>}
                <p className="notice-meta">
                  <span>{SOURCE[n.source] ?? n.source}</span>
                  {" · "}
                  <time dateTime={n.ts} title={kstFull(n.ts)}>{kstShort(n.ts)}</time>
                </p>
                <div className="notice-actions">
                  {inApp && (
                    <button type="button" className="btn btn--small" onClick={() => onOpen(n)}>
                      {n.ref_type === "approval" ? "승인 요청 보기" : "대화 보기"}
                    </button>
                  )}
                  {link?.kind === "internal" && (
                    <a className="btn btn--small" href={link.url.pathname + link.url.search}
                       onClick={(e) => {
                         if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
                         e.preventDefault();
                         onInternalLink(link.url, n);
                       }}>
                      원문 보기
                    </a>
                  )}
                  {link?.kind === "external" && (
                    <a className="btn btn--small" href={link.url.href} target="_blank" rel="noopener noreferrer"
                       onClick={() => onRead(n)}>
                      원문 열기<span className="sr-only"> (새 탭, {link.url.host})</span>
                    </a>
                  )}
                  {!n.read_at && (
                    <button type="button" className="btn btn--small btn--ghost" onClick={() => onRead(n)}>
                      읽음<span className="sr-only">: {n.title}</span>
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
