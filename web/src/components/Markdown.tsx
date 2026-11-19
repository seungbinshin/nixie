import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { classifyLink } from "../api";

interface Props {
  text: string;
  /** Heading levels shift by this value (wiki documents). Without it, headings render as bold lines (chat). */
  headingShift?: number;
  /** In-app navigation for same-origin links and wiki links ("<id>.md"). */
  onNavigate?(href: string): void;
}

const WIKI_LINK = /^([A-Za-z0-9-]+)\.md$/;

/** Markdown (GitHub flavor) without raw HTML. Links: only http(s) and in-app routes; other systems open in a new tab. */
export function Markdown({ text, headingShift, onNavigate }: Props) {
  const heading = (level: number) => ({ children }: { children?: ReactNode }) => {
    if (headingShift === undefined) return <p className="md-h">{children}</p>;
    const n = Math.min(6, level + headingShift);
    const Tag = `h${n}` as "h2";
    return <Tag className="md-heading">{children}</Tag>;
  };
  const components: Components = {
    h1: heading(1), h2: heading(2), h3: heading(3), h4: heading(4), h5: heading(5), h6: heading(6),
    a({ href, children }) {
      const wiki = href ? WIKI_LINK.exec(href) : null;
      if (wiki) {
        const to = `/?wiki=${encodeURIComponent(wiki[1])}`;
        return <a href={to} onClick={(e) => { if (onNavigate && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onNavigate(to); } }}>{children}</a>;
      }
      const link = classifyLink(href);
      if (!link) return <span>{children}</span>;
      if (link.kind === "internal") {
        const to = link.url.pathname + link.url.search;
        return <a href={to} onClick={(e) => { if (onNavigate && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onNavigate(to); } }}>{children}</a>;
      }
      return (
        <a href={link.url.href} target="_blank" rel="noopener noreferrer">
          {children}<span className="sr-only"> (새 탭)</span>
        </a>
      );
    },
    img({ src, alt }) {
      const link = classifyLink(typeof src === "string" ? src : null);
      return link ? <a href={link.url.href} target="_blank" rel="noopener noreferrer">{alt || "이미지 열기"}</a> : <span>{alt}</span>;
    },
    table({ children }) {
      return <div className="md-table"><table>{children}</table></div>;
    },
  };
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
}
