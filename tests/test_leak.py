"""정답 유출 차단 — 가장 중요한 계약. 키 검사가 아니라 직렬화된 문자열에서 정답 · 해설 텍스트를 찾는다."""

import json

import pytest
from starlette.testclient import TestClient

from app.config import DEFAULT_QUESTIONS, Settings
from app.main import create_app
from helpers import BANK, PASSWORD, QUESTIONS, Sock, host, host_cmd, login, play


def secrets_of(q: dict) -> list[str]:
    """정답 전체 · 괄호 조각 · 해설. 조각도 정답이므로 따로 찾는다."""
    pieces = [p.strip() for p in q["a"].replace(")", "(").split("(") if p.strip()]
    return [q["a"], *pieces, q["note"]]


def flat(raw: str) -> str:
    return json.dumps(json.loads(raw), ensure_ascii=False)


def assert_player_log_clean(log: list[str]) -> int:
    """공개 상태 state 를 뺀 모든 메시지에 어떤 문제의 정답 · 해설도 없다. 건너뛴 (공개) 메시지 수를 돌려준다."""
    revealed = 0
    for raw in log:
        msg = json.loads(raw)
        if msg["type"] == "state" and msg["question"] and msg["question"]["revealed"]:
            revealed += 1
            continue
        text = flat(raw)
        for q in BANK["questions"]:
            for s in secrets_of(q):
                assert s not in text, f"leak of {s!r} in {text}"
    return revealed


def test_player_never_sees_answer_or_note_before_reveal(client: TestClient) -> None:
    login(client)
    with host(client) as h, play(client) as p, play(client) as lobby_only:
        h.recv("state")
        p.recv("lobby")
        lobby_only.recv("lobby")
        p.send(type="join", team="t1")
        p.recv("joined")
        p.recv("state")
        h.recv("state")
        lobby_only.recv("lobby")

        p.send(type="submit", qid="s1", text="no open question")
        p.recv("error")
        lobby_only.send(type="submit", qid="s1", text="x")
        lobby_only.recv("error")
        p.send(type="submit", qid="s1", text="")
        p.recv("error")

        for cat in ("시사", "시사", "CS"):  # 여러 문제를 연속으로 연다
            qid = host_cmd(h, p, lobby_only, type="pick", cat=cat)["current"]["qid"]
            st = json.loads(p.log[-1])
            assert st["question"]["text"] == QUESTIONS[qid]["q"]  # 본문은 나간다
            p.send(type="submit", qid=qid, text="my guess")
            p.recv("submitted")
            p.recv("state")
            h.recv("state")
            lobby_only.recv("lobby")
            host_cmd(h, p, lobby_only, type="timer_toggle")
        last_qid = host_cmd(h, p, lobby_only, type="back")["history"][-1]["qid"]
        host_cmd(h, p, lobby_only, type="open", qid=last_qid)
        host_cmd(h, p, lobby_only, type="reveal")  # 공개 — 이 한 메시지만 정답이 나가도 된다
        revealed_state = json.loads(p.log[-1])
        cur = QUESTIONS[revealed_state["question"]["qid"]]
        assert revealed_state["question"]["answer"] == cur["a"]
        host_cmd(h, p, lobby_only, type="reveal")  # 다시 가리면 사라진다

        assert assert_player_log_clean(p.log) == 1
        assert assert_player_log_clean(lobby_only.log) == 0
        for s in secrets_of(cur):
            assert s not in flat(p.log[-1])


def test_host_state_has_no_question_text_or_non_current_answers(client: TestClient) -> None:
    login(client)
    with host(client) as h:
        h.recv("state")
        for cat in ("시사", "영화", "CS"):
            h.send(type="pick", cat=cat)
            h.recv("state")
        h.send(type="back")
        last_qid = h.recv("state")["history"][-1]["qid"]
        h.send(type="open", qid=last_qid)
        h.recv("state")
        for raw in h.log:
            st = json.loads(raw)
            cur = st["current"]["qid"] if st["current"] else None
            text = flat(raw)
            for q in BANK["questions"]:
                if q["id"] == cur:
                    continue
                for s in [*secrets_of(q), q["q"]]:
                    assert s not in text, f"{q['id']} leaked in host state: {s!r}"
            for c in st["categories"]:
                assert set(c) == {"key", "label", "total", "left", "used"}
            assert all(set(e) == {"qid", "cat", "no"} for e in st["history"])
            if cur:
                assert set(st["current"]) == {"qid", "no", "cat", "catLabel", "text", "answer", "note", "revealed"}


def test_host_only_gets_current_answer_even_for_old_submissions(client: TestClient) -> None:
    login(client)
    with host(client) as h, play(client) as p:
        h.recv("state")
        p.recv("lobby")
        p.send(type="join", team="t1")
        p.recv("joined")
        p.recv("state")
        h.recv("state")
        first = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        p.send(type="submit", qid=first, text="old")
        p.recv("submitted")
        p.recv("state")
        h.recv("state")
        second = host_cmd(h, p, type="pick", cat="CS")["current"]["qid"]
        text = flat(h.log[-1])
        assert QUESTIONS[first]["a"] not in text and QUESTIONS[first]["note"] not in text
        assert QUESTIONS[second]["a"] in text


@pytest.fixture
def real_client(tmp_path) -> TestClient:
    # 실제 문제 파일로 정적 경로 유출을 본다 (기본 경로)
    with TestClient(create_app(Settings(host_password=PASSWORD, db_path=tmp_path / "r.db"))) as c:
        yield c


LEAK_PATHS_404 = [
    "/questions.json",
    "/app/questions.json",
    "/static/questions.json",
    "/static/../questions.json",
    "/static/%2e%2e/questions.json",
    "/static/..%2fquestions.json",
    "/static/",
    "/static/play.html",
    "/static/../templates/host.html",
]


def test_question_file_is_not_served_anywhere(real_client: TestClient) -> None:
    real = json.loads(DEFAULT_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    needles = [s for q in real for s in (q["q"], q["note"]) if s] + [q["a"] for q in real if len(q["a"]) >= 6]
    assert needles
    for path in LEAK_PATHS_404:
        r = real_client.get(path)
        assert r.status_code == 404, path
    login(real_client)
    for path in ["/", "/host", "/static/style.css", "/static/common.js", "/static/play.js", "/static/host.js"]:
        r = real_client.get(path)
        assert r.status_code == 200, path
        assert not any(n in r.text for n in needles), path
