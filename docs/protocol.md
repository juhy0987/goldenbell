# 골든벨 서버 · 클라이언트 계약

서버와 화면이 함께 지키는 계약이다. 바꿀 때는 서버 · 화면 · 테스트를 같은 커밋에서 맞춘다.

## 목표

- **정답 유출 차단**: 문제 · 정답은 서버에만 있다. 참여자에게는 진행자가 연 문제의 본문만, 정답 · 해설은 진행자가 공개한 뒤에만 보낸다.
  진행자에게도 정답은 현재 문제 것만 보낸다. 문제 파일은 정적 경로로 서빙되지 않는다.
- **팀 점유**: 참여자는 입장할 때 팀을 고르고, 그 팀의 소켓이 열려 있는 동안 다른 연결은 같은 팀을 고를 수 없다. 소켓이 닫히면 풀린다.
- **진행자 주도**: 진행자가 문제를 열어야 참여자 화면이 답안 작성 화면으로 바뀐다.
- **진행자 인증**: `/host` 는 비밀번호 한 번 입력으로 들어간다.

## 구성

- Python 3.12 · FastAPI (uvicorn) · Jinja2 템플릿 · sqlite3 (표준 라이브러리).
- **단일 프로세스 · 단일 워커** 로 띄운다. 팀 점유 표는 프로세스 메모리에 있다 (점유는 연결 수명과 같으므로 영속할 대상이 아니다).
  게임 상태 (팀 · 출제 기록 · 채점 · 답안 · 타이머 · 설정) 와 진행자 세션은 sqlite 에 있어 재시작해도 남는다.
- 설정 (환경변수)
  - `GOLDENBELL_HOST_PASSWORD` — 필수. 비어 있으면 서버가 기동하지 않는다.
  - `GOLDENBELL_DB` — sqlite 파일 경로. 기본 `goldenbell.db`.
  - `GOLDENBELL_QUESTIONS` — 문제 파일 경로. 기본 `app/questions.json`.

## HTTP

| 메서드 · 경로 | 동작 |
|---|---|
| `GET /` | 참여자 페이지 (`play.html`). 문제 데이터를 담지 않는다 |
| `GET /host` | 진행자 쿠키가 유효하면 `host.html`, 아니면 `login.html` (컨텍스트 `error=None`) |
| `POST /host/login` | 폼 필드 `password`. 맞으면 쿠키 `gb_host` (HttpOnly · SameSite=Lax · 무작위 토큰) 를 심고 `303 → /host`. 틀리면 `401` 로 `login.html` (`error` 에 한국어 메시지) |
| `POST /host/logout` | 토큰 폐기, 쿠키 삭제, `303 → /host` |
| `GET /static/*` | `static/` 의 css · js 만 |

비밀번호 비교는 상수 시간 비교 (`hmac.compare_digest`).

## 메시지 공통

- 모든 메시지는 JSON 객체, `type` 필드가 있다.
- 오류: `{"type":"error","code":"<code>","message":"<한국어 안내>"}`. 오류는 연결을 끊지 않는다 (인증 실패 제외).
- 시각은 서버 기준. 타이머는 남은 시간 `remainMs` 로 보낸다 — 클라이언트는 받은 시각 + `remainMs` 로 자기 시계의 종료 시각을 계산한다 (시계 차이 무시).
- 서버는 상태가 바뀔 때마다 관련 소켓 전부에 새 `state` 를 보낸다. 타이머가 0 에 닿으면 서버가 스스로 남은 시간을 0 으로 확정하고 `state` 를 보낸다.

### 타이머 객체

```json
{"running": true, "remainMs": 13250, "lenSec": 20}
```

- `running=false, remainMs=null` — 시작 전. `running=false, remainMs=0` — 시간 종료 (제출 잠금). `running=false, remainMs>0` — 일시정지.

## 참여자 소켓 `/ws/play`

인증 없음. 연결 직후 서버가 `lobby` 를 보낸다.

| 방향 | 메시지 | 설명 |
|---|---|---|
| S→C | `{"type":"lobby","teams":[{"id","name","player","taken"}]}` | 팀 목록과 점유 여부. 팀에 들어가지 않은 소켓에, 점유 · 해제 · 팀 목록 변경 때마다 다시 보낸다 |
| C→S | `{"type":"join","team":"<id>"}` | 팀 점유 시도 |
| S→C | `{"type":"joined","team":"<id>"}` 뒤에 곧바로 `state` | 성공 |
| S→C | `error` code `team_taken` / `no_such_team` / `already_joined` | 실패 |
| C→S | `{"type":"leave"}` | 점유를 풀고 로비로 (응답은 `lobby`) |
| C→S | `{"type":"submit","qid":"<id>","text":"<답>"}` | 답안 제출 · 다시 제출 (덮어쓰기) |
| S→C | `{"type":"submitted","qid","text"}` 뒤에 `state` | 제출 성공 |
| S→C | `error` code `not_joined` / `not_open` (열린 문제 없음 또는 qid 불일치) / `locked` (공개됨 또는 시간 종료) / `empty` / `too_long` (40자 초과) | 제출 실패 |
| S→C | `{"type":"kicked","reason":"team_removed" \| "released"}` 뒤에 `lobby` | 진행자가 팀을 지웠거나 연결을 해제함. 점유는 풀린다 |

제출 텍스트는 앞뒤 공백을 지운 뒤 판단한다. 길이는 문자 수 기준 40.

### 참여자 `state`

