"""게임 상태와 규칙. 소켓을 모른다 — 상태 dict 를 바꾸고, 실패는 GameError 로 던진다."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from app.questions import Bank, is_match

TIMER_CHOICES = (10, 20, 30, 60, 90)
MAX_ANSWER_LEN = 40
MAX_NAME_LEN = 20
TEAM_COUNT_RANGE = (2, 20)
POINTS_RANGE = (1, 1000)
ADJ_RANGE = (-100000, 100000)
# 원본 페이지에 기본값이 없어 브리프의 기본값을 쓴다
DEFAULT_TEAM_COUNT = 6
DEFAULT_POINTS = 10
DEFAULT_TIMER_LEN = 20

MESSAGES = {
    "bad_request": "잘못된 요청입니다.",
    "bad_value": "허용되지 않는 값입니다.",
    "team_taken": "이미 다른 참가자가 고른 팀입니다.",
    "no_such_team": "없는 팀입니다.",
    "already_joined": "이미 팀에 들어가 있습니다.",
    "not_joined": "먼저 팀을 고르세요.",
    "not_open": "지금 답을 낼 수 있는 문제가 없습니다.",
    "locked": "제출이 마감되었습니다.",
    "empty": "답을 입력하세요.",
    "too_long": "너무 깁니다.",
    "category_empty": "이 분야에 남은 문제가 없습니다.",
    "not_used": "아직 출제하지 않은 문제입니다.",
    "no_current": "열린 문제가 없습니다.",
}


class GameError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def payload(self) -> dict[str, str]:
        return {"type": "error", "code": self.code, "message": MESSAGES[self.code]}


def default_team(n: int) -> dict[str, Any]:
    return {"id": f"t{n}", "name": f"{n}팀", "player": "", "adj": 0}


def new_state() -> dict[str, Any]:
    return {
        "points": DEFAULT_POINTS,
        "timerLen": DEFAULT_TIMER_LEN,
        "teams": [default_team(n) for n in range(1, DEFAULT_TEAM_COUNT + 1)],
        "used": [],  # 출제 순서대로 qid
        "current": None,
        "revealed": False,
        "hits": {},  # qid -> [팀id]
        "answers": {},  # qid -> {팀id: {"text","at"}}
        "timer": {"endAt": None, "remain": None},  # endAt 이 있으면 진행 중 (ms, 서버 시계)
    }


def _int_in(value: Any, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
        raise GameError("bad_value")
    return value


def str_field(msg: dict[str, Any], key: str, code: str = "bad_request") -> str:
    """메시지 필드를 문자열로 받는다. 비교 · 해시에 쓰기 전에 경계에서 타입을 막는다."""
    value = msg.get(key)
    if not isinstance(value, str):
        raise GameError(code)
    return value


def _short_str(value: Any) -> str:
    if not isinstance(value, str):
        raise GameError("bad_value")
    value = value.strip()
    if len(value) > MAX_NAME_LEN:
        raise GameError("too_long")
    return value


class Game:
    def __init__(self, bank: Bank, state: dict[str, Any], now: Callable[[], float], rng: random.Random) -> None:
        self.bank = bank
        self.state = state
        self._now = now
        self._rng = rng
        # 문제 파일이 바뀌어 사라진 qid 는 버린다 (current 가 사라진 문제를 가리키면 화면을 만들 수 없고,
        # 남은 채점은 점수에 계속 더해진다)
        state["used"] = [qid for qid in state["used"] if qid in bank.by_id]
        state["hits"] = {qid: tids for qid, tids in state["hits"].items() if qid in bank.by_id}
        state["answers"] = {qid: subs for qid, subs in state["answers"].items() if qid in bank.by_id}
        if state["current"] not in bank.by_id:
            state["current"] = None

    def now_ms(self) -> int:
        return int(self._now() * 1000)

    # --- 조회 ---
    def team(self, tid: str) -> dict[str, Any]:
        team = next((t for t in self.state["teams"] if t["id"] == tid), None)
        if team is None:
            raise GameError("no_such_team")
        return team

    def current(self) -> dict[str, str] | None:
        qid = self.state["current"]
        return self.bank.by_id[qid] if qid else None

    def require_current(self) -> str:
        if not self.state["current"]:
            raise GameError("no_current")
        return self.state["current"]

    def scores(self) -> dict[str, int]:
        hits_per_team: dict[str, int] = {}
        for tids in self.state["hits"].values():
            for tid in tids:
                hits_per_team[tid] = hits_per_team.get(tid, 0) + 1
        points = self.state["points"]
        return {t["id"]: t["adj"] + hits_per_team.get(t["id"], 0) * points for t in self.state["teams"]}

    def ranks(self) -> dict[str, int]:
        scores = self.scores()
        return {tid: 1 + sum(other > s for other in scores.values()) for tid, s in scores.items()}

    # --- 타이머 ---
    def timer_view(self) -> dict[str, Any]:
        timer = self.state["timer"]
        length = self.state["timerLen"]
        if timer["endAt"] is not None:
            left = timer["endAt"] - self.now_ms()
            if left > 0:
                return {"running": True, "remainMs": left, "lenSec": length}
            return {"running": False, "remainMs": 0, "lenSec": length}
        return {"running": False, "remainMs": timer["remain"], "lenSec": length}

    def settle_timer(self) -> bool:
        """종료 시각이 지난 타이머를 남은 시간 0 으로 확정한다. 바뀌었으면 True."""
        timer = self.state["timer"]
        if timer["endAt"] is not None and timer["endAt"] <= self.now_ms():
            self.state["timer"] = {"endAt": None, "remain": 0}
            return True
        return False

    def _reset_timer(self) -> None:
        self.state["timer"] = {"endAt": None, "remain": None}

    def _pause_timer(self) -> None:
        view = self.timer_view()
        self.state["timer"] = {"endAt": None, "remain": view["remainMs"]}

    def locked(self) -> bool:
        # 태스크가 아직 돌지 않았어도 남은 시간 계산으로 판정한다
        return self.state["revealed"] or self.timer_view()["remainMs"] == 0

    # --- 참여자 ---
    def submit(self, tid: str, qid: str, text: Any) -> str:
        if not self.state["current"] or qid != self.state["current"]:
            raise GameError("not_open")
        if self.locked():
            raise GameError("locked")
        if not isinstance(text, str):
            raise GameError("bad_request")
        text = text.strip()
        if not text:
            raise GameError("empty")
        if len(text) > MAX_ANSWER_LEN:
            raise GameError("too_long")
        self.state["answers"].setdefault(qid, {})[tid] = {"text": text, "at": self.now_ms()}
        return text

    # --- 진행자 ---
    def apply_host(self, msg: dict[str, Any]) -> None:
        handler = getattr(self, f"_host_{msg['type']}", None)
        if handler is None:
            raise GameError("bad_request")
        handler(msg)

    def _open(self, qid: str) -> None:
        self.state["current"] = qid
        self.state["revealed"] = False
        self._reset_timer()

    def _host_pick(self, msg: dict[str, Any]) -> None:
        cat = str_field(msg, "cat", "bad_value")
        if cat not in {c["key"] for c in self.bank.categories}:
            raise GameError("bad_value")
        left = [q["id"] for q in self.bank.in_category(cat) if q["id"] not in self.state["used"]]
        if not left:
            raise GameError("category_empty")
        qid = self._rng.choice(left)
        self.state["used"].append(qid)
        self._open(qid)

    def _host_open(self, msg: dict[str, Any]) -> None:
        qid = str_field(msg, "qid")
        if qid not in self.state["used"]:
            raise GameError("not_used")
        self._open(qid)

    def _host_back(self, msg: dict[str, Any]) -> None:
        self.state["current"] = None
        self.state["revealed"] = False
        self._reset_timer()

    def _host_reveal(self, msg: dict[str, Any]) -> None:
        self.require_current()
        if not self.state["revealed"] and self.state["timer"]["endAt"] is not None:
            self._pause_timer()
        self.state["revealed"] = not self.state["revealed"]

    def _host_timer_toggle(self, msg: dict[str, Any]) -> None:
        self.require_current()
        timer = self.state["timer"]
        if timer["endAt"] is not None:
            self._pause_timer()
        elif timer["remain"]:
            self.state["timer"] = {"endAt": self.now_ms() + timer["remain"], "remain": None}
        else:
            self.state["timer"] = {"endAt": self.now_ms() + self.state["timerLen"] * 1000, "remain": None}

    def _host_timer_len(self, msg: dict[str, Any]) -> None:
        sec = msg.get("sec")
        if isinstance(sec, bool) or not isinstance(sec, int) or sec not in TIMER_CHOICES:
            raise GameError("bad_value")
        self.state["timerLen"] = sec
        self._reset_timer()

    def _host_toggle_hit(self, msg: dict[str, Any]) -> None:
        qid = self.require_current()
        tid = self.team(str_field(msg, "team"))["id"]
        hits = self.state["hits"].setdefault(qid, [])
        if tid in hits:
            hits.remove(tid)
        else:
            hits.append(tid)

    def _host_auto_grade(self, msg: dict[str, Any]) -> None:
        qid = self.require_current()
        answer = self.bank.by_id[qid]["a"]
        hits = self.state["hits"].setdefault(qid, [])
        team_ids = {t["id"] for t in self.state["teams"]}
        for tid, sub in self.state["answers"].get(qid, {}).items():
            if tid in team_ids and tid not in hits and is_match(sub["text"], answer):
                hits.append(tid)

    def _host_settings(self, msg: dict[str, Any]) -> None:
        count = _int_in(msg["teamCount"], TEAM_COUNT_RANGE) if "teamCount" in msg else None
        points = _int_in(msg["points"], POINTS_RANGE) if "points" in msg else None
        if points is not None:
            self.state["points"] = points
        if count is not None:
            teams = self.state["teams"][:count]
            teams += [default_team(n) for n in range(len(teams) + 1, count + 1)]
            self.state["teams"] = teams
            # 빠진 팀의 채점 · 답안을 지운다 — 다시 늘렸을 때 옛 점수가 되살아나지 않게
            kept = {t["id"] for t in teams}
            for qid, tids in self.state["hits"].items():
                self.state["hits"][qid] = [tid for tid in tids if tid in kept]
            for qid, subs in self.state["answers"].items():
                self.state["answers"][qid] = {tid: s for tid, s in subs.items() if tid in kept}

    def _host_team_update(self, msg: dict[str, Any]) -> None:
        team = self.team(str_field(msg, "team"))
        name = _short_str(msg["name"]) if "name" in msg else None
        player = _short_str(msg["player"]) if "player" in msg else None
        adj = _int_in(msg["adj"], ADJ_RANGE) if "adj" in msg else None
        if name is not None:
            team["name"] = name or f"{team['id'][1:]}팀"
        if player is not None:
            team["player"] = player
        if adj is not None:
            team["adj"] = adj

    def _host_reset(self, msg: dict[str, Any]) -> None:
        for team in self.state["teams"]:
            team["adj"] = 0
        self.state.update(used=[], current=None, revealed=False, hits={}, answers={})
        self._reset_timer()
