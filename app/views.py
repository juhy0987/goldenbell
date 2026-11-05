"""소켓으로 보내는 메시지 모양. 정답 유출 차단은 여기서 지킨다."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.game import Game
from app.questions import is_match


def _question_base(game: Game, q: dict[str, str]) -> dict[str, Any]:
    return {
        "qid": q["id"],
        "no": game.state["used"].index(q["id"]) + 1,
        "cat": q["cat"],
        "catLabel": game.bank.label(q["cat"]),
        "text": q["q"],
        "revealed": game.state["revealed"],
    }


def _progress(game: Game) -> dict[str, int]:
    return {"used": len(game.state["used"]), "total": len(game.bank.questions)}


def lobby(game: Game, taken: Collection[str]) -> dict[str, Any]:
    teams = [{"id": t["id"], "name": t["name"], "player": t["player"], "taken": t["id"] in taken} for t in game.state["teams"]]
    return {"type": "lobby", "teams": teams}


def player_state(game: Game, tid: str) -> dict[str, Any]:
    team = game.team(tid)
    scores, ranks = game.scores(), game.ranks()
    board = [
        {"id": t["id"], "name": t["name"], "player": t["player"], "score": scores[t["id"]], "rank": ranks[t["id"]]}
        for t in game.state["teams"]
    ]
    board.sort(key=lambda row: row["rank"])
    q = game.current()
    question = None
    submission = None
    if q is not None:
        question = _question_base(game, q)
        # 공개 전에는 answer · note · hit 를 절대 싣지 않는다
        if game.state["revealed"]:
            question.update(answer=q["a"], note=q["note"], hit=tid in game.state["hits"].get(q["id"], []))
        sub = game.state["answers"].get(q["id"], {}).get(tid)
        submission = {"text": sub["text"], "at": sub["at"]} if sub else None
    return {
        "type": "state",
        "team": {"id": team["id"], "name": team["name"], "player": team["player"]},
        "points": game.state["points"],
        "progress": _progress(game),
        "scoreboard": board,
        "question": question,
        "timer": game.timer_view(),
        "locked": game.locked(),
        "mySubmission": submission,
    }


def host_state(game: Game, connected: Collection[str]) -> dict[str, Any]:
    used = set(game.state["used"])
    categories = []
    for c in game.bank.categories:
        flags = [q["id"] in used for q in game.bank.in_category(c["key"])]
        categories.append({"key": c["key"], "label": c["label"], "total": len(flags), "left": flags.count(False), "used": flags})
    history = [
        {"qid": qid, "cat": game.bank.by_id[qid]["cat"], "no": n} for n, qid in enumerate(game.state["used"], start=1)
    ]
    scores, ranks = game.scores(), game.ranks()
    teams = [
        {**t, "score": scores[t["id"]], "rank": ranks[t["id"]], "connected": t["id"] in connected}
        for t in game.state["teams"]
    ]
    q = game.current()
    current = submissions = None
    correct: list[str] = []
    if q is not None:
        current = {**_question_base(game, q), "answer": q["a"], "note": q["note"]}
        subs = game.state["answers"].get(q["id"], {})
        submissions = {tid: {**s, "match": is_match(s["text"], q["a"])} for tid, s in subs.items()}
        correct = list(game.state["hits"].get(q["id"], []))
    return {
        "type": "state",
        "points": game.state["points"],
        "timerLen": game.state["timerLen"],
        "progress": _progress(game),
        "categories": categories,
        "history": history,
        "current": current,
        "timer": game.timer_view(),
        "teams": teams,
        "submissions": submissions or {},
        "correct": correct,
    }
