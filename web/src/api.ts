// HTTP client of the web UI.
// - Credentials are cookies only (SSO proxy session; nixie_session before SSO). No token in JS, so a new tab
//   or a deep link keeps the sign-in.
// - Every request sends X-Nixie-Request: 1. The gateway refuses state changes without it (CSRF protection).
// - 401 -> the SSO sign-in URL, then back to the same page (path + query).
import type { Me } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export class AuthRequired extends Error {
  constructor(public loginUrl: string | null) {
    super("authentication required");
  }
}

let redirecting = false;

export function loginTarget(loginUrl: string, here: Location = window.location): string {
  const rd = here.pathname + here.search;
  const sep = loginUrl.includes("?") ? "&" : "?";
  return `${loginUrl}${sep}rd=${encodeURIComponent(rd)}`;
}

/** Send the browser to SSO. Called once, even when many requests fail together. */
export function goToLogin(loginUrl: string | null): void {
  if (!loginUrl || redirecting) return;
  redirecting = true;
  window.location.assign(loginTarget(loginUrl));
}

async function detail(r: Response): Promise<{ detail: string; login_url?: string | null }> {
  try {
    const j = await r.json();
    return { detail: typeof j.detail === "string" ? j.detail : r.statusText, login_url: j.login_url };
  } catch {
    return { detail: r.statusText };
  }
}

export async function request<T>(url: string, init: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", "X-Nixie-Request": "1" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(url, {
    method: init.method ?? "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init.signal,
  });
  if (r.status === 401) {
    const d = await detail(r);
    const login = d.login_url === undefined ? "/oauth2/start" : d.login_url;
    goToLogin(login);
    throw new AuthRequired(login);
  }
  if (!r.ok) {
    const d = await detail(r);
    throw new ApiError(r.status, d.detail);
  }
  return (await r.json()) as T;
}

export function getMe(): Promise<Me> {
  return request<Me>("/auth/me");
}

/** Run `fn` once per key at a time. A second call while the first runs returns the same promise. */
const inflight = new Map<string, Promise<unknown>>();
export function once<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const running = inflight.get(key);
  if (running) return running as Promise<T>;
  const p = fn().finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

export function isBusy(key: string): boolean {
  return inflight.has(key);
}

/** Only http(s) links leave the app. Same-origin links stay in this tab. */
export function classifyLink(raw: string | null | undefined, origin: string = window.location.origin):
  { kind: "internal"; url: URL } | { kind: "external"; url: URL } | null {
  if (!raw) return null;
  let u: URL;
  try {
    u = new URL(raw, origin);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  return u.origin === origin ? { kind: "internal", url: u } : { kind: "external", url: u };
}
