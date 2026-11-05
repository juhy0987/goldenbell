# 골든벨

오프라인 모임에서 쓰는 골든벨 서버. 진행자가 문제를 열면 참여자가 휴대폰으로 팀을 골라 답을 내고, 진행자가 채점 · 공개한다.
문제와 정답은 서버에만 있고, 게임 상태는 sqlite 에 저장되어 재시작해도 남는다.

## 요구

Python 3.12

## 설치

```bash
python -m venv .venv
# Windows
.venv/Scripts/python -m pip install -r requirements.txt
# POSIX
.venv/bin/python -m pip install -r requirements.txt
```

## 환경변수

| 이름 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `GOLDENBELL_HOST_PASSWORD` | 예 | — | 진행자 비밀번호. 비어 있으면 기동하지 않는다 |
| `GOLDENBELL_DB` | 아니오 | `goldenbell.db` | sqlite 파일 경로 |
| `GOLDENBELL_QUESTIONS` | 아니오 | `app/questions.json` | 문제 파일 경로 (형식은 [docs/protocol.md](docs/protocol.md)) |

모르는 `GOLDENBELL_*` 변수가 있으면 오타로 보고 기동하지 않는다.

## 실행

```bash
# Windows (PowerShell)
$env:GOLDENBELL_HOST_PASSWORD = "<비밀번호>"
.venv/Scripts/python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000

# POSIX
GOLDENBELL_HOST_PASSWORD='<비밀번호>' .venv/bin/python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

- 참여자: `http://<서버 주소>:8000/`
- 진행자: `http://<서버 주소>:8000/host`

**워커는 하나만 띄운다** (`--workers` 를 주지 않는다). 팀 점유 표가 프로세스 메모리에 있어 여러 워커면 같은 팀을 둘이 잡을 수 있다.
HTTP 로컬 운영 전제다 — 진행자 쿠키에 `Secure` 가 없다.

## 테스트

```bash
# Windows
.venv/Scripts/python -m pytest -q
# POSIX
.venv/bin/python -m pytest -q
```

## 문서

- [docs/protocol.md](docs/protocol.md) — HTTP · WebSocket 계약, 오류 코드, 규칙, 알려진 제약
- [docs/reference-artifact.html](docs/reference-artifact.html) — 원본 페이지 참고본
