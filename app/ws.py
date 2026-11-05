"""WebSocket 허브 — 팀 점유 · 방송 · 타이머 만료 예약.

전제: 단일 프로세스 · 단일 이벤트 루프. 상태 변경은 await 없이 끝나므로 핸들러 사이에 경쟁이 없다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app import views
from app.db import Store
from app.game import Game, GameError, str_field

log = logging.getLogger(__name__)

HOST_COOKIE = "gb_host"
AUTH_FAILED_CODE = 4401


class Hub:
    def __init__(self, game: Game, store: Store) -> None:
        self.game = game
        self.store = store
        self.players: dict[WebSocket, str | None] = {}  # 소켓 -> 점유한 팀 id
        self.owner: dict[str, WebSocket] = {}  # 팀 id -> 점유 소켓 (메모리만 — 연결 수명과 같다)
        self.hosts: set[WebSocket] = set()
        self.host_tokens: dict[WebSocket, str] = {}  # 진행자 소켓 -> 연결에 쓴 세션 토큰 (로그아웃 때 닫을 대상)
        self._timer_task: asyncio.Task[None] | None = None

    async def send(self, ws: WebSocket, payload: dict[str, Any]) -> None:
        try:
            await ws.send_json(payload)
        except Exception as exc:  # noqa: BLE001 — 끊긴 소켓 하나 때문에 방송 전체가 멈추면 안 된다
            log.warning("websocket send failed: %r", exc)

    async def broadcast(self) -> None:
        # 소켓 목록만 스냅샷한다. 앞선 send 의 await 사이에 끊기거나 점유가 바뀔 수 있어 tid 는 보낼 때 다시 읽는다
        for ws in list(self.players):
            if ws not in self.players:
                continue
            tid = self.players[ws]
            payload = views.player_state(self.game, tid) if tid else views.lobby(self.game, self.owner)
            await self.send(ws, payload)
        for ws in list(self.hosts):
            if ws in self.hosts:
                await self.send(ws, views.host_state(self.game, self.owner))

    async def close_hosts(self, token: str) -> None:
        """폐기된 토큰으로 열린 진행자 소켓을 모두 4401 로 닫는다."""
        closing = [ws for ws, t in self.host_tokens.items() if t == token]
        for ws in closing:
            self.hosts.discard(ws)
            del self.host_tokens[ws]  # 닫기 전에 빼서 이후 메시지 · 방송이 닿지 않게
        for ws in closing:
            try:
                await ws.close(code=AUTH_FAILED_CODE)
            except Exception as exc:  # noqa: BLE001 — 이미 끊긴 소켓이어도 나머지는 닫아야 한다
                log.warning("websocket close failed: %r", exc)

    def commit(self) -> None:
        self.store.save_state(self.game.state)
        self.schedule_timer()

    def release(self, tid: str) -> WebSocket | None:
        ws = self.owner.pop(tid, None)
        if ws is not None:
            self.players[ws] = None
        return ws

    async def kick(self, tids: list[str], reason: str) -> None:
        # 점유를 전부 푼 뒤에 보낸다 — send 의 await 사이에 다른 방송이 사라진 팀 id 를 보지 않게
        kicked = [ws for ws in map(self.release, tids) if ws is not None]
        for ws in kicked:
            await self.send(ws, {"type": "kicked", "reason": reason})

    def schedule_timer(self) -> None:
        """타이머가 돌고 있으면 만료 방송을 예약한다. 일시정지 · 공개 · 리셋 · 재시작마다 다시 잡는다."""
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None
        if self.game.state["timer"]["endAt"] is not None:
            self._timer_task = asyncio.get_running_loop().create_task(self._expire())

    async def _expire(self) -> None:
        while (end := self.game.state["timer"]["endAt"]) is not None:
            wait = (end - self.game.now_ms()) / 1000
            if wait <= 0:
                break
            await asyncio.sleep(wait)
        self._timer_task = None
        # 실패한 진행자 메시지가 먼저 settle 했을 수도 있으니 반환값과 무관하게 저장 · 방송한다
        self.game.settle_timer()
        self.store.save_state(self.game.state)
        await self.broadcast()


async def _receive(ws: WebSocket) -> dict[str, Any] | None:
    """메시지 하나를 읽는다. JSON 객체에 문자열 type 이 없으면 None."""
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
    raw = message.get("text")
    if raw is None:  # 바이너리 프레임
        return None
    try:
        msg = json.loads(raw)
    except (ValueError, RecursionError):  # 깨진 JSON · 지나치게 깊은 중첩
        return None
    return msg if isinstance(msg, dict) and isinstance(msg.get("type"), str) else None


async def _handle_player(hub: Hub, ws: WebSocket, msg: dict[str, Any]) -> None:
    game, tid = hub.game, hub.players[ws]
    kind = msg["type"]
    if kind == "join":
        if tid:
            raise GameError("already_joined")
        team = game.team(str_field(msg, "team"))["id"]
        if team in hub.owner:
            raise GameError("team_taken")
        hub.owner[team] = ws
        hub.players[ws] = team
        await hub.send(ws, {"type": "joined", "team": team})
    elif kind == "leave":
        if tid:
            hub.release(tid)
    elif kind == "submit":
        if not tid:
            raise GameError("not_joined")
        game.settle_timer()
        qid = str_field(msg, "qid")
        text = game.submit(tid, qid, msg.get("text"))
        hub.commit()
        await hub.send(ws, {"type": "submitted", "qid": qid, "text": text})
    else:
        raise GameError("bad_request")
    await hub.broadcast()


async def _handle_host(hub: Hub, ws: WebSocket, msg: dict[str, Any]) -> None:
    if ws not in hub.hosts:
        # 로그아웃으로 이미 닫은 소켓 — 남아 있던 메시지는 실행하지 않고 수신 루프를 끝낸다
        raise WebSocketDisconnect(AUTH_FAILED_CODE)
    game = hub.game
    game.settle_timer()
    if msg["type"] == "release":
        await hub.kick([game.team(str_field(msg, "team"))["id"]], "released")
    else:
        game.apply_host(msg)
        hub.commit()
        # 팀 수를 줄여 사라진 팀의 점유 소켓을 내보낸다
        alive = {t["id"] for t in game.state["teams"]}
        await hub.kick([tid for tid in hub.owner if tid not in alive], "team_removed")
    await hub.broadcast()


async def _serve(ws: WebSocket, handle: Any) -> None:
    while True:
        msg = await _receive(ws)
        try:
            if msg is None:
                raise GameError("bad_request")
            await handle(msg)
        except GameError as err:
            await ws.send_json(err.payload())


def add_ws_routes(app: FastAPI, hub: Hub) -> None:
    @app.websocket("/ws/play")
    async def play(ws: WebSocket) -> None:
        await ws.accept()
        hub.players[ws] = None
        try:
            await ws.send_json(views.lobby(hub.game, hub.owner))
            await _serve(ws, lambda msg: _handle_player(hub, ws, msg))
        except WebSocketDisconnect:
            pass  # 정상 종료 — 아래에서 점유를 푼다
        finally:
            tid = hub.players.pop(ws, None)
            if tid and hub.owner.get(tid) is ws:
                del hub.owner[tid]
                await hub.broadcast()

    @app.websocket("/ws/host")
    async def host(ws: WebSocket) -> None:
        token = ws.cookies.get(HOST_COOKIE)
        await ws.accept()
        # 브라우저가 닫힘 코드를 볼 수 있게 accept 뒤에 닫는다 (accept 전 거절은 1006 으로만 보인다)
        if not token or not hub.store.has_session(token):
            await ws.close(code=AUTH_FAILED_CODE)
            return
        hub.hosts.add(ws)
        hub.host_tokens[ws] = token
        try:
            await ws.send_json(views.host_state(hub.game, hub.owner))
            await _serve(ws, lambda msg: _handle_host(hub, ws, msg))
        except WebSocketDisconnect:
            pass  # 정상 종료
        finally:
            hub.hosts.discard(ws)
            hub.host_tokens.pop(ws, None)
