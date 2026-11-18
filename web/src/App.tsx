import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, AuthRequired, getMe, goToLogin, isBusy, once, request } from "./api";
import { AgentChat } from "./components/AgentChat";
import { Approvals, type DecisionState } from "./components/Approvals";
import { Inbox } from "./components/Inbox";
import { RoomList } from "./components/RoomList";
import { RoomView } from "./components/RoomView";
import { hrefFor, parseRoute, type Route } from "./route";
import { openStream, type StreamState } from "./stream";
import type { Approval, Change, ChatMessage, Me, Notice, RoomDetail, RoomSummary } from "./types";

type Pane = "rooms" | "main" | "side";
type Topic = Change["topic"];

const STREAM_LABEL: Record<StreamState, string> = {
  connecting: "실시간 연결 중",
  live: "실시간 연결됨",
  offline: "실시간 연결 끊김",
};

function message(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return "에이전트를 시작하지 못했습니다. 잠시 후 다시 시도하십시오.";
    if (e.status === 502) return "에이전트에 연결하지 못했습니다.";
    return e.message;
  }
  if (e instanceof AuthRequired) return "다시 로그인해야 합니다.";
  return "네트워크 오류가 발생했습니다.";
}

function decisionText(res: { status?: string; error?: string; duplicate?: boolean; peer_error?: string }): string {
  const base = res.status === "approved" ? "승인했습니다."
    : res.status === "declined" ? "거절했습니다."
    : res.status === "failed" ? `실행하지 못했습니다: ${res.error ?? ""}`
    : res.status === "in_progress" ? "처리 중입니다."
    : `처리했습니다 (${res.status}).`;
  return base + (res.duplicate ? " (이미 처리된 요청)" : "") + (res.peer_error ? " 상대 에이전트에 알리지 못했습니다." : "");
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.search));
  const [pane, setPane] = useState<Pane>(() => (parseRoute(window.location.search) as { panel?: string }).panel ? "side" : "main");
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null);
  const [room, setRoom] = useState<RoomDetail | null>(null);
  const [roomError, setRoomError] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatMessage[] | null>(null);
  const [chatUnread, setChatUnread] = useState(false);
  const [sending, setSending] = useState(false);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({});
  const [notices, setNotices] = useState<Notice[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [stream, setStream] = useState<StreamState>("connecting");
  const [announce, setAnnounce] = useState("");
  const [notifyPerm, setNotifyPerm] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission);

  const api = me?.api ?? "";
  const roomId = route.view === "room" ? route.roomId : null;
  const roomIdRef = useRef(roomId);
  roomIdRef.current = roomId;
  const knownNotices = useRef<Set<string> | null>(null);
  const focusAfterNav = useRef(false);

  const say = useCallback((text: string) => {
    setAnnounce("");
    window.setTimeout(() => setAnnounce(text), 30); // re-announce the same text
  }, []);

  // ------------------------------------------------------------ navigation
  const navigate = useCallback((href: string, opts: { replace?: boolean; focus?: boolean } = {}) => {
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin) return;
    const next = url.pathname + url.search;
    if (opts.replace) window.history.replaceState(null, "", next);
    else if (next !== window.location.pathname + window.location.search) window.history.pushState(null, "", next);
    const r = parseRoute(url.search);
    focusAfterNav.current = opts.focus ?? true;
    setRoute(r);
    setPane("panel" in r && r.panel ? "side" : "main");
  }, []);

  useEffect(() => {
    const onPop = () => {
      focusAfterNav.current = true;
      setRoute(parseRoute(window.location.search));
    };
    window.addEventListener("popstate", onPop);
    const onSwMessage = (e: MessageEvent) => {
      if (e.data?.type === "navigate" && typeof e.data.url === "string") navigate(e.data.url);
    };
    navigator.serviceWorker?.addEventListener("message", onSwMessage);
    return () => {
      window.removeEventListener("popstate", onPop);
      navigator.serviceWorker?.removeEventListener("message", onSwMessage);
    };
  }, [navigate]);

  // ------------------------------------------------------------ loaders
  const loadRooms = useCallback(async () => {
    if (!api) return;
    try {
      setRooms(await request<RoomSummary[]>(`${api}/rooms`));
    } catch (e) {
      if (!(e instanceof AuthRequired)) say(`대화방 목록: ${message(e)}`);
    }
  }, [api, say]);

  const loadRoom = useCallback(async (id: string) => {
    if (!api) return;
    try {
      const r = await request<RoomDetail>(`${api}/rooms/${encodeURIComponent(id)}`);
      if (roomIdRef.current !== id) return;
      setRoom(r);
      setRoomError(null);
      const top = r.lines.length ? r.lines[r.lines.length - 1].id : 0;
      if (top > r.last_read && document.visibilityState === "visible") {
        await request(`${api}/rooms/${encodeURIComponent(id)}/read`, { method: "POST", body: { upto: top } });
      }
    } catch (e) {
      if (roomIdRef.current !== id) return;
      setRoomError(e instanceof ApiError && e.status === 404 ? "이 대화방을 찾을 수 없습니다." : message(e));
    }
  }, [api]);

  const loadChat = useCallback(async () => {
    if (!api) return;
    try {
      setChat((await request<{ messages: ChatMessage[] }>(`${api}/chat`)).messages);
    } catch (e) {
      if (!(e instanceof AuthRequired)) say(`대화: ${message(e)}`);
    }
  }, [api, say]);

  const loadApprovals = useCallback(async () => {
    if (!api) return;
    try {
      setApprovals(await request<Approval[]>(`${api}/approvals?status=pending`));
    } catch (e) {
      if (!(e instanceof AuthRequired)) say(`승인 대기: ${message(e)}`);
    }
  }, [api, say]);

  const loadNotices = useCallback(async () => {
    if (!api) return;
    try {
      const r = await request<{ items: Notice[]; unread: number }>(`${api}/notifications`);
      setNotices(r.items);
      setUnread(r.unread);
      const known = knownNotices.current;
      const fresh = known ? r.items.filter((n) => !n.read_at && !known.has(n.id)) : [];
      knownNotices.current = new Set(r.items.map((n) => n.id));
      if (fresh.length) {
        say(`새 알림: ${fresh[0].title}`);
        void desktopNotify(fresh);
      }
    } catch (e) {
      if (!(e instanceof AuthRequired)) say(`알림함: ${message(e)}`);
    }
  }, [api, say]);

  // ------------------------------------------------------------ sign-in
  useEffect(() => {
    getMe().then(setMe).catch((e) => {
      if (e instanceof AuthRequired) {
        if (!e.loginUrl) setFatal("로그인 세션이 없습니다. 관리자에게 받은 로그인 링크를 여십시오.");
        return; // otherwise the browser is on its way to SSO
      }
      setFatal(message(e));
    });
  }, []);

  useEffect(() => {
    if (!api) return;
    void loadRooms();
    void loadApprovals();
    void loadNotices();
  }, [api, loadRooms, loadApprovals, loadNotices]);

  useEffect(() => {
    if (!api) return;
    if (roomId) {
      setRoom(null);
      setRoomError(null);
      void loadRoom(roomId);
    } else {
      setChatUnread(false);
      void loadChat();
    }
  }, [api, roomId, loadRoom, loadChat]);

  // ------------------------------------------------------------ real time
  const pending = useRef<Set<Topic | "all">>(new Set());
  const flushTimer = useRef<number | undefined>();
  const refs = useRef({ loadRooms, loadRoom, loadChat, loadApprovals, loadNotices });
  refs.current = { loadRooms, loadRoom, loadChat, loadApprovals, loadNotices };

  const schedule = useCallback((t: Topic | "all") => {
    pending.current.add(t);
    if (flushTimer.current) return;
    flushTimer.current = window.setTimeout(() => {
      flushTimer.current = undefined;
      const set = pending.current;
      pending.current = new Set();
      const all = set.has("all");
      const f = refs.current;
      if (all || set.has("room")) {
        void f.loadRooms();
        if (roomIdRef.current) void f.loadRoom(roomIdRef.current);
      }
      if (all || set.has("chat")) {
        if (!roomIdRef.current) void f.loadChat();
        else setChatUnread(true);
      }
      if (all || set.has("approval")) void f.loadApprovals();
      if (all || set.has("notification")) void f.loadNotices();
    }, 250);
  }, []);

  useEffect(() => {
    if (!me) return;
    return openStream(me.stream, {
      onChange: (c) => schedule(c.topic),
      onResync: () => schedule("all"),
      onState: setStream,
      checkSession: async () => {
        try {
          await getMe();
          return true;
        } catch (e) {
          if (e instanceof AuthRequired) goToLogin(e.loginUrl);
          return false;
        }
      },
    });
  }, [me, schedule]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && roomIdRef.current) void refs.current.loadRoom(roomIdRef.current);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // ------------------------------------------------------------ title, badge
  useEffect(() => {
    const roomUnread = (rooms ?? []).reduce((s, r) => s + r.unread, 0);
    const n = unread + (approvals?.length ?? 0);
    document.title = n > 0 ? `(${n}) Nixie` : "Nixie";
    const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
    if (n + roomUnread > 0) void nav.setAppBadge?.(n + roomUnread).catch(() => undefined);
    else void nav.clearAppBadge?.().catch(() => undefined);
  }, [unread, approvals, rooms]);

  // ------------------------------------------------------------ focus after navigation
  useEffect(() => {
    if (!focusAfterNav.current) return;
    focusAfterNav.current = false;
    const id = "panel" in route && route.panel ? (route.panel === "inbox" ? "inbox-h" : "approvals-h") : "main-h";
    if ("focus" in route && route.focus) return; // the approval card takes focus
    window.requestAnimationFrame(() => document.getElementById(id)?.focus());
  }, [route, room, chat]);

  // ------------------------------------------------------------ keyboard: Alt+1..4 jump between regions
  useEffect(() => {
    const regions = ["rooms-h", "main-h", "approvals-h", "inbox-h"];
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const i = ["1", "2", "3", "4"].indexOf(e.key);
      if (i < 0) return;
      e.preventDefault();
      setPane(i === 0 ? "rooms" : i === 1 ? "main" : "side");
      window.requestAnimationFrame(() => document.getElementById(regions[i])?.focus());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ------------------------------------------------------------ actions
  const send = useCallback(async (text: string): Promise<boolean> => {
    if (!api) return false;
    const temp: ChatMessage = { id: -Date.now(), ts: new Date().toISOString(), role: "owner", text, pending: true };
    setChat((c) => [...(c ?? []), temp]);
    setSending(true);
    try {
      await once("chat", () => request(`${api}/chat`, { method: "POST", body: { text } }));
      await loadChat();
      say("에이전트가 응답했습니다.");
      return true;
    } catch (e) {
      setChat((c) => (c ?? []).filter((m) => m.id !== temp.id));
      if (!(e instanceof AuthRequired)) say(`보내지 못했습니다: ${message(e)}`);
      return false;
    } finally {
      setSending(false);
    }
  }, [api, loadChat, say]);

  const decide = useCallback((a: Approval, approve: boolean) => {
    const key = `approval:${a.id}`;
    if (!api || isBusy(key)) return; // a second press while the first runs does nothing
    setDecisions((d) => ({ ...d, [a.id]: { busy: true } }));
    once(key, () => request<{ status: string; error?: string; duplicate?: boolean }>(
      `${api}/approvals/${encodeURIComponent(a.id)}/${approve ? "approve" : "decline"}`, { method: "POST" }))
      .then((res) => {
        const text = decisionText(res);
        setDecisions((d) => ({ ...d, [a.id]: { busy: false, result: text } }));
        say(`${a.title}: ${text}`);
        window.setTimeout(() => void loadApprovals(), 2500);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 409) {
          setDecisions((d) => ({ ...d, [a.id]: { busy: false, result: "이미 다른 결정으로 처리된 요청입니다." } }));
          window.setTimeout(() => void loadApprovals(), 2500);
          return;
        }
        setDecisions((d) => ({ ...d, [a.id]: { busy: false, error: message(e) } }));
      });
  }, [api, loadApprovals, say]);

  const markRead = useCallback(async (n: Notice) => {
    if (!api || n.read_at) return;
    setNotices((list) => (list ?? []).map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
    setUnread((u) => Math.max(0, u - 1));
    try {
      await request(`${api}/notifications/${encodeURIComponent(n.id)}/read`, { method: "POST" });
    } catch {
      void loadNotices();
    }
  }, [api, loadNotices]);

  const readAll = useCallback(async () => {
    if (!api) return;
    try {
      await request(`${api}/notifications/read-all`, { method: "POST" });
      say("모든 알림을 읽음으로 표시했습니다.");
    } finally {
      void loadNotices();
    }
  }, [api, loadNotices, say]);

  const openNotice = useCallback((n: Notice) => {
    void markRead(n);
    if (n.ref_type === "approval" && n.ref_id) navigate(hrefFor({ approval: n.ref_id }));
    else if (n.ref_type === "room" && n.ref_id) navigate(hrefFor({ room: n.ref_id }));
  }, [markRead, navigate]);

  const enableDesktop = useCallback(async () => {
    if (typeof Notification === "undefined") return;
    const p = await Notification.requestPermission();
    setNotifyPerm(p);
    say(p === "granted" ? "데스크톱 알림을 켰습니다." : "데스크톱 알림이 허용되지 않았습니다.");
  }, [say]);

  // ------------------------------------------------------------ render
  const approvalFocus = "focus" in route ? route.focus : undefined;
  const header = useMemo(() => (
    <header className="topbar">
      <h1 className="brand">Nixie</h1>
      <p className="who-am-i">{me ? `${me.name}님` : ""}</p>
      <p className={`live live--${stream}`} role="status">
        <span className="live-dot" aria-hidden="true" />{STREAM_LABEL[stream]}
      </p>
      <div className="top-actions">
        {notifyPerm === "default" && (
          <button type="button" className="btn btn--small" onClick={() => void enableDesktop()}>데스크톱 알림 켜기</button>
        )}
        <details className="shortcuts">
          <summary>단축키</summary>
          <ul>
            <li><kbd>Alt</kbd>+<kbd>1</kbd> 대화방 목록</li>
            <li><kbd>Alt</kbd>+<kbd>2</kbd> 대화 본문</li>
            <li><kbd>Alt</kbd>+<kbd>3</kbd> 승인 대기</li>
            <li><kbd>Alt</kbd>+<kbd>4</kbd> 알림함</li>
            <li><kbd>↑</kbd>/<kbd>↓</kbd> 대화방 이동, <kbd>Enter</kbd> 열기</li>
          </ul>
        </details>
        {me && <a className="btn btn--small btn--ghost" href={me.logout_url}>로그아웃</a>}
      </div>
    </header>
  ), [me, stream, notifyPerm, enableDesktop]);

  if (fatal) {
    return (
      <main className="fatal">
        <h1>Nixie</h1>
        <p role="alert">{fatal}</p>
      </main>
    );
  }
  if (!me) {
    return (
      <main className="fatal" aria-busy="true">
        <h1>Nixie</h1>
        <p role="status">로그인 정보를 확인하는 중…</p>
      </main>
    );
  }

  const pendingCount = approvals?.length ?? 0;
  return (
    <div className="app" data-pane={pane}>
      <a className="skip" href="#main-h" onClick={(e) => { e.preventDefault(); setPane("main"); document.getElementById("main-h")?.focus(); }}>
        본문으로 건너뛰기
      </a>
      <a className="skip" href="#approvals-h" onClick={(e) => { e.preventDefault(); setPane("side"); window.requestAnimationFrame(() => document.getElementById("approvals-h")?.focus()); }}>
        승인 대기로 건너뛰기
      </a>
      {header}
      <div className="switcher" role="group" aria-label="화면 선택">
        <button type="button" aria-pressed={pane === "rooms"} onClick={() => setPane("rooms")}>대화방</button>
        <button type="button" aria-pressed={pane === "main"} onClick={() => setPane("main")}>대화</button>
        <button type="button" aria-pressed={pane === "side"} onClick={() => setPane("side")}>
          승인·알림{pendingCount + unread > 0 && <span className="count">{pendingCount + unread}</span>}
        </button>
      </div>
      <div className="layout">
        <div className="pane pane--rooms">
          <RoomList rooms={rooms} me={me.user} current={roomId} chatUnread={chatUnread}
                    onOpen={(id) => navigate(id ? hrefFor({ room: id }) : hrefFor({}))} />
        </div>
        <main className="pane pane--main">
          {roomId ? (
            <RoomView room={room} error={roomError} me={me.user} onGoChat={() => navigate(hrefFor({}))} />
          ) : (
            <AgentChat name={me.name} messages={chat} sending={sending} onSend={send} />
          )}
        </main>
        <aside className="pane pane--side" aria-label="승인 대기와 알림함">
          <Approvals items={approvals} decisions={decisions} focusId={approvalFocus} onDecide={decide}
                     onOpenRoom={(id) => navigate(hrefFor({ room: id }))} />
          <Inbox items={notices} unread={unread} onOpen={openNotice} onRead={(n) => void markRead(n)}
                 onReadAll={() => void readAll()}
                 onInternalLink={(url, n) => { void markRead(n); navigate(url.pathname + url.search); }} />
        </aside>
      </div>
      <div className="sr-only" role="status" aria-live="polite">{announce}</div>
    </div>
  );
}

async function desktopNotify(items: Notice[]): Promise<void> {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible") return; // the page shows it already
  const reg = await navigator.serviceWorker?.getRegistration().catch(() => undefined);
  for (const n of items.slice(0, 3)) {
    const url = n.ref_type === "approval" && n.ref_id ? hrefFor({ approval: n.ref_id })
      : n.ref_type === "room" && n.ref_id ? hrefFor({ room: n.ref_id }) : hrefFor({ panel: "inbox" });
    const opts: NotificationOptions = { body: n.body, tag: `nixie-${n.id}`, icon: "/icons/icon-192.png", data: { url } };
    if (reg) await reg.showNotification(n.title, opts);
    else new Notification(n.title, opts).onclick = () => { window.focus(); window.location.assign(url); };
  }
}
