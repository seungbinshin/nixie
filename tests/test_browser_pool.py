"""Shared browser pool: lease per job, only the user's profile is loaded and saved back."""

from __future__ import annotations

from util import post

H = {"X-Pool-Token": "pool-secret"}


def run(pool_url, user, steps, headers=H):
    return post(f"{pool_url}/run", json={"user": user, "steps": steps}, headers=headers)


def test_profile_persists_per_user_and_is_isolated(browser_pool, mock_hynix, users_root):
    srv, pool = browser_pool
    portal = mock_hynix.url
    balance = [{"action": "goto", "url": f"{portal}/hr/leave"}, {"action": "text", "selector": "#balance"}]

    r = run(srv.url, "alice", balance)
    assert r.status_code == 200 and r.json()["results"][1]["text"] == "로그인 필요"

    # SSO once (sets a cookie in this lease's fresh context)
    r = run(srv.url, "alice", [{"action": "goto", "url": f"{portal}/sso"}, {"action": "click", "selector": "#sso"},
                               {"action": "wait_for", "selector": "#balance"}])
    assert r.status_code == 200, r.text

    # a NEW lease (new context) still sees the session: it came from alice's profile on disk
    r = run(srv.url, "alice", balance)
    assert r.json()["results"][1]["text"] == "잔여 12일"
    assert (users_root / "alice" / "browser" / "storage_state.json").is_file()

    # bob shares the worker but not alice's cookies
    r = run(srv.url, "bob", balance)
    assert r.json()["results"][1]["text"] == "로그인 필요"
    assert pool.status()["leases"] >= 4 and pool.status()["in_use"] == 0


def test_pool_guards(browser_pool):
    srv, _ = browser_pool
    assert run(srv.url, "alice", [{"action": "goto", "url": "https://example.com/"}]).status_code == 403
    assert run(srv.url, "../etc", [{"action": "title"}]).status_code == 400
    assert run(srv.url, "alice", [{"action": "eval", "value": "1"}]).status_code == 400
    assert run(srv.url, "alice", [{"action": "title"}], headers={"X-Pool-Token": "x"}).status_code == 401
