"""sqlite 저장소. 게임 상태는 JSON 한 행, 진행자 세션은 토큰 표."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: Path) -> None:
        # TestClient 는 앱을 별도 스레드의 루프에서 돌린다. 접근은 그 루프 하나뿐이라 스레드 검사를 끈다
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("CREATE TABLE IF NOT EXISTS game (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS host_session (token TEXT PRIMARY KEY)")

    def load_state(self) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT data FROM game WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, state: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO game (id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (json.dumps(state, ensure_ascii=False),),
        )

    def add_session(self, token: str) -> None:
        self._conn.execute("INSERT INTO host_session (token) VALUES (?)", (token,))

    def has_session(self, token: str) -> bool:
        return self._conn.execute("SELECT 1 FROM host_session WHERE token = ?", (token,)).fetchone() is not None

    def delete_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM host_session WHERE token = ?", (token,))

    def close(self) -> None:
        self._conn.close()
