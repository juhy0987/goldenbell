import asyncio
import time

from starlette.testclient import TestClient

from app.main import create_app
from helpers import Clock, host, host_cmd, login, play


def test_state_and_host_session_survive_restart_but_team_ownership_does_not(make_settings, clock) -> None:
    with TestClient(create_app(make_settings())) as c1:
        login(c1)
        token = c1.cookies.get("gb_host")
        with host(c1) as h, play(c1) as p:
            h.recv("state")
            p.recv("lobby")
            p.send(type="join", team="t1")
            p.recv("joined")
            p.recv("state")
            h.recv("state")
            host_cmd(h, p, type="settings", points=30)
            host_cmd(h, p, type="timer_len", sec=60)
            host_cmd(h, p, type="team_update", team="t2", name="Beta", adj=4)
            qid = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
            p.send(type="submit", qid=qid, text="persisted")
            p.recv("submitted")
            p.recv("state")
            h.recv("state")
            host_cmd(h, p, type="toggle_hit", team="t1")

    with TestClient(create_app(make_settings())) as c2:
        c2.cookies.set("gb_host", token)  # 진행자 세션은 DB 에 있어 새 앱에서도 유효하다
        with host(c2) as h, play(c2) as p:
            st = h.recv("state")
            assert st["points"] == 30 and st["timerLen"] == 60
            assert st["history"] == [{"qid": qid, "cat": "CS", "no": 1}]
            assert st["current"]["qid"] == qid
            assert st["correct"] == ["t1"] and st["submissions"]["t1"]["text"] == "persisted"
            assert (st["teams"][1]["name"], st["teams"][1]["adj"]) == ("Beta", 4)
            assert st["teams"][0]["score"] == 30
            assert all(t["connected"] is False for t in st["teams"])  # 점유는 남지 않는다
            lobby = p.recv("lobby")
            assert all(t["taken"] is False for t in lobby["teams"])
            p.send(type="join", team="t1")  # 재시작 전 점유 팀을 다시 고를 수 있다
            p.recv("joined")
            assert p.recv("state")["mySubmission"]["text"] == "persisted"


def test_running_timer_survives_restart_via_absolute_end_time(make_settings, clock: Clock) -> None:
    with TestClient(create_app(make_settings())) as c1:
        login(c1)
        with host(c1) as h:
            h.recv("state")
            host_cmd(h, type="pick", cat="CS")
            host_cmd(h, type="timer_toggle")
    clock.advance(5)
    with TestClient(create_app(make_settings())) as c2:
        c2.cookies.set("gb_host", c1.cookies.get("gb_host"))
        with host(c2) as h:
            assert h.recv("state")["timer"] == {"running": True, "remainMs": 15000, "lenSec": 20}


def test_unknown_current_after_question_file_change_is_dropped(make_settings, tmp_path) -> None:
    import json

    from helpers import BANK

    with TestClient(create_app(make_settings())) as c1:
        login(c1)
        with host(c1) as h:
            h.recv("state")
            host_cmd(h, type="pick", cat="CS")
    smaller = {"categories": BANK["categories"], "questions": [q for q in BANK["questions"] if q["cat"] != "CS"]}
    path = tmp_path / "smaller.json"
    path.write_text(json.dumps(smaller, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(make_settings(questions_path=path))) as c2:
        c2.cookies.set("gb_host", c1.cookies.get("gb_host"))
        with host(c2) as h:
            st = h.recv("state")
        assert st["current"] is None and st["history"] == []


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def test_timer_expiry_task_broadcasts_zero_remaining(make_settings, tmp_path) -> None:
    """서버가 스스로 만료를 방송한다. 실제 시계로 50ms 만 기다린다."""
    from app.db import Store
    from app.game import Game, new_state
    from app.questions import load_bank
    from app.ws import Hub

    settings = make_settings(now=time.time)
    game = Game(load_bank(settings.questions_path), new_state(), settings.now, settings.rng)
    store = Store(tmp_path / "expiry.db")
    hub = Hub(game, store)
    game.state["current"] = "c1"
    game.state["used"] = ["c1"]
    h_ws, p_ws = FakeWs(), FakeWs()
    hub.hosts.add(h_ws)  # type: ignore[arg-type]
    hub.players[p_ws] = "t1"  # type: ignore[index]
    hub.owner["t1"] = p_ws  # type: ignore[assignment]

    async def scenario() -> None:
        game.state["timer"] = {"endAt": game.now_ms() + 50, "remain": None}
        hub.commit()  # 만료 태스크를 예약한다
        await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert h_ws.sent and h_ws.sent[-1]["timer"] == {"running": False, "remainMs": 0, "lenSec": 20}
    assert p_ws.sent[-1]["timer"]["remainMs"] == 0 and p_ws.sent[-1]["locked"] is True
    assert game.state["timer"] == {"endAt": None, "remain": 0}
    assert store.load_state()["timer"] == {"endAt": None, "remain": 0}  # 확정된 값이 저장된다


def test_pause_cancels_expiry_broadcast(make_settings, tmp_path) -> None:
    from app.db import Store
    from app.game import Game, new_state
    from app.questions import load_bank
    from app.ws import Hub

    settings = make_settings(now=time.time)
    game = Game(load_bank(settings.questions_path), new_state(), settings.now, settings.rng)
    hub = Hub(game, Store(tmp_path / "pause.db"))
    game.state["current"] = "c1"
    game.state["used"] = ["c1"]
    ws = FakeWs()
    hub.hosts.add(ws)  # type: ignore[arg-type]

    async def scenario() -> None:
        game.state["timer"] = {"endAt": game.now_ms() + 50, "remain": None}
        hub.commit()
        game.apply_host({"type": "timer_toggle"})  # 일시정지
        hub.commit()
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert ws.sent == []
    assert game.state["timer"]["endAt"] is None and game.state["timer"]["remain"] > 0
