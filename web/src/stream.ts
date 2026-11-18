// Real-time changes from the gateway (Server-Sent Events). The stream does not wake the agent.
// The browser reconnects by itself after network errors. After an HTTP error (for example an expired SSO
// session) the stream closes; then we check the session and either go to SSO or reconnect with backoff.
import type { Change } from "./types";

export type StreamState = "connecting" | "live" | "offline";

export interface StreamHandlers {
  onChange(c: Change): void;
  onResync(): void;
  onState(s: StreamState): void;
  checkSession(): Promise<boolean>; // false -> signed out (the caller redirects)
}

export function openStream(url: string, h: StreamHandlers): () => void {
  let es: EventSource | null = null;
  let stopped = false;
  let delay = 1000;
  let timer: number | undefined;
  let lastSeq = -1;

  const connect = () => {
    if (stopped) return;
    h.onState("connecting");
    es = new EventSource(url, { withCredentials: true });
    es.addEventListener("hello", (e) => {
      const seq = JSON.parse((e as MessageEvent).data).seq as number;
      if (lastSeq >= 0 && seq !== lastSeq) h.onResync(); // missed changes while away
      lastSeq = seq;
      delay = 1000;
      h.onState("live");
    });
    es.addEventListener("change", (e) => {
      const c = JSON.parse((e as MessageEvent).data) as Change;
      lastSeq = c.seq;
      h.onChange(c);
    });
    es.addEventListener("resync", (e) => {
      lastSeq = JSON.parse((e as MessageEvent).data).seq;
      h.onResync();
    });
    es.onerror = () => {
      if (!es || es.readyState !== EventSource.CLOSED) {
        h.onState("connecting"); // the browser retries by itself
        return;
      }
      h.onState("offline");
      es = null;
      void h.checkSession().then((ok) => {
        if (!ok || stopped) return;
        timer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30000);
      });
    };
  };
  connect();
  return () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    es?.close();
  };
}
