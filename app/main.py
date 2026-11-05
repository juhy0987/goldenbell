"""앱 팩토리. 모듈 수준에서 앱을 만들지 않는다 — 임포트만으로 설정 검사가 돌지 않게."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.types import Scope

from app.config import Settings
from app.db import Store
from app.game import Game, new_state
from app.questions import load_bank
from app.ws import HOST_COOKIE, Hub, add_ws_routes

APP_DIR = Path(__file__).parent
LOGIN_ERROR = "비밀번호가 올바르지 않습니다."


class AssetFiles(StaticFiles):
    """static/ 에서 css · js 만 내보낸다."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if not path.endswith((".css", ".js")):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    bank = load_bank(settings.questions_path)
    store = Store(settings.db_path)
    game = Game(bank, store.load_state() or new_state(), settings.now, settings.rng)
    store.save_state(game.state)
    hub = Hub(game, store)
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        hub.schedule_timer()  # 재시작 전에 돌던 타이머의 만료 방송을 다시 잡는다
        yield
        store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", AssetFiles(directory=APP_DIR / "static"), name="static")

    def is_host(request: Request) -> bool:
        token = request.cookies.get(HOST_COOKIE)
        return bool(token) and store.has_session(token)

    @app.get("/")
    async def play(request: Request) -> Response:
        return templates.TemplateResponse(request, "play.html")

    @app.get("/host")
    async def host(request: Request) -> Response:
        if is_host(request):
            return templates.TemplateResponse(request, "host.html")
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/host/login")
    async def login(request: Request, password: str = Form("")) -> Response:
        if not hmac.compare_digest(password.encode(), settings.host_password.encode()):
            return templates.TemplateResponse(request, "login.html", {"error": LOGIN_ERROR}, status_code=401)
        token = secrets.token_urlsafe(32)
        store.add_session(token)
        response = RedirectResponse("/host", status_code=303)
        response.set_cookie(HOST_COOKIE, token, httponly=True, samesite="lax")
        return response

    @app.post("/host/logout")
    async def logout(request: Request) -> Response:
        token = request.cookies.get(HOST_COOKIE)
        if token:
            store.delete_session(token)
            await hub.close_hosts(token)  # 같은 토큰으로 열린 다른 탭의 진행자 소켓도 끊는다
        response = RedirectResponse("/host", status_code=303)
        response.delete_cookie(HOST_COOKIE)
        return response

    add_ws_routes(app, hub)
    return app
