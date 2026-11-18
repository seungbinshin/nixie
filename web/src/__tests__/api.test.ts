import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthRequired, classifyLink, loginTarget, once, request } from "../api";
import { hrefFor, parseRoute } from "../route";

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("sends the CSRF header and same-origin cookies, never a token", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    await request("/u/a/owner/chat", { method: "POST", body: { text: "hi" } });
    const init = f.mock.calls[0][1]!;
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>)["X-Nixie-Request"]).toBe("1");
    expect(JSON.stringify(init.headers)).not.toMatch(/Owner-Token/i);
  });

  it("401 sends the browser to SSO and keeps the deep link", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "sso_required", login_url: "/oauth2/start" }), { status: 401 }));
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, pathname: "/", search: "?room=a~b" } as Location);
    await expect(request("/auth/me")).rejects.toBeInstanceOf(AuthRequired);
    expect(assign).toHaveBeenCalledWith("/oauth2/start?rd=%2F%3Froom%3Da~b");
  });

  it("builds the login target with rd", () => {
    expect(loginTarget("/oauth2/start", { pathname: "/", search: "?approval=x1" } as Location)).toBe("/oauth2/start?rd=%2F%3Fapproval%3Dx1");
  });

  it("runs one request per key at a time (double click on approve)", async () => {
    let calls = 0;
    let release!: () => void;
    const job = () => { calls++; return new Promise<void>((r) => { release = r; }); };
    const a = once("approval:1", job);
    const b = once("approval:1", job);
    expect(a).toBe(b);
    release();
    await a;
    expect(calls).toBe(1);
  });

  it("classifies links: only http(s); same origin stays in the tab", () => {
    expect(classifyLink("javascript:alert(1)", "https://nixie.x")).toBeNull();
    expect(classifyLink("data:text/html,x", "https://nixie.x")).toBeNull();
    expect(classifyLink("https://approval.corp/doc/1", "https://nixie.x")?.kind).toBe("external");
    expect(classifyLink("/?room=a~b", "https://nixie.x")?.kind).toBe("internal");
  });

  it("routes live in the query string", () => {
    expect(parseRoute("?room=alice~bob")).toEqual({ view: "room", roomId: "alice~bob" });
    expect(parseRoute("?approval=a1")).toEqual({ view: "chat", panel: "approvals", focus: "a1" });
    expect(parseRoute("")).toEqual({ view: "chat" });
    expect(hrefFor({ room: "a~b" })).toBe("/?room=a~b");
    expect(hrefFor({ approval: "x&y" })).toBe("/?approval=x%26y");
  });
});
