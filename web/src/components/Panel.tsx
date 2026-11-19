import type { ReactNode } from "react";
import { IconChevronDown, IconChevronRight, IconMaximize, IconRestore } from "../icons";

export type PanelId = "rooms" | "main" | "approvals" | "inbox";

interface Props {
  id: PanelId;
  title: ReactNode;
  headingId: string;
  collapsed: boolean;
  maximized: boolean;
  onCollapse(): void;
  onMaximize(): void;
  label: string; // plain text name for the buttons
  tools?: ReactNode;
  className?: string;
  as?: "section" | "nav";
  children: ReactNode;
}

/** A pane with minimize (fold to the header) and maximize (fill the window). Both are buttons: keyboard works. */
export function Panel({ id, title, headingId, collapsed, maximized, onCollapse, onMaximize, label, tools, className, as = "section", children }: Props) {
  const Tag = as;
  const bodyId = `${id}-body`;
  return (
    <Tag className={"panel " + (className ?? "")} aria-labelledby={headingId} data-collapsed={collapsed || undefined}>
      <div className="panel-head">
        <h2 id={headingId} className="pane-title" tabIndex={-1}>{title}</h2>
        <div className="panel-buttons">
          {tools}
          <button type="button" className="icon-btn" aria-expanded={!collapsed} aria-controls={bodyId}
                  title={collapsed ? `${label} 펼치기` : `${label} 최소화`} onClick={onCollapse}>
            {collapsed ? <IconChevronRight /> : <IconChevronDown />}
            <span className="sr-only">{collapsed ? `${label} 펼치기` : `${label} 최소화`}</span>
          </button>
          <button type="button" className="icon-btn icon-btn--max" aria-pressed={maximized}
                  title={maximized ? `${label} 원래 크기 (Esc)` : `${label} 최대화`} onClick={onMaximize}>
            {maximized ? <IconRestore /> : <IconMaximize />}
            <span className="sr-only">{maximized ? `${label} 원래 크기` : `${label} 최대화`}</span>
          </button>
        </div>
      </div>
      <div id={bodyId} className="panel-body" hidden={collapsed}>{children}</div>
    </Tag>
  );
}