```json
{
  "type": "state",
  "team": {"id": "t1", "name": "1팀", "player": ""},
  "points": 10,
  "progress": {"used": 3, "total": 32},
  "scoreboard": [{"id": "t1", "name": "1팀", "player": "", "score": 20, "rank": 1}],
  "question": null,
  "timer": {"running": false, "remainMs": null, "lenSec": 20},
  "locked": false,
  "mySubmission": null
}
```

- `question` 은 열린 문제가 없으면 `null` (대기 화면). 있으면
  `{"qid","no","cat","catLabel","text","revealed"}` 이고, **`revealed=true` 일 때만** `answer` · `note` · `hit` (우리 팀 정답 처리 여부) 가 붙는다.
- `locked` — 공개됐거나 시간 종료.
- `mySubmission` — 현재 문제에 우리 팀이 낸 답 `{"text","at"}` 또는 `null`.
- `scoreboard` 는 순위순. 동점은 같은 순위.

## 진행자 소켓 `/ws/host`

핸드셰이크 쿠키 `gb_host` 가 유효해야 한다. 아니면 accept 하지 않거나 즉시 코드 `4401` 로 닫는다. 연결 직후 서버가 `state` 를 보낸다.

### 진행자 → 서버

| 메시지 | 동작 |
|---|---|
| `{"type":"pick","cat":"<분야>"}` | 그 분야의 미출제 문제 중 무작위 하나를 출제 기록에 넣고 연다. 남은 문제가 없으면 `error` `category_empty` |
| `{"type":"open","qid":"<id>"}` | 이미 출제한 문제를 다시 연다. 출제 기록에 없으면 `error` `not_used` |
| `{"type":"back"}` | 분야 선택으로 (열린 문제 없음) |
| `{"type":"reveal"}` | 정답 공개 ↔ 가리기 토글. 공개할 때 돌던 타이머는 남은 시간으로 멈춘다 |
| `{"type":"timer_toggle"}` | 시작 / 일시정지 / 이어서. 남은 시간이 0 이거나 없으면 `lenSec` 로 새로 시작 |
| `{"type":"timer_len","sec":20}` | 10 · 20 · 30 · 60 · 90 중 하나. 타이머 초기화 |
| `{"type":"toggle_hit","team":"<id>"}` | 현재 문제에서 그 팀 정답 처리 ↔ 취소 |
| `{"type":"auto_grade"}` | 현재 문제에서 정답과 일치하는 답안을 낸 팀을 모두 정답 처리 |
| `{"type":"settings","teamCount":6,"points":10}` | 둘 다 선택. 팀 수 2 ~ 20, 점수 1 ~ 1000. 팀 수를 줄이면 뒤쪽 팀이 빠지고 그 팀 점유 소켓은 `kicked team_removed` |
| `{"type":"team_update","team":"<id>","name"?,"player"?,"adj"?}` | 팀 이름 (빈 값이면 `N팀`) · 대표 · 보너스 |
| `{"type":"release","team":"<id>"}` | 그 팀 점유를 강제로 푼다 (휴대폰이 꺼져 소켓이 남은 경우). 소켓은 `kicked released` |
| `{"type":"reset"}` | 점수 · 출제 기록 · 채점 · 답안 · 보너스를 지운다. 팀 이름 · 대표 · 점수 설정 · 제한 시간은 남는다 |

진행자 소켓은 여러 개 열려도 된다 (모두 같은 `state` 를 받는다).

### 진행자 `state`

```json
{
  "type": "state",
  "points": 10,
  "timerLen": 20,
  "progress": {"used": 3, "total": 32},
  "categories": [{"key": "CS", "label": "백엔드 CS", "total": 16, "left": 14, "used": [true, false]}],
  "history": [{"qid": "c3", "cat": "CS", "no": 1}],
  "current": null,
  "timer": {"running": false, "remainMs": null, "lenSec": 20},
  "teams": [{"id": "t1", "name": "1팀", "player": "", "adj": 0, "score": 20, "rank": 1, "connected": true}],
  "submissions": {},
  "correct": []
}
```

- `categories` 는 분야 순서 고정 (`시사` · `영화` · `밈` · `넌센스` · `CS`). `used` 는 그 분야 문제별 출제 여부 (순서는 문제 파일 순서). 문제 id · 본문 · 정답은 담지 않는다.
- `current` 는 열린 문제 `{"qid","no","cat","catLabel","text","answer","note","revealed"}` 또는 `null`. **진행자에게도 정답은 현재 문제 것만** 보낸다.
- `teams` 는 설정 순서 (순위순이 아님). `connected` 는 그 팀 점유 소켓이 열려 있는지.
- `submissions` — 현재 문제의 `{팀id: {"text","at","match"}}`. `match` 는 정답 일치 여부.
- `correct` — 현재 문제에서 정답 처리된 팀 id 목록.

## 규칙 (원본 페이지 동작 유지)

- 점수 = 보너스 `adj` + 맞힌 문제 수 × `points`. 순위는 점수 내림차순, 동점 공동 순위 (1, 1, 3).
- 정답 일치: 소문자화 후 공백과 `. - · _ ' " ‘ ’ “ ” ! ? ~` 를 지운 값이, 정답 전체 또는 정답을 괄호 `(` `)` 로 나눈 각 조각 (같은 정규화, 빈 조각 제외) 중 하나와 같으면 일치.
- 문제를 열면 공개 상태 해제, 타이머 초기화. 다시 연 문제의 채점 · 답안은 남는다.
