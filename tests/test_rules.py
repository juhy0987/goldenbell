import pytest

from app.game import Game, new_state
from app.questions import is_match, load_bank, normalize

# docs/protocol.md "규칙" 이 적은 제거 대상 문자 (원본 norm 과 같은 집합)
STRIPPED = [" ", "\t", ".", "-", "·", "_", "'", '"', "‘", "’", "“", "”", "!", "?", "~"]
KEPT = [",", ":", ";", "/", "\\", "*", "+", "=", "&", "…", "`"]


@pytest.mark.parametrize("ch", STRIPPED)
def test_normalize_strips_each_documented_char(ch: str) -> None:
    assert normalize(f"a{ch}b") == "ab"


@pytest.mark.parametrize("ch", KEPT)
def test_normalize_keeps_other_punctuation(ch: str) -> None:
    assert normalize(f"a{ch}b") == f"a{ch}b"


def test_normalize_lowercases_and_keeps_hangul() -> None:
    assert normalize("  HeLLo Wörld  ") == "helloWörld".lower()
    assert normalize("대 한 민 국!") == "대한민국"


@pytest.mark.parametrize(
    ("text", "answer", "expected"),
    [
        ("Seoul", "seoul", True),
        (" s.e-o_u l ", "Seoul", True),
        ("seoul!", "Seoul", True),
        ("서울", "서울", True),
        ("seoul", "Seoul(서울)", True),  # 괄호 앞 조각
        ("서울", "Seoul(서울)", True),  # 괄호 안 조각
        ("seoul서울", "Seoul(서울)", False),  # 괄호 문자는 정규화로 지워지지 않으므로 조각을 이어 붙인 값은 불일치
        ("Seoul(서울)", "Seoul(서울)", True),
        ("busan", "Seoul(서울)", False),
        ("", "Seoul", False),
        ("   ", "Seoul", False),
        ("", "()", False),  # 빈 조각은 후보에서 제외 — 빈 답이 일치하면 안 된다
        ("", "a()b", False),
        ("a", "a()b", True),
        ("b", "a()b", True),
        ("ab", "a()b", False),
        ("seou", "Seoul", False),  # 부분 일치 금지
        ("Seoul,", "Seoul", False),  # 쉼표는 지우지 않는다
    ],
)
def test_is_match_table(text: str, answer: str, expected: bool) -> None:
    assert is_match(text, answer) is expected


def test_load_bank_rejects_bad_files(tmp_path) -> None:
    path = tmp_path / "q.json"
    path.write_text('{"categories":[{"key":"A","label":"A"}],"questions":[{"id":"1","cat":"B","q":"","a":"","note":""}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_bank(path)
    path.write_text(
        '{"categories":[{"key":"A","label":"A"}],"questions":['
        '{"id":"1","cat":"A","q":"","a":"","note":""},{"id":"1","cat":"A","q":"","a":"","note":""}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_bank(path)


def make_game(questions_path, clock) -> Game:
    import random

    return Game(load_bank(questions_path), new_state(), clock, random.Random(1))


def test_scores_and_competition_ranking(questions_path, clock) -> None:
    g = make_game(questions_path, clock)
    g.state["points"] = 10
    g.state["hits"] = {"s1": ["t1", "t2", "t3"], "s2": ["t1", "t2"], "c1": ["t1"]}
    for t in g.state["teams"]:
        t["adj"] = {"t3": 5}.get(t["id"], 0)
    assert g.scores() == {"t1": 30, "t2": 20, "t3": 15, "t4": 0, "t5": 0, "t6": 0}
    assert g.ranks() == {"t1": 1, "t2": 2, "t3": 3, "t4": 4, "t5": 4, "t6": 4}
    g.state["teams"][1]["adj"] = 10  # t1 30 = t2 30 -> 1, 1, 3
    assert [g.ranks()[t] for t in ("t1", "t2", "t3", "t4")] == [1, 1, 3, 4]


def test_negative_adj_lowers_score(questions_path, clock) -> None:
    g = make_game(questions_path, clock)
    g.state["teams"][0]["adj"] = -15
    g.state["hits"] = {"s1": ["t1"]}
    assert g.scores()["t1"] == -5
