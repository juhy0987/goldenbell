import json
import random
from contextlib import ExitStack, contextmanager

from starlette.testclient import TestClient

from app.main import create_app
from helpers import QUESTIONS, Sock, host, host_cmd, login, play


@contextmanager
def arena(client: TestClient, n_players: int = 1):
    """진행자 소켓 하나와, 팀 t1..tN 에 들어간 참여자 N 명. 모두 초기 메시지를 읽은 상태."""
    login(client)
    with ExitStack() as stack:
        h = stack.enter_context(host(client))
        h.recv("state")
        players: list[Sock] = []
        for i in range(n_players):
            p = stack.enter_context(play(client))
            p.recv("lobby")
            p.send(type="join", team=f"t{i + 1}")
            p.recv("joined")
            p.recv("state")
            h.recv("state")
            for earlier in players:
                earlier.recv("state")
            players.append(p)
        yield h, players


def cmd_err(h: Sock, **msg) -> str:
    h.send(**msg)
    return h.recv("error")["code"]


def last(p: Sock) -> dict:
    return json.loads(p.log[-1])


def test_host_first_message_is_state_with_fixed_category_order(client: TestClient) -> None:
    login(client)
    with host(client) as h:
        st = h.recv("state")
    assert [c["key"] for c in st["categories"]] == ["시사", "영화", "밈", "넌센스", "CS"]
    assert st["categories"][4] == {"key": "CS", "label": "백엔드 CS", "total": 2, "left": 2, "used": [False, False]}
    assert st["current"] is None and st["history"] == [] and st["submissions"] == {} and st["correct"] == []
    assert st["timerLen"] == 20 and st["points"] == 10  # 기본값 (구현 선택)
    assert [t["id"] for t in st["teams"]] == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert all(t["connected"] is False for t in st["teams"])


