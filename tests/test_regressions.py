"""qa 지적으로 들어간 수정의 회귀 테스트. 공개 동작 (소켓 메시지 · HTTP) 으로만 검사한다."""

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from helpers import BANK, PASSWORD, host, host_cmd, login, play
from test_host_ws import arena, cmd_err


def test_logout_closes_only_sockets_of_that_token(client: TestClient) -> None:
    login(client)
    t1 = client.cookies.get("gb_host")
    with host(client) as a:
        a.recv("state")
        client.cookies.clear()
        login(client)
        t2 = client.cookies.get("gb_host")
        assert t1 != t2
        with host(client) as b:
            b.recv("state")
            client.cookies.clear()
            client.cookies.set("gb_host", t1)
            assert client.post("/host/logout", follow_redirects=False).status_code == 303
            with pytest.raises(WebSocketDisconnect) as exc:
                a.recv()
            assert exc.value.code == 4401
            host_cmd(b, type="pick", cat="CS")  # 다른 토큰의 소켓은 계속 동작한다


def test_player_survives_binary_deep_and_mistyped_messages(client: TestClient) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        p.ws.send_bytes(b"\xff\xfe")
        assert p.recv("error")
        p.ws.send_text("[" * 100000)
        assert p.recv("error")["code"] == "bad_request"
        p.send(type="join", team=[1])
        assert p.recv("error")
        p.send(type="join", team={})
        assert p.recv("error")
        p.send(type="join", team="t1")
        p.recv("joined")
        p.recv("state")
        h.recv("state")
        host_cmd(h, p, type="pick", cat="CS")
        p.send(type="submit", qid={}, text="x")
        assert p.recv("error")
        p.send(type="submit", qid=[1], text="x")
        assert p.recv("error")
        p.send(type="nope", team=[1])
        assert p.recv("error")["code"] == "bad_request"
        p.send(type="leave")  # 아직 정상 동작한다
        p.recv("lobby")


def test_host_survives_binary_deep_and_mistyped_messages(client: TestClient) -> None:
    with arena(client, 0) as (h, _):
        h.ws.send_bytes(b"\x00\x01")
        assert h.recv("error")["code"] == "bad_request"
        h.ws.send_text("[" * 100000)
        assert h.recv("error")["code"] == "bad_request"
        assert cmd_err(h, type="pick", cat=[1]) == "bad_value"
        assert cmd_err(h, type="pick", cat={}) == "bad_value"
        for t in ("open",):
            assert cmd_err(h, type=t, qid={}) == "bad_request"
            assert cmd_err(h, type=t, qid=[1]) == "bad_request"
        host_cmd(h, type="pick", cat="CS")  # 토글 계열은 열린 문제가 있어야 team 검사에 닿는다
        for t in ("toggle_hit", "release", "team_update"):
            assert cmd_err(h, type=t, team=[1]) == "bad_request", t
            assert cmd_err(h, type=t, team={}) == "bad_request", t
        host_cmd(h, type="reveal")  # 연결이 살아 있다


def test_timer_len_rejects_float(client: TestClient) -> None:
    with arena(client, 0) as (h, _):
        assert cmd_err(h, type="timer_len", sec=20.0) == "bad_value"
        assert cmd_err(h, type="timer_len", sec=30.0) == "bad_value"


def test_vanished_question_grading_does_not_count_after_restart(make_settings, tmp_path) -> None:
    with TestClient(create_app(make_settings())) as c1:
        login(c1)
        token = c1.cookies.get("gb_host")
        with host(c1) as h:
            h.recv("state")
            host_cmd(h, type="pick", cat="CS")
            st = host_cmd(h, type="toggle_hit", team="t1")
            assert st["teams"][0]["score"] == 10
    smaller = {"categories": BANK["categories"], "questions": [q for q in BANK["questions"] if q["cat"] != "CS"]}
    path = tmp_path / "smaller.json"
    path.write_text(json.dumps(smaller, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(make_settings(questions_path=path))) as c2:
        c2.cookies.set("gb_host", token)
        with host(c2) as h:
            st = h.recv("state")
        assert st["teams"][0]["score"] == 0 and st["progress"]["used"] == 0


def test_from_env_rejects_unknown_goldenbell_key_without_leaking_password() -> None:
    secret = "s3cret-value-xyz"
    env = {"GOLDENBELL_HOST_PASSWORD": secret, "GOLDENBELL_PORT": "9000", "OTHER": "ok"}
    with pytest.raises(ValueError) as exc:
        Settings.from_env(env)
    assert "GOLDENBELL_PORT" in str(exc.value) and secret not in str(exc.value)
    assert secret not in repr(Settings(host_password=secret))
    ok = Settings.from_env({"GOLDENBELL_HOST_PASSWORD": secret, "UNRELATED": "1"})
    assert ok.host_password == secret
