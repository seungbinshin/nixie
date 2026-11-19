"""Web UI end to end: real gateway + 5 agent sandboxes + the static build in web/dist + Chromium.

The browser context adds the two headers that the SSO proxy (nginx + oauth2-proxy) adds in production.
Skipped when web/dist is not built or Chromium is not available.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from nixie.gateway import GatewaySettings, create_gateway
from nixie.pilot import provision
from nixie.sandbox import ProcessBackend, SandboxManager
from util import ThreadServer, free_port, get, post, wait_until

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"
CHROMIUM = os.environ.get("NIXIE_CHROME_PATH") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists()
                                                  else shutil.which("chromium") or shutil.which("google-chrome"))
PROXY_SECRET = "ui-proxy-secret"

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists() or not CHROMIUM,
                                reason="web/dist not built or no Chromium")


def sso(user: str) -> dict[str, str]:
    return {"X-Auth-Request-User": user, "X-Nixie-Proxy-Secret": PROXY_SECRET, "X-Nixie-Request": "1"}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    port = free_port()
    gw = f"http://127.0.0.1:{port}"
    plan = yaml.safe_load((ROOT / "examples" / "pilot" / "plan.yaml").read_text(encoding="utf-8"))
    plan.pop("defaults", None)
    (root / "plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    provision(root / "plan.yaml", root / "users", gw)
    mgr = SandboxManager(ProcessBackend(), root / "users", idle_seconds=60)
    st = GatewaySettings(auth_mode="sso", proxy_secret=PROXY_SECRET, web_root=DIST, cookie_secure=False,
                         stream_poll=0.3, admin_token="adm")
    with ThreadServer(create_gateway(mgr, reap_interval=1, settings=st), port=port):
        yield gw, root


@pytest.fixture(scope="module")
def pw():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    b = pw.chromium.launch(executable_path=CHROMIUM)
    yield b
    b.close()


def open_as(browser, gw: str, user: str, path: str = "/", **ctx):
    context = browser.new_context(extra_http_headers={"X-Auth-Request-User": user, "X-Nixie-Proxy-Secret": PROXY_SECRET},
                                  locale="ko-KR", timezone_id="Asia/Seoul", **ctx)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(gw + path)
    page.get_by_text("실시간 연결됨").wait_for(timeout=20000)
    return context, page, errors


def test_realtime_rooms_keyboard_approval_once(stack, browser):
    gw, _ = stack
    ctx, page, errors = open_as(browser, gw, "user2")
    try:
        page.get_by_role("heading", name="대화방").wait_for()
        rooms = page.locator(".rooms a[data-nav]")
        wait_until(lambda: rooms.count() == 4, timeout=20)  # my agent, work wiki, 2 rooms of 2 participants
        assert "사용자2 · 사용자1" in rooms.nth(2).inner_text()

        # user1's agent asks user2's agent; user2's open page updates without reload
        r = post(f"{gw}/u/user1/owner/schedule", headers=sso("user1"),
                 json={"peer": "user2", "title": "주간 회의", "duration_min": 60,
                       "window_start": "2026-11-19T13:00", "window_end": "2026-11-19T18:00"})
        assert r.status_code == 200 and r.json()["status"] == "pending_owner_approval", r.text
        card = page.locator("article.card").first
        card.wait_for(timeout=15000)
        assert "11/19 13:00–14:00 KST" in card.inner_text()
        page.locator(".rooms .badge").first.wait_for(timeout=15000)  # unread mark on the room
        page.locator(".notice--unread").first.wait_for(timeout=15000)
        assert rooms.nth(2).inner_text().find("사용자1") >= 0  # most recent room first

        # keyboard only: Alt+3 -> approvals, Tab to "승인", press Enter twice fast
        page.keyboard.press("Alt+3")
        assert page.evaluate("document.activeElement.id") == "approvals-h"
        for _ in range(8):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.textContent") == "승인":
                break
        assert page.evaluate("document.activeElement.textContent") == "승인"
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.get_by_text("승인했습니다.").first.wait_for(timeout=20000)
        ev = get(f"{gw}/u/user1/owner/events", headers=sso("user1")).json()
        assert len(ev) == 1 and ev[0]["start"] == "2026-11-19T04:00:00Z"
        lines = get(f"{gw}/u/user1/owner/rooms/user1~user2", headers=sso("user1")).json()["lines"]
        assert [l["intent"] for l in lines].count("meeting.confirm") == 1

        # open the room: read-only, KST text, unread mark goes away
        page.locator(".rooms a[data-nav]", has_text="사용자1").first.click()
        page.locator(".conv-meta .tag", has_text="읽기 전용").wait_for()
        assert page.locator("main textarea").count() == 0
        assert "KST" in page.locator(".messages").inner_text()
        wait_until(lambda: page.locator(".rooms .badge").count() == 0, timeout=15)
        assert "room=user1~user2" in page.url
        assert errors == [], errors
    finally:
        ctx.close()


def test_chat_inbox_links_deeplink_and_mobile(stack, browser):
    gw, root = stack
    tok = [l for l in (root / "users" / "user3" / "secrets.env").read_text().splitlines()
           if l.startswith("INGEST_TOKEN=")][0].split("=", 1)[1]
    post(f"{gw}/u/user3/ingest/notifications", headers={"X-Ingest-Token": tok},
         json={"items": [{"title": "결재 요청: 장비 구매", "source": "approval", "external_id": "A-1",
                          "source_url": "https://approval.example/doc/A-1"}]})
    ctx, page, errors = open_as(browser, gw, "user3", "/?room=user2~user3")
    try:
        page.locator(".conv-meta .tag", has_text="읽기 전용").wait_for()  # deep link opens the room
        link = page.get_by_role("link", name="원문 열기 (새 탭, approval.example)")
        assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener noreferrer"

        page.locator(".rooms a[data-nav]").first.click()  # my agent
        box = page.get_by_label("하댕이에게 보낼 메시지")  # default agent: 하댕이
        box.fill("/help")
        box.press("Enter")
        page.get_by_text("Commands:").wait_for(timeout=20000)
        assert page.locator(".msg--mine").count() >= 1
        assert errors == [], errors
    finally:
        ctx.close()

    ctx, page, errors = open_as(browser, gw, "user3", viewport={"width": 390, "height": 844})
    try:
        assert page.get_by_role("group", name="화면 선택").is_visible()
        assert page.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="대화방").click()
        assert page.locator(".rooms").is_visible() and not page.locator("main").is_visible()
        page.get_by_role("button", name="승인·알림").click()
        assert page.get_by_role("heading", name="알림함").is_visible()
        assert errors == [], errors
    finally:
        ctx.close()


def test_character_theme_markdown_panels_concurrency_wiki(stack, browser):
    gw, _ = stack
    ctx, page, errors = open_as(browser, gw, "user5")
    try:
        # first visit: choose the agent character; the header shows its picture and name
        page.get_by_text("함께 일할 에이전트를 골라 주세요").wait_for(timeout=20000)
        page.locator(".onboard .char-option", has_text="하냥이").click()
        page.locator("#main-h", has_text="하냥이").wait_for(timeout=15000)
        assert page.get_by_role("img", name="하냥이 (고양이)").first.is_visible()

        # customize: name, color, font (keyboard-usable dialog)
        page.locator(".topbar").get_by_role("button", name="꾸미기").click()
        dlg = page.get_by_role("dialog", name="에이전트와 화면 꾸미기")
        dlg.get_by_label("에이전트 이름").fill("나비")
        dlg.get_by_label("민트").check()
        dlg.get_by_label("주아").check()
        dlg.get_by_role("button", name="저장").click()
        page.locator("#main-h", has_text="나비").wait_for(timeout=15000)
        assert page.evaluate("getComputedStyle(document.body).fontFamily").lstrip('"').startswith("Jua")
        prof = get(f"{gw}/auth/me", headers=sso("user5")).json()["profile"]
        assert (prof["agent_name"], prof["character"], prof["theme"]["accent"], prof["theme"]["font"]) == \
            ("나비", "hanyang", "#22b8a0", "jua")

        box = page.get_by_label("나비에게 보낼 메시지")
        # a slow request (wakes user1's agent) and, at the same time, more requests
        box.fill("/ask user1 점심 메뉴 추천해 줘")
        box.press("Enter")
        page.locator(".inflight").wait_for(timeout=5000)
        assert box.is_enabled()
        box.fill("/remember public 회의는 오전이 좋음")
        box.press("Enter")
        box.fill("/help")
        box.press("Enter")
        page.get_by_text("Commands:").wait_for(timeout=20000)
        page.locator(".inflight").wait_for(state="detached", timeout=30000)

        # markdown: the /wiki reply has bold text and a wiki link
        box.fill("/wiki")
        box.press("Enter")
        strong = page.locator(".md strong", has_text="최근 보관 문서")
        strong.wait_for(timeout=20000)
        link = page.locator(".md a[href^='/?wiki=']").first
        assert link.is_visible()

        # panels: minimize and maximize with the keyboard; Esc restores
        page.get_by_role("button", name="승인 대기 최소화").focus()
        page.keyboard.press("Enter")
        assert page.get_by_role("button", name="승인 대기 펼치기").get_attribute("aria-expanded") == "false"
        assert not page.locator("#approvals-body").is_visible()
        page.keyboard.press("Enter")
        assert page.locator("#approvals-body").is_visible()
        page.get_by_role("button", name="알림함 최대화").focus()
        page.keyboard.press("Enter")
        assert not page.locator("main").is_visible() and page.locator("#inbox-body").is_visible()
        page.keyboard.press("Escape")
        assert page.locator("main").is_visible()

        # work wiki: archived automatically, read-only, follow-up note becomes a new document
        link.click()
        page.locator(".tag--lock", has_text="보관됨 · 수정 불가").wait_for(timeout=15000)
        assert "회의는 오전이 좋음" in page.locator(".wiki-doc").inner_text()
        page.get_by_label("후속 메모 (원문은 바뀌지 않고, 메모가 새 문서로 보관됩니다)").fill("다음 주에 다시 확인")
        page.get_by_role("button", name="보관").click()
        page.locator("#main-h", has_text="후속 메모:").wait_for(timeout=15000)
        page.get_by_role("link", name="← 업무 위키").click()
        page.get_by_role("heading", name="업무 위키").wait_for()
        assert page.get_by_role("link", name="ZIP 내려받기").get_attribute("href").endswith("/archive/export.zip")
        page.locator(".wiki-item").nth(1).wait_for(timeout=15000)  # ask + remember + note (list is debounced)
        assert errors == [], errors
    finally:
        ctx.close()


def test_pwa_installable_and_sso_redirect(stack, browser, pw, tmp_path):
    gw, _ = stack
    # a normal (not incognito) profile, as on a Windows desktop with Edge or Chrome
    ctx = pw.chromium.launch_persistent_context(
        str(tmp_path / "profile"), executable_path=CHROMIUM,
        extra_http_headers={"X-Auth-Request-User": "user4", "X-Nixie-Proxy-Secret": PROXY_SECRET})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(gw + "/")
    page.get_by_text("실시간 연결됨").wait_for(timeout=20000)
    try:
        cdp = ctx.new_cdp_session(page)
        wait_until(lambda: page.evaluate("navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))"),
                   timeout=15)
        res = cdp.send("Page.getInstallabilityErrors")
        assert res["installabilityErrors"] == [], res
        manifest = cdp.send("Page.getAppManifest")
        assert not manifest.get("errors"), manifest
        assert errors == [], errors
    finally:
        ctx.close()
    # no SSO identity -> the page sends the browser to the SSO start URL with the deep link
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(gw + "/?room=user3~user4")
    page.wait_for_url("**/oauth2/start?rd=*", timeout=15000)  # (no oauth2-proxy in this test: 404 after that)
    assert page.url.endswith("/oauth2/start?rd=%2F%3Froom%3Duser3~user4"), page.url
    ctx.close()