def test_pick_opens_question_and_exhausts_category(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        st = host_cmd(h, p, type="pick", cat="CS")
        cur = st["current"]
        q = QUESTIONS[cur["qid"]]
        assert cur["cat"] == "CS" and cur["catLabel"] == "백엔드 CS" and cur["no"] == 1 and cur["revealed"] is False
        assert cur["text"] == q["q"] and cur["answer"] == q["a"] and cur["note"] == q["note"]
        assert st["history"] == [{"qid": cur["qid"], "cat": "CS", "no": 1}]
        assert st["categories"][4]["left"] == 1 and sorted(st["categories"][4]["used"]) == [False, True]
        assert st["progress"] == {"used": 1, "total": 7}
        st2 = host_cmd(h, p, type="pick", cat="CS")
        assert st2["current"]["qid"] != cur["qid"] and st2["current"]["no"] == 2
        assert cmd_err(h, type="pick", cat="CS") == "category_empty"
        assert cmd_err(h, type="pick", cat="없는분야") == "bad_value"  # 구현 선택 코드
        assert cmd_err(h, type="pick") == "bad_value"


def test_pick_is_deterministic_with_seeded_rng(make_settings, tmp_path) -> None:
    picks = []
    for i in range(2):
        settings = make_settings(db_path=tmp_path / f"d{i}.db", rng=random.Random(3))
        with TestClient(create_app(settings)) as c, arena(c, 0) as (h, _):
            picks.append(host_cmd(h, type="pick", cat="시사")["current"]["qid"])
    assert picks[0] == picks[1]


def test_open_and_back(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        assert cmd_err(h, type="open", qid="s1") == "not_used"
        assert cmd_err(h, type="open", qid="nope") == "not_used"
        st = host_cmd(h, p, type="back")
        assert st["current"] is None and st["submissions"] == {}
        assert last(p)["question"] is None
        st = host_cmd(h, p, type="open", qid=qid)
        assert st["current"]["qid"] == qid and st["current"]["no"] == 1  # 다시 열어도 출제 순서 번호 유지


def test_reopen_keeps_grading_and_submission_but_resets_reveal_and_timer(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        p.send(type="submit", qid=qid, text="mine")
        p.recv("submitted")
        p.recv("state")
        h.recv("state")
        host_cmd(h, p, type="toggle_hit", team="t1")
        host_cmd(h, p, type="timer_toggle")
        host_cmd(h, p, type="reveal")
        host_cmd(h, p, type="back")
        st = host_cmd(h, p, type="open", qid=qid)
        assert st["current"]["revealed"] is False
        assert st["timer"] == {"running": False, "remainMs": None, "lenSec": 20}
        assert st["correct"] == ["t1"] and st["submissions"]["t1"]["text"] == "mine"


def test_reveal_toggle_stops_timer_and_gates_answer_for_players(client: TestClient, clock) -> None:
    with arena(client) as (h, (p,)):
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        q = QUESTIONS[qid]
        host_cmd(h, p, type="timer_toggle")
        clock.advance(5)
        st = host_cmd(h, p, type="reveal")
        assert st["current"]["revealed"] is True
        assert st["timer"] == {"running": False, "remainMs": 15000, "lenSec": 20}  # 공개 시 남은 시간으로 멈춘다
        question = last(p)["question"]
        assert question["answer"] == q["a"] and question["note"] == q["note"] and question["hit"] is False
        assert last(p)["locked"] is True
        st = host_cmd(h, p, type="reveal")
        assert st["current"]["revealed"] is False
        assert not {"answer", "note", "hit"} & last(p)["question"].keys()
        assert q["a"] not in p.log[-1] and q["note"] not in p.log[-1]


def test_no_current_errors(client: TestClient) -> None:
    with arena(client) as (h, _):
        for t in ("reveal", "timer_toggle", "auto_grade"):
            assert cmd_err(h, type=t) == "no_current"
        assert cmd_err(h, type="toggle_hit", team="t1") == "no_current"


def test_timer_toggle_start_pause_resume_restart(client: TestClient, clock) -> None:
    with arena(client) as (h, (p,)):
        host_cmd(h, p, type="pick", cat="CS")
        t = host_cmd(h, p, type="timer_toggle")["timer"]
        assert t == {"running": True, "remainMs": 20000, "lenSec": 20}
        clock.advance(5)
        t = host_cmd(h, p, type="timer_toggle")["timer"]
        assert t == {"running": False, "remainMs": 15000, "lenSec": 20}
        clock.advance(100)  # 멈춰 있는 동안은 줄지 않는다
        t = host_cmd(h, p, type="timer_toggle")["timer"]
        assert t == {"running": True, "remainMs": 15000, "lenSec": 20}
        clock.advance(16)  # 종료 시각 경과
        st = host_cmd(h, p, type="reveal")  # 다음 진행자 메시지가 0 으로 확정한다
        assert st["timer"] == {"running": False, "remainMs": 0, "lenSec": 20}
        host_cmd(h, p, type="reveal")  # 가리기
        t = host_cmd(h, p, type="timer_toggle")["timer"]  # 남은 시간 0 이면 lenSec 로 새로 시작
        assert t == {"running": True, "remainMs": 20000, "lenSec": 20}


def test_timer_len_choices(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        host_cmd(h, p, type="pick", cat="CS")
        host_cmd(h, p, type="timer_toggle")
        for sec in (10, 30, 60, 90, 20):
            st = host_cmd(h, p, type="timer_len", sec=sec)
            assert st["timerLen"] == sec and st["timer"] == {"running": False, "remainMs": None, "lenSec": sec}
        for bad in (0, 15, 25, 120, -20, "30", None, True, 20.5):
            assert cmd_err(h, type="timer_len", sec=bad) == "bad_value", bad
        assert cmd_err(h, type="timer_len") == "bad_value"


def test_toggle_hit_scores_and_shared_rank(client: TestClient) -> None:
    with arena(client, 2) as (h, (p1, p2)):
        host_cmd(h, p1, p2, type="pick", cat="CS")
        st = host_cmd(h, p1, p2, type="toggle_hit", team="t1")
        assert st["correct"] == ["t1"]
        score = {t["id"]: (t["score"], t["rank"]) for t in st["teams"]}
        assert score["t1"] == (10, 1) and score["t2"] == (0, 2) and score["t6"] == (0, 2)
        board = [(r["id"], r["rank"]) for r in last(p1)["scoreboard"]]  # 순위순, 동점은 같은 순위
        assert board[0] == ("t1", 1) and {r for _, r in board[1:]} == {2}
        st = host_cmd(h, p1, p2, type="toggle_hit", team="t1")
        assert st["correct"] == [] and st["teams"][0]["score"] == 0
        assert cmd_err(h, type="toggle_hit", team="t99") == "no_such_team"


def test_score_is_adj_plus_hits_times_points_and_ranks_1_1_3(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        host_cmd(h, p, type="settings", points=25)
        host_cmd(h, p, type="pick", cat="CS")
        host_cmd(h, p, type="toggle_hit", team="t1")
        host_cmd(h, p, type="toggle_hit", team="t2")
        host_cmd(h, p, type="pick", cat="CS")
        host_cmd(h, p, type="toggle_hit", team="t1")  # t1 2문제 = 50
        host_cmd(h, p, type="team_update", team="t1", adj=-5)  # 45
        host_cmd(h, p, type="team_update", team="t2", adj=20)  # 25 + 20 = 45
        st = host_cmd(h, p, type="team_update", team="t3", adj=7)
        by = {t["id"]: (t["score"], t["rank"]) for t in st["teams"]}
        assert by["t1"] == (45, 1) and by["t2"] == (45, 1) and by["t3"] == (7, 3) and by["t4"] == (0, 4)


def test_auto_grade_marks_only_matching_submissions(client: TestClient) -> None:
    with arena(client, 3) as (h, ps):
        qid = host_cmd(h, *ps, type="pick", cat="시사")["current"]["qid"]
        answer = QUESTIONS[qid]["a"]
        texts = [" " + answer.upper().replace(" ", "") + "! ", "totally wrong", answer.split("(")[0]]
        for p, text in zip(ps, texts):
            p.send(type="submit", qid=qid, text=text)
            p.recv("submitted")
            for q in ps:
                q.recv("state")
            h.recv("state")
        st = host_cmd(h, *ps, type="auto_grade")
        assert st["correct"] == ["t1", "t3"]
        assert {t: s["match"] for t, s in st["submissions"].items()} == {"t1": True, "t2": False, "t3": True}
        host_cmd(h, *ps, type="toggle_hit", team="t1")  # 수동 취소
        st = host_cmd(h, *ps, type="auto_grade")  # 일치하는 팀은 다시 들어가고, 이미 있는 팀은 중복되지 않는다
        assert sorted(st["correct"]) == ["t1", "t3"]


def test_settings_validation_and_points(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        bad_cases = [{"teamCount": 1}, {"teamCount": 21}, {"teamCount": "6"}, {"teamCount": True}, {"teamCount": 5.5},
                     {"points": 0}, {"points": 1001}, {"points": "10"}, {"points": None}]
        for bad in bad_cases:
            assert cmd_err(h, type="settings", **bad) == "bad_value", bad
        assert host_cmd(h, p, type="settings", points=1000)["points"] == 1000
        st = host_cmd(h, p, type="settings", teamCount=20, points=1)
        assert len(st["teams"]) == 20 and st["points"] == 1 and st["teams"][19]["id"] == "t20"
        st = host_cmd(h, p, type="settings", teamCount=2)
        assert [t["id"] for t in st["teams"]] == ["t1", "t2"]
        assert cmd_err(h, type="settings", teamCount=1, points=77) == "bad_value"
        assert host_cmd(h, p, type="settings")["points"] == 1  # 거절된 요청이 점수를 부분 적용하지 않는다


def test_shrinking_teams_kicks_owner_and_drops_their_grading(client: TestClient) -> None:
    with arena(client, 6) as (h, ps):
        qid = host_cmd(h, *ps, type="pick", cat="CS")["current"]["qid"]
        ps[5].send(type="submit", qid=qid, text="x")
        ps[5].recv("submitted")
        for p in ps:
            p.recv("state")
        h.recv("state")
        host_cmd(h, *ps, type="toggle_hit", team="t6")
        h.send(type="settings", teamCount=3)
        st = h.recv("state")
        assert [t["id"] for t in st["teams"]] == ["t1", "t2", "t3"]
        assert st["correct"] == [] and st["submissions"] == {}
        for p in ps[3:]:  # 빠진 팀의 소켓은 kicked 뒤 lobby
            assert p.recv() == {"type": "kicked", "reason": "team_removed"}
            assert [t["id"] for t in p.recv("lobby")["teams"]] == ["t1", "t2", "t3"]
        for p in ps[:3]:
            p.recv("state")
        h.send(type="settings", teamCount=6)  # 다시 늘려도 옛 점수가 되살아나지 않는다
        st = h.recv("state")
        assert st["teams"][5]["score"] == 0 and st["teams"][5]["connected"] is False


def test_team_update(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        st = host_cmd(h, p, type="team_update", team="t1", name="  Alpha ", player="Kim", adj=3)
        assert (st["teams"][0]["name"], st["teams"][0]["player"], st["teams"][0]["adj"], st["teams"][0]["score"]) == ("Alpha", "Kim", 3, 3)
        assert last(p)["team"] == {"id": "t1", "name": "Alpha", "player": "Kim"}
        assert host_cmd(h, p, type="team_update", team="t1", name="")["teams"][0]["name"] == "1팀"  # 빈 이름은 N팀
        assert host_cmd(h, p, type="team_update", team="t3", name="   ")["teams"][2]["name"] == "3팀"
        assert cmd_err(h, type="team_update", team="t1", name="가" * 21) == "too_long"  # 구현 선택 코드
        assert cmd_err(h, type="team_update", team="t1", player="가" * 21) == "too_long"
        host_cmd(h, p, type="team_update", team="t1", name="가" * 20)
        assert cmd_err(h, type="team_update", team="t1", adj="3") == "bad_value"
        assert cmd_err(h, type="team_update", team="t1", adj=True) == "bad_value"
        assert cmd_err(h, type="team_update", team="t99", name="x") == "no_such_team"
        assert cmd_err(h, type="team_update", team="t1", name=5) == "bad_value"


def test_release_kicks_socket_and_frees_team(client: TestClient) -> None:
    with arena(client, 2) as (h, (p1, p2)):
        h.send(type="release", team="t1")
        assert h.recv("state")["teams"][0]["connected"] is False
        assert p1.recv() == {"type": "kicked", "reason": "released"}
        lob = p1.recv("lobby")
        assert lob["teams"][0]["taken"] is False and lob["teams"][1]["taken"] is True
        p2.recv("state")
        p1.send(type="join", team="t1")  # 풀린 팀을 다시 고를 수 있다
        p1.recv("joined")
        p1.recv("state")
        h.recv("state")
        p2.recv("state")
        assert cmd_err(h, type="release", team="t99") == "no_such_team"


def test_reset_clears_progress_keeps_settings(client: TestClient) -> None:
    with arena(client) as (h, (p,)):
        host_cmd(h, p, type="settings", points=50)
        host_cmd(h, p, type="timer_len", sec=30)
        host_cmd(h, p, type="team_update", team="t1", name="Alpha", player="Kim", adj=9)
        qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        p.send(type="submit", qid=qid, text="a")
        p.recv("submitted")
        p.recv("state")
        h.recv("state")
        host_cmd(h, p, type="toggle_hit", team="t1")
        st = host_cmd(h, p, type="reset")
        assert st["current"] is None and st["history"] == [] and st["progress"]["used"] == 0
        assert st["correct"] == [] and st["submissions"] == {}
        assert all(c["left"] == c["total"] for c in st["categories"])
        t1 = st["teams"][0]
        assert (t1["name"], t1["player"], t1["adj"], t1["score"], t1["connected"]) == ("Alpha", "Kim", 0, 0, True)
        assert st["points"] == 50 and st["timerLen"] == 30 and st["timer"]["remainMs"] is None
        # 지워진 뒤 다시 출제해도 옛 답안 · 채점이 없다
        st = host_cmd(h, p, type="pick", cat="CS")
        assert st["submissions"] == {} and st["correct"] == []


def test_broadcast_reaches_every_host_and_player_socket(client: TestClient) -> None:
    login(client)
    with host(client) as h1, host(client) as h2, play(client) as joined, play(client) as lobby:
        h1.recv("state")
        h2.recv("state")
        joined.recv("lobby")
        lobby.recv("lobby")
        joined.send(type="join", team="t1")
        joined.recv("joined")
        joined.recv("state")
        h1.recv("state")
        h2.recv("state")
        assert lobby.recv("lobby")["teams"][0]["taken"] is True

        h1.send(type="pick", cat="CS")
        s1, s2 = h1.recv("state"), h2.recv("state")
        assert s1 == s2 and s1["current"] is not None
        assert joined.recv("state")["question"]["qid"] == s1["current"]["qid"]
        lobby.recv("lobby")  # 팀 없는 소켓은 문제 대신 로비를 받는다

        h2.send(type="timer_toggle")  # 다른 진행자 소켓의 조작도 전부에 간다
        assert h1.recv("state")["timer"]["running"] is True
        assert h2.recv("state")["timer"]["running"] is True
        assert joined.recv("state")["timer"]["running"] is True
        lobby.recv("lobby")


def test_unknown_host_message_is_bad_request_and_connection_survives(client: TestClient) -> None:
    with arena(client, 0) as (h, _):
        assert cmd_err(h, type="nope") == "bad_request"
        assert cmd_err(h, type="_open", qid="s1") == "bad_request"  # 내부 메서드 이름으로 우회 불가
        h.ws.send_text("garbage")
        assert h.recv("error")["code"] == "bad_request"
        host_cmd(h, type="pick", cat="CS")
