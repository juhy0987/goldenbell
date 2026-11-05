"""문제 파일 로드와 정답 일치 판정."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 원본 norm(): 소문자화 후 공백과 . - · _ ' " ‘ ’ “ ” ! ? ~ 제거
_STRIP_RE = re.compile(r"[\s.\-·_'\"‘’“”!?~]")


def normalize(text: str) -> str:
    return _STRIP_RE.sub("", text.lower())


def is_match(text: str, answer: str) -> bool:
    """정답 전체 또는 괄호로 나눈 각 조각 중 하나와 정규화 값이 같으면 일치."""
    candidates = {normalize(answer)} | {normalize(part) for part in re.split(r"[()]", answer)}
    candidates.discard("")
    return normalize(text) in candidates


@dataclass(frozen=True)
class Bank:
    categories: list[dict[str, str]]  # {"key","label","color"} — 분야 순서 고정
    questions: list[dict[str, str]]  # {"id","cat","q","a","note"} — 파일 순서
    by_id: dict[str, dict[str, str]]

    def label(self, cat: str) -> str:
        return next(c["label"] for c in self.categories if c["key"] == cat)

    def in_category(self, cat: str) -> list[dict[str, str]]:
        return [q for q in self.questions if q["cat"] == cat]


def load_bank(path: Path) -> Bank:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    categories = data["categories"]
    questions = data["questions"]
    keys = {c["key"] for c in categories}
    for c in categories:
        if not all(isinstance(c.get(k), str) and c[k] for k in ("key", "label")):
            raise ValueError(f"invalid category entry: {c!r}")
    for q in questions:
        if not all(isinstance(q.get(k), str) for k in ("id", "cat", "q", "a", "note")):
            raise ValueError(f"invalid question entry: {q!r}")
        if q["cat"] not in keys:
            raise ValueError(f"question {q['id']} has unknown category {q['cat']!r}")
    by_id = {q["id"]: q for q in questions}
    if len(by_id) != len(questions):
        raise ValueError("duplicate question ids")
    return Bank(categories=categories, questions=questions, by_id=by_id)
