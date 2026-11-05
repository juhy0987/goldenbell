from starlette.testclient import TestClient

from helpers import host, host_cmd, join, login, play


def test_connect_sends_lobby_with_default_six_teams(client: TestClient) -> None:
    with play(client) as p:
        lobby = p.recv()
    assert lobby["type"] == "lobby"
    assert [t["id"] for t in lobby["teams"]] == ["t1", "t2", "t3", "t4", "t5", "t6"]  # 기본 팀 수 6 (구현 선택)
    assert all(t["taken"] is False for t in lobby["teams"])
    assert lobby["teams"][0] == {"id": "t1", "name": "1팀", "player": "", "taken": False}


def test_join_sequence_is_joined_then_state(client: TestClient) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        p.send(type="join", team="t2")
        assert p.recv() == {"type": "joined", "team": "t2"}
        st = p.recv("state")
        assert st["team"] == {"id": "t2", "name": "2팀", "player": ""}
        assert st["question"] is None and st["mySubmission"] is None and st["locked"] is False
        assert st["points"] == 10  # 기본 점수 (구현 선택)
        assert st["progress"] == {"used": 0, "total": 7}
        assert st["timer"] == {"running": False, "remainMs": None, "lenSec": 20}
        assert h.recv("state")["teams"][1]["connected"] is True


def test_join_errors_and_taken_lobby_broadcast(client: TestClient) -> None:
    with play(client) as a, play(client) as b:
        a.recv("lobby")
        b.recv("lobby")
        a.send(type="join", team="t1")
        a.recv("joined")
        a.recv("state")
        lob = b.recv("lobby")  # 점유 변화는 로비의 다른 소켓에도 간다
        assert lob["teams"][0]["taken"] is True

        b.send(type="join", team="t1")
        assert b.recv()["code"] == "team_taken"
        b.send(type="join", team="zz")
        assert b.recv()["code"] == "no_such_team"
        a.send(type="join", team="t3")
        assert a.recv()["code"] == "already_joined"
        a.send(type="submit", qid="s1", text="x")
        assert a.recv()["code"] == "not_open"  # 가입은 돼 있고 열린 문제가 없음
        b.send(type="submit", qid="s1", text="x")
        assert b.recv()["code"] == "not_joined"


def test_disconnect_frees_team(client: TestClient) -> None:
    with play(client) as b:
        b.recv("lobby")
        with play(client) as a:
            a.recv("lobby")
            a.send(type="join", team="t1")
            a.recv("joined")
            a.recv("state")
            b.recv("lobby")
        # a 가 닫히면 b (로비) 에게 해제된 로비가 온다
        assert b.recv("lobby")["teams"][0]["taken"] is False
        b.send(type="join", team="t1")
        b.recv("joined")


def test_leave_returns_to_lobby_and_frees_team(client: TestClient) -> None:
    with play(client) as a:
        a.recv("lobby")
        a.send(type="join", team="t1")
        a.recv("joined")
        a.recv("state")
        a.send(type="leave")
        lob = a.recv("lobby")
        assert lob["teams"][0]["taken"] is False
        a.send(type="join", team="t1")  # 다시 들어갈 수 있다
        a.recv("joined")


def test_bad_messages_keep_connection(client: TestClient) -> None:
    with play(client) as a:
        a.recv("lobby")
        a.ws.send_text("not json")
        assert a.recv()["code"] == "bad_request"
        a.send(type="nope")
        assert a.recv()["code"] == "bad_request"
        a.ws.send_text("[1]")
        assert a.recv()["code"] == "bad_request"
        a.send(type="join", team="t1")
        a.recv("joined")


def test_submit_flow_trim_overwrite_and_host_sees_it(client: TestClient, clock) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        join(p, h)
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        p.send(type="submit", qid=qid, text="  hello  ")
        assert p.recv() == {"type": "submitted", "qid": qid, "text": "hello"}
        st = p.recv("state")
        assert st["mySubmission"] == {"text": "hello", "at": int(clock.t * 1000)}  # at = epoch ms (구현 선택)
        assert h.recv("state")["submissions"]["t1"]["text"] == "hello"

        clock.advance(1)
        p.send(type="submit", qid=qid, text="second")
        p.recv("submitted")
        assert p.recv("state")["mySubmission"]["text"] == "second"
        assert h.recv("state")["submissions"]["t1"]["text"] == "second"  # 덮어쓰기


def test_submit_validation(client: TestClient) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        join(p, h)
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]

        def err(**kw):
            p.send(type="submit", **kw)
            return p.recv("error")["code"]

        assert err(qid=qid, text="") == "empty"
        assert err(qid=qid, text="   ") == "empty"
        assert err(qid=qid, text="가" * 41) == "too_long"  # 문자 수 기준 (바이트 아님)
        assert err(qid="other", text="x") == "not_open"
        assert err(qid=qid, text=5) == "bad_request"  # 구현 선택 코드
        p.send(type="submit", qid=qid, text="가" * 40)
        assert p.recv("submitted")["text"] == "가" * 40
        p.recv("state")
        h.recv("state")
        # 앞뒤 공백은 지운 뒤 길이를 센다
        p.send(type="submit", qid=qid, text=" " + "a" * 40 + " ")
        p.recv("submitted")


def test_submit_locked_after_reveal_and_after_time_up(client: TestClient, clock) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        join(p, h)
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]

        h.send(type="reveal")
        h.recv("state")
        assert p.recv("state")["locked"] is True
        p.send(type="submit", qid=qid, text="x")
        assert p.recv("error")["code"] == "locked"

        h.send(type="reveal")  # 가리기
        h.recv("state")
        assert p.recv("state")["locked"] is False
        host_cmd(h, p, type="timer_toggle")
        clock.advance(21)  # 20초 종료 — 태스크가 아니라 계산 경로로 잠긴다
        p.send(type="submit", qid=qid, text="late")
        assert p.recv("error")["code"] == "locked"
