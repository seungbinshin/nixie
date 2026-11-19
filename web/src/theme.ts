// Theme: mode (system/light/dark), accent color (any #rrggbb), font. Every derived color keeps WCAG AA contrast
// (4.5:1 for text), whatever accent the owner picks.
export type Mode = "system" | "light" | "dark";
export type FontId = "gowun" | "jua" | "dongle" | "system";
export interface Theme { mode: Mode; accent: string; font: FontId }

export const DEFAULT_THEME: Theme = { mode: "system", accent: "#4f7cff", font: "gowun" };

export const PRESETS: { name: string; color: string }[] = [
  { name: "하늘", color: "#4f7cff" },
  { name: "딸기우유", color: "#ff6b8b" },
  { name: "민트", color: "#22b8a0" },
  { name: "라벤더", color: "#8b6cf6" },
  { name: "귤", color: "#ff9636" },
  { name: "레몬", color: "#f2c230" },
  { name: "숲", color: "#3f9b4f" },
  { name: "하이닉스 레드", color: "#e4002b" },
];

export const FONTS: Record<FontId, { label: string; body: string; display: string; scale: number }> = {
  gowun: { label: "고운돋움", body: "'Gowun Dodum'", display: "'Jua'", scale: 1 },
  jua: { label: "주아", body: "'Jua'", display: "'Jua'", scale: 1 },
  dongle: { label: "동글", body: "'Dongle'", display: "'Dongle'", scale: 1.5 },
  system: { label: "시스템 글꼴", body: "'Segoe UI', 'Malgun Gothic'", display: "'Segoe UI', 'Malgun Gothic'", scale: 1 },
};
const FALLBACK = ", 'Malgun Gothic', 'Apple SD Gothic Neo', system-ui, sans-serif";

type RGB = [number, number, number];
const hex = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
const toHex = (c: RGB) => "#" + c.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("");
const mix = (a: RGB, b: RGB, t: number): RGB => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * t) as RGB;

function lum(c: RGB): number {
  const [r, g, b] = c.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [x, y] = [lum(hex(a)), lum(hex(b))].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

/** Move `fg` toward `toward` until it has `min` contrast against every color in `bgs`. */
export function ensureContrast(fg: string, bgs: string[], toward: string, min = 4.5): string {
  let c = hex(fg);
  for (let i = 0; i <= 20; i++) {
    const cur = toHex(c);
    if (bgs.every((b) => contrast(cur, b) >= min)) return cur;
    c = mix(c, hex(toward), 0.08);
  }
  return toward;
}

export const PALETTE = {
  light: { bg: "#f7f6f3", surface: "#ffffff", surface2: "#efede8", text: "#1f2328", muted: "#56606b", border: "#dcd8d0",
           danger: "#c0262d", dangerText: "#ffffff", ok: "#0b7a45", warn: "#8a5a00" },
  dark: { bg: "#121317", surface: "#1b1d23", surface2: "#252832", text: "#eceef2", muted: "#b4bac4", border: "#343845",
          danger: "#ff8f8f", dangerText: "#1a0605", ok: "#6fdba5", warn: "#f3c46b" },
};

export function derive(accent: string, dark: boolean): Record<string, string> {
  const p = dark ? PALETTE.dark : PALETTE.light;
  const white = "#ffffff", ink = "#14161a";
  // button / badge: the accent, with the text color that reads best; adjust the accent only when needed
  const btnText = contrast(accent, white) >= contrast(accent, ink) ? white : ink;
  const btn = ensureContrast(accent, [btnText], btnText === white ? ink : white);
  const soft = toHex(mix(hex(p.surface), hex(accent), dark ? 0.28 : 0.14));
  // accent as text (links, focus ring): readable on bg, surface and soft
  const link = ensureContrast(accent, [p.bg, p.surface, soft, p.surface2], dark ? white : ink);
  const muted = ensureContrast(p.muted, [p.bg, p.surface, soft, p.surface2], dark ? white : ink);
  return {
    "--bg": p.bg, "--surface": p.surface, "--surface-2": p.surface2, "--text": p.text, "--muted": muted,
    "--border": p.border, "--accent": btn, "--accent-text": btnText, "--accent-soft": soft, "--link": link,
    "--focus": link, "--mine": soft, "--danger": p.danger, "--danger-text": p.dangerText, "--ok": p.ok, "--warn": p.warn,
  };
}

const media = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
let current: Theme = DEFAULT_THEME;

export function applyTheme(t: Theme): void {
  current = t;
  const dark = t.mode === "dark" || (t.mode === "system" && !!media?.matches);
  const root = document.documentElement;
  root.dataset.mode = dark ? "dark" : "light";
  root.style.colorScheme = dark ? "dark" : "light";
  for (const [k, v] of Object.entries(derive(t.accent, dark))) root.style.setProperty(k, v);
  const f = FONTS[t.font] ?? FONTS.gowun;
  root.style.setProperty("--font-body", f.body + FALLBACK);
  root.style.setProperty("--font-display", f.display + FALLBACK);
  root.style.setProperty("--font-scale", String(f.scale));
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? PALETTE.dark.surface : t.accent);
}

media?.addEventListener?.("change", () => {
  if (current.mode === "system") applyTheme(current);
});

const KEY = "nixie.theme";
export function cachedTheme(): Theme {
  try {
    const t = JSON.parse(localStorage.getItem(KEY) || "null");
    if (t && /^#[0-9a-f]{6}$/i.test(t.accent) && t.font in FONTS && ["system", "light", "dark"].includes(t.mode)) return t;
  } catch {
    /* private mode or bad value */
  }
  return DEFAULT_THEME;
}
export function cacheTheme(t: Theme): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(t));
  } catch {
    /* ignore */
  }
}
