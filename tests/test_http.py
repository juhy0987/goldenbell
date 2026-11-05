import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from helpers import PASSWORD, login

LOGIN_MARK = 'name="password"'


def test_login_success_sets_httponly_cookie_and_redirects(client: TestClient) -> None:
    r = client.post("/host/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/host"
    cookie = r.headers["set-cookie"].lower()
    assert "gb_host=" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_login_failure_is_401_with_login_page_and_no_cookie(client: TestClient) -> None:
    r = client.post("/host/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert LOGIN_MARK in r.text and "비밀번호" in r.text
    assert "set-cookie" not in r.headers


def test_login_missing_password_field_is_401(client: TestClient) -> None:
    assert client.post("/host/login", data={}, follow_redirects=False).status_code == 401


def test_tokens_are_random_per_login(client: TestClient, make_settings) -> None:
    login(client)
    first = client.cookies.get("gb_host")
    client.cookies.clear()
    login(client)
    assert first and client.cookies.get("gb_host") != first


def test_host_page_depends_on_cookie(client: TestClient) -> None:
    anon = client.get("/host")
    assert anon.status_code == 200 and LOGIN_MARK in anon.text
    login(client)
    authed = client.get("/host")
    assert authed.status_code == 200 and LOGIN_MARK not in authed.text


def test_forged_cookie_is_not_a_host(client: TestClient) -> None:
    client.cookies.set("gb_host", "forged-token")
    assert LOGIN_MARK in client.get("/host").text
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/host") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_host_ws_without_cookie_closes_4401(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/host") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_logout_invalidates_token(client: TestClient) -> None:
    login(client)
    token = client.cookies.get("gb_host")
    r = client.post("/host/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/host"
    # 쿠키를 되살려도 서버 쪽 토큰이 폐기되어 있어야 한다
    client.cookies.clear()
    client.cookies.set("gb_host", token)
    assert LOGIN_MARK in client.get("/host").text
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/host") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_play_page_is_public(client: TestClient) -> None:
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("pw", ["", "   "])
def test_empty_password_refuses_settings(pw: str) -> None:
    with pytest.raises(ValueError):
        Settings(host_password=pw)


def test_from_env_requires_password() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({})
    assert Settings.from_env({"GOLDENBELL_HOST_PASSWORD": "x"}).host_password == "x"
