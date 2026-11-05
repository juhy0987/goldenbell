"""환경변수 설정. 비밀번호가 없으면 기동하지 않는다."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_QUESTIONS = Path(__file__).parent / "questions.json"
ENV_PREFIX = "GOLDENBELL_"
KNOWN_ENV = frozenset({"GOLDENBELL_HOST_PASSWORD", "GOLDENBELL_DB", "GOLDENBELL_QUESTIONS"})


@dataclass(frozen=True)
class Settings:
    host_password: str = field(repr=False)  # 로그 · 예외에 설정 객체가 찍혀도 비밀번호가 나오지 않게
    db_path: Path = Path("goldenbell.db")
    questions_path: Path = DEFAULT_QUESTIONS
    # 테스트가 경계 시각 · 출제 순서를 고정할 수 있게 시계 (초) 와 난수를 주입받는다
    now: Callable[[], float] = time.time
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if not self.host_password or not self.host_password.strip():
            raise ValueError("GOLDENBELL_HOST_PASSWORD must be set to a non-empty value")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        # 오타 난 설정이 조용히 무시되지 않게 모르는 GOLDENBELL_* 키는 거부한다 (값은 메시지에 넣지 않는다)
        unknown = sorted(k for k in env if k.startswith(ENV_PREFIX) and k not in KNOWN_ENV)
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(unknown)} (known: {', '.join(sorted(KNOWN_ENV))})")
        return cls(
            host_password=env.get("GOLDENBELL_HOST_PASSWORD", ""),
            db_path=Path(env.get("GOLDENBELL_DB") or "goldenbell.db"),
            questions_path=Path(env.get("GOLDENBELL_QUESTIONS") or DEFAULT_QUESTIONS),
        )
