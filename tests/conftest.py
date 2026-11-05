import json
import random
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from helpers import BANK, PASSWORD, Clock


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def questions_path(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(BANK, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def make_settings(tmp_path: Path, questions_path: Path, clock: Clock) -> Callable[..., Settings]:
    def make(**over) -> Settings:
        args = dict(
            host_password=PASSWORD,
            db_path=tmp_path / "gb.db",
            questions_path=questions_path,
            now=clock,
            rng=random.Random(7),
        )
        return Settings(**{**args, **over})

    return make


@pytest.fixture
def client(make_settings: Callable[..., Settings]) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings())) as c:
        yield c
