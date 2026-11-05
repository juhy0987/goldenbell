"""테스트 공용 도구 — 소켓 래퍼와 고정 문제 은행."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from starlette.testclient import TestClient

PASSWORD = "pw-test"

# 정답 · 해설에 눈에 띄는 토큰을 넣어, 직렬화된 메시지에서 문자열 검색으로 유출을 잡는다
BANK: dict[str, Any] = {
    "categories": [
        {"key": "시사", "label": "시사", "color": "x"},
        {"key": "영화", "label": "영화", "color": "x"},
        {"key": "밈", "label": "밈", "color": "x"},
        {"key": "넌센스", "label": "넌센스", "color": "x"},
        {"key": "CS", "label": "백엔드 CS", "color": "x"},
    ],
    "questions": [
        {"id": "s1", "cat": "시사", "q": "QBODY-s1", "a": "Zebra Crossing(횡단보도)", "note": "NOTE-s1-secret"},
        {"id": "s2", "cat": "시사", "q": "QBODY-s2", "a": "Quokka2", "note": "NOTE-s2-secret"},
        {"id": "f1", "cat": "영화", "q": "QBODY-f1", "a": "Quokka3", "note": "NOTE-f1-secret"},
        {"id": "e1", "cat": "밈", "q": "QBODY-e1", "a": "Quokka4", "note": "NOTE-e1-secret"},
        {"id": "n1", "cat": "넌센스", "q": "QBODY-n1", "a": "Quokka5", "note": "NOTE-n1-secret"},
        {"id": "c1", "cat": "CS", "q": "QBODY-c1", "a": "Quokka6", "note": "NOTE-c1-secret"},
        {"id": "c2", "cat": "CS", "q": "QBODY-c2", "a": "Quokka7", "note": "NOTE-c2-secret"},
    ],
}
QUESTIONS = {q["id"]: q for q in BANK["questions"]}


class Clock:
    """초 단위 float 를 돌려주는 수동 시계."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += sec


class Sock:
    """JSON 소켓 래퍼. 받은 원문을 log 에 쌓아 유출 검사에 쓴다."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.log: list[str] = []

    def recv(self, kind: str | None = None) -> dict[str, Any]:
        raw = self.ws.receive_text()
        self.log.append(raw)
        msg = json.loads(raw)
        if kind is not None:
            assert msg["type"] == kind, msg
        return msg

    def send(self, **msg: Any) -> None:
        self.ws.send_text(json.dumps(msg, ensure_ascii=False))


@contextmanager
def play(client: TestClient):
    with client.websocket_connect("/ws/play") as ws:
        yield Sock(ws)


@contextmanager
def host(client: TestClient):
    with client.websocket_connect("/ws/host") as ws:
        yield Sock(ws)


def login(client: TestClient) -> None:
    r = client.post("/host/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303


def host_cmd(h: Sock, *others: Sock, **msg: Any) -> dict[str, Any]:
    """진행자 명령을 보내고 진행자 · others 가 방송 state 를 한 번씩 받는다. 진행자가 받은 state 를 돌려준다."""
    h.send(**msg)
    st = h.recv("state")
    for o in others:
        o.recv()
    return st


def join(p: Sock, h: Sock, team: str = "t1") -> dict[str, Any]:
    """로비의 p 가 팀에 들어가고 (joined, state) h 가 방송을 받는다. p 의 state 를 돌려준다."""
    p.send(type="join", team=team)
    p.recv("joined")
    st = p.recv("state")
    h.recv("state")
    return st
