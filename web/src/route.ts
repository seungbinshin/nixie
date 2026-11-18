// Routes live in the query string (not the hash), so the SSO proxy keeps them in the post-login redirect.
//   /?room=<room_id>     agent-to-agent room (read-only)
//   /                    chat with my agent
//   /?approval=<id>      approval card
//   /?panel=inbox|approvals
export type Route =
  | { view: "chat" }
  | { view: "room"; roomId: string }
  | { view: "chat"; panel: "inbox" | "approvals"; focus?: string };

export function parseRoute(search: string): Route {
  const q = new URLSearchParams(search);
  const room = q.get("room");
  if (room) return { view: "room", roomId: room };
  const approval = q.get("approval");
  if (approval) return { view: "chat", panel: "approvals", focus: approval };
  const panel = q.get("panel");
  if (panel === "inbox" || panel === "approvals") return { view: "chat", panel };
  return { view: "chat" };
}

export function hrefFor(r: { room?: string; approval?: string; panel?: string }): string {
  // encodeURIComponent keeps "~" readable in room ids (alice~bob)
  const q = (["room", "approval", "panel"] as const)
    .filter((k) => r[k])
    .map((k) => `${k}=${encodeURIComponent(r[k]!)}`)
    .join("&");
  return q ? `/?${q}` : "/";
}
