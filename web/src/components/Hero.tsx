import type { ReactNode } from "react";

interface Props {
  avatars: ReactNode;
  title: string; // the page heading (#main-h)
  subtitle?: ReactNode;
  menuExpanded: boolean;
  onMenu(): void;
  right?: ReactNode;
}

/** Chat header: the agent's picture in the middle with its name below, a round menu button on the left
 *  (opens or folds the room list), actions on the right. It floats over the messages with a fade. */
export function Hero({ avatars, title, subtitle, menuExpanded, onMenu, right }: Props) {
  return (
    <header className="hero">
      <div className="hero-left">
        <button type="button" className="round-btn" aria-expanded={menuExpanded} aria-controls="rooms-body"
                title={menuExpanded ? "대화방 목록 접기" : "대화방 목록 열기"} onClick={onMenu}>
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
            <path d="M5 8h14M5 16h14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
          <span className="sr-only">대화방 목록</span>
        </button>
      </div>
      <div className="hero-center">
        <div className="hero-avatars">{avatars}</div>
        <h2 id="main-h" tabIndex={-1} className="hero-name">{title}</h2>
        {subtitle && <p className="hero-sub conv-meta">{subtitle}</p>}
      </div>
      <div className="hero-right">{right}</div>
    </header>
  );
}
