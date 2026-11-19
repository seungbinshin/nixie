// Icons as inline SVG. Symbol characters (⤢ ▾ ↪ 📚 ...) depend on the fonts of the PC: on Windows some are
// missing from Malgun Gothic / Segoe UI and show as empty boxes or in another font. SVG looks the same everywhere.
import type { ReactNode } from "react";

function Svg({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"
         fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const IconChevronDown = () => <Svg><path d="m6 9 6 6 6-6" /></Svg>;
export const IconChevronRight = () => <Svg><path d="m9 6 6 6-6 6" /></Svg>;
export const IconMaximize = () => <Svg><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" /></Svg>;
export const IconRestore = () => <Svg><path d="M4 14h6v6M20 10h-6V4M10 14l-7 7M14 10l7-7" /></Svg>;
export const IconReply = () => <Svg size={14}><path d="M9 7 4 12l5 5" /><path d="M4 12h10a6 6 0 0 1 6 6v1" /></Svg>;
export const IconBack = () => <Svg size={16}><path d="M15 6l-6 6 6 6" /></Svg>;
export const IconFollowUp = () => <Svg size={14}><path d="M6 4v8a4 4 0 0 0 4 4h8" /><path d="m15 13 3 3-3 3" /></Svg>;
export const IconBook = () => (
  <Svg size={20}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
    <path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5" />
    <path d="M9 8h7M9 11.5h5" />
  </Svg>
);
export const IconArrowUp = () => <Svg size={14}><path d="M12 19V5M6 11l6-6 6 6" /></Svg>;
export const IconArrowDown = () => <Svg size={14}><path d="M12 5v14M6 13l6 6 6-6" /></Svg>;
