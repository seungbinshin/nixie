"""Mock in-house services for tests: REST APIs + a groupware portal that has no API for some screens."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

CALLS: list[tuple[str, str]] = []


def create_mock() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def record(request: Request, call_next):
        CALLS.append((request.method, request.url.path))
        return await call_next(request)

    # ---- REST APIs
    @app.get("/v1/rooms/available")
    async def rooms(date: str, start: str, end: str, size: str):
        return {"rooms": [{"id": "M-301", "name": "M동 301", "capacity": 8}], "date": date}

    @app.post("/v1/reservations")
    async def reserve(body: dict):
        return {"reservation_id": "R-1", **body}

    @app.post("/v1/leave-requests")
    async def leave(body: dict):
        return {"request_id": "LV-API-1", "status": "submitted", **body}

    @app.get("/v1/search")
    async def search(q: str, limit: str = "5"):
        return {"hits": [{"title": "출장 규정 v3", "snippet": "국내 출장비는 ... 기준으로 정산한다."}], "q": q}

    @app.get("/v1/approvals")
    async def approvals(status: str):
        return {"items": [{"id": "A-9", "title": "장비 구매 품의"}]}

    # ---- Portal pages (browser route). Session cookie simulates SSO.
    def logged_in(request: Request) -> bool:
        return request.cookies.get("sso") == "ok"

    @app.get("/sso", response_class=HTMLResponse)
    async def sso_page():
        return '<html><body><button id="sso" onclick="location.href=\'/sso/callback\'">SSO</button></body></html>'

    @app.get("/sso/callback")
    async def sso_cb():
        r = RedirectResponse("/hr/leave", status_code=302)
        r.set_cookie("sso", "ok", httponly=True)
        return r

    @app.get("/hr/leave", response_class=HTMLResponse)
    async def leave_home(request: Request):
        bal = "잔여 12일" if logged_in(request) else "로그인 필요"
        return f'<html><body><div id="balance">{bal}</div></body></html>'

    @app.get("/hr/leave/new", response_class=HTMLResponse)
    async def leave_form(request: Request):
        if not logged_in(request):
            return '<html><body><div id="result">로그인 필요</div></body></html>'
        return """<html><body><form method="post" action="/hr/leave">
          <select id="kind" name="kind"><option value="annual">연차</option><option value="half_am">오전반차</option></select>
          <input id="start" name="start"><input id="end" name="end"><input id="reason" name="reason">
          <button id="submit" type="submit">신청</button></form></body></html>"""

    @app.post("/hr/leave", response_class=HTMLResponse)
    async def leave_submit(request: Request, kind: str = Form(), start: str = Form(), end: str = Form(),
                           reason: str = Form()):
        if not logged_in(request):
            return JSONResponse({"error": "login"}, status_code=401)
        return f'<html><body><div id="result">접수 완료 LV-WEB-7 ({kind} {start}~{end})</div></body></html>'

    return app
