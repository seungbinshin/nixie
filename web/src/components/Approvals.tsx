import { useEffect, useRef } from "react";
import type { Approval } from "../types";
import { kstFull, kstShort } from "../time";

export interface DecisionState {
  busy: boolean;
  result?: string;
  error?: string;
}

interface Props {
  items: Approval[] | null;
  decisions: Record<string, DecisionState>;
  focusId?: string;
  onDecide(a: Approval, approve: boolean): void;
  onOpenRoom(roomId: string): void;
}

/** Pending approvals. Keyboard: Tab to a button, Enter or Space. A second press while busy does nothing. */
export function Approvals({ items, decisions, focusId, onDecide, onOpenRoom }: Props) {
  const focusRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focusId && focusRef.current) focusRef.current.focus();
  }, [focusId, items]);

  return (
    <section className="panel" aria-labelledby="approvals-h">
      <h2 id="approvals-h" className="pane-title" tabIndex={-1}>
        승인 대기 {items && items.length > 0 && <span className="count">{items.length}</span>}
      </h2>
      {items === null ? (
        <p className="muted pad" role="status">불러오는 중…</p>
      ) : items.length === 0 ? (
        <p className="muted pad">승인을 기다리는 요청이 없습니다.</p>
      ) : (
        <ul className="cards">
          {items.map((a) => {
            const d = decisions[a.id] ?? { busy: false };
            const done = Boolean(d.result);
            return (
              <li key={a.id}>
                <article className={"card" + (a.id === focusId ? " card--focus" : "")} aria-labelledby={`ap-${a.id}`}
                         tabIndex={-1} ref={a.id === focusId ? focusRef : undefined}>
                  <h3 id={`ap-${a.id}`} className="card-title">{a.title}</h3>
                  <p className="card-body">{a.summary}</p>
                  <p className="card-meta">
                    <time dateTime={a.created} title={kstFull(a.created)}>{kstShort(a.created)}</time>
                    {a.room_id && (
                      <>
                        {" · "}
                        <button type="button" className="linklike" onClick={() => onOpenRoom(a.room_id!)}>대화 보기</button>
                      </>
                    )}
                  </p>
                  {done ? (
                    <p className="card-result" role="status">{d.result}</p>
                  ) : (
                    <div className="card-actions">
                      <button type="button" className="btn btn--primary" disabled={d.busy} aria-busy={d.busy}
                              aria-describedby={`ap-${a.id}`} onClick={() => onDecide(a, true)}>
                        승인
                      </button>
                      <button type="button" className="btn" disabled={d.busy} aria-busy={d.busy}
                              aria-describedby={`ap-${a.id}`} onClick={() => onDecide(a, false)}>
                        거절
                      </button>
                    </div>
                  )}
                  {d.error && <p className="error" role="alert">{d.error}</p>}
                </article>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
