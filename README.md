# Nixie MVP

개인형 AI Agent입니다. 소유자 1명당 Agent 1개가 동작합니다. Agent끼리는 A2A Protocol(JSON-RPC 2.0 over HTTP)로 통신합니다.

## 기능
| 기능 | 설명 |
|---|---|
| 일정 조율 | 내 Agent가 상대 Agent에게 빈 시간을 묻습니다. 공통 슬롯을 찾아 회의를 제안합니다. |
| 소유자 승인 | 상대 소유자가 승인해야 일정이 확정됩니다. `meeting.auto_accept` scope를 주면 승인 없이 확정됩니다. |
| 정보 공유 제어 | 사실(fact)마다 공개 범위를 지정합니다: `public`, `trusted`, `private`. `private`는 다른 Agent에 공유하지 않습니다. |
| 신뢰 관계 | 미리 등록한 peer만 요청할 수 있습니다. peer별 공유 토큰과 scope로 검사합니다. |
| 소유자 대화 | LLM을 설정하면 자연어로 명령합니다(tool calling). LLM이 없으면 `/명령` 형식을 씁니다. |
| Agent 간 대화 열람 | Agent 간 메시지마다 사람이 읽는 문장 1줄과 JSON 데이터를 함께 보냅니다. 양쪽 소유자는 같은 대화를 읽을 수 있습니다. 소유자는 대화에 글을 쓸 수 없습니다. |
| 대화 무결성 | 대화 기록은 추가만 가능합니다(SQLite trigger로 수정·삭제 차단). 각 줄은 앞 줄의 hash를 포함합니다(hash chain). |
| 감사 로그 | 모든 Agent 간 메시지를 SQLite에 기록합니다 (`GET /owner/audit`). |

## 구조
```
owner(웹/CLI) ──X-Owner-Token──▶ [Nixie Agent A] ◀──A2A JSON-RPC──▶ [Nixie Agent B] ◀── owner B
                                   │ policy / store(SQLite) / LLM(OpenAI 호환)
```
- `nixie/agent.py`: 핵심 로직 (일정 협상, 승인, 질의 응답, 소유자 대화)
- `nixie/app.py`: A2A 서버(Agent Card: `/.well-known/agent-card.json`, `/a2a/jsonrpc`), 소유자 API(`/owner/*`), 웹 채팅(`/`)
- `nixie/peer.py`: A2A 클라이언트
- `nixie/policy.py`: 인증, scope, 공개 범위
- `nixie/schedule.py`: 빈 시간 계산

## Agent 간 대화 (소유자는 읽기 전용)
- 조회: `GET /owner/conversations`, `GET /owner/conversations/{id}`. 웹 채팅의 "agent conversations" 버튼으로도 봅니다.
- 쓰기 경로가 없습니다: 대화 API는 GET만 제공합니다(POST/PUT/PATCH/DELETE는 405). 소유자 대화 도구에도 대화에 쓰는 기능이 없습니다.
- 소유자가 하는 일은 대화 밖에 있습니다: (1) 요청 시작(목표 지정), (2) 사전 규칙 설정(scope, 공개 범위), (3) 결과 승인 또는 거절. 승인 결과는 Agent가 대화로 전달합니다.

대화 예 (실제 출력):
```
alice: 안녕하세요, Alice님의 Agent입니다. Alice님이 'Lunch'(60분) 일정을 원합니다. 11/17 09:00–18:00 사이에 가능한 시간을 알려 주세요.
  bob: Bob님은 다음 시간에 가능합니다: 11/17 09:00–10:00, ... 외 7개. (일정 내용은 공유하지 않습니다.)
alice: 두 분 모두 가능한 11/17 13:00–14:00에 'Lunch'을(를) 제안합니다.
  bob: Bob님께 승인을 요청했습니다. 결정이 나오면 이 대화로 알려 드리겠습니다.
  bob: Bob님이 승인했습니다. 11/17 13:00–14:00 'Lunch' 일정을 확정합니다.
alice: 확인했습니다. Alice님 일정에 등록했습니다.
```

## 참조 서비스 기능 대비 구현 범위
Pickle과 Instinct는 Agent 간 통신 프로토콜과 내부 구조를 공개하지 않습니다. 따라서 공개 기사에 나온 기능만 비교합니다. 내부 구조는 비교할 수 없습니다.

| 참조 기능 | 출처 | Nixie |
|---|---|---|
| 친구의 Agent와 대화해 약속 시간 조율 | Pickle | 구현 (시간만. 장소·예약은 미구현) |
| 소유자가 정한 규칙 안에서 동작 | Pickle | 구현 (peer별 scope, 자동 수락 여부) |
| 공유 범위 제어 | Pickle | 구현 (fact별 public/trusted/private) |
| 신뢰 관계 Agent끼리 직접 통신 (Trusted Person) | Instinct | 구현 (peer 등록 + 토큰 + scope) |
| 상대 Agent에 일정 정보 공유 | Instinct | 구현 (빈 시간만 공유, 일정 내용 비공개) |
| 결제 등 행동 전 소유자 승인 | Pickle, Instinct | 일정 확정에 적용. 결제 기능은 없음 |
| 소유자 소개(매칭) | Pickle | 미구현 |
| 메신저 채널 (SMS, iMessage, WhatsApp) | Instinct | 미구현 (웹, CLI만) |
| 그룹 채팅 참여 | Instinct | 미구현 |
| 메일·캘린더·외부 앱 연동 | Pickle, Instinct | 미구현 |
| 클라우드 브라우저로 사이트 작업, 대리 전화 | Pickle, Instinct | 미구현 |
| 자격 증명 금고 | Pickle | 미구현 |
| 선제 알림 (아침 브리핑 등) | Pickle, Instinct | 미구현 |
| 사람이 읽을 수 있고 소유자가 개입할 수 없는 Agent 간 대화 | Nixie 요구 사항 | 구현 |

## 요구 사항
- Linux 또는 macOS, Python 3.11 이상 (Windows는 WSL2 사용)
- 1인 MVP: 1 vCPU, RAM 1 GB, GPU 불필요 (LLM은 외부 또는 사내 API 사용)
- 선택: OpenAI 호환 LLM 엔드포인트 (vLLM, Ollama `http://host:11434/v1`, LiteLLM, 사내 LLM 게이트웨이)

## 설치 및 실행 (Python)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
cp examples/.env.example .env      # 토큰 값을 바꿉니다
set -a; . ./.env; set +a
nixie serve --config examples/alice.yaml &
nixie serve --config examples/bob.yaml &
```

## 설치 및 실행 (Docker)
```bash
cp examples/.env.example .env
# 사내 TLS 프록시를 사용하면 루트 CA(PEM, *.crt)를 certs/ 폴더에 넣습니다.
docker compose up -d --build
```

## 사용 예
```bash
# Alice가 Bob과 점심 일정을 요청합니다
curl -X POST localhost:8701/owner/schedule -H "X-Owner-Token: $ALICE_OWNER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"peer":"bob","title":"Lunch","duration_min":60,"window_start":"2026-11-17T09:00","window_end":"2026-11-17T18:00"}'
# Bob이 대기 중인 요청을 확인하고 승인합니다
curl localhost:8702/owner/approvals -H "X-Owner-Token: $BOB_OWNER_TOKEN"
curl -X POST localhost:8702/owner/approvals/<id>/approve -H "X-Owner-Token: $BOB_OWNER_TOKEN"
# 명령 모드 대화
nixie chat --url http://127.0.0.1:8701 --token $ALICE_OWNER_TOKEN /ask bob "what food?"
```
웹 채팅: 브라우저에서 `http://<서버>:8701/`을 엽니다. 소유자 토큰을 입력합니다.

명령 목록: `/schedule <peer> <분> <시작> <끝> <제목>`, `/ask <peer> <질문>`, `/remember [private|trusted|public] <내용>`, `/approvals`, `/approve <id>`, `/decline <id>`, `/events`

## 테스트
```bash
pytest -q   # 실제 서버 3개(alice, bob, carol)와 가짜 LLM 서버로 E2E 검증
```
검증 항목: Agent Card 제공, 승인 기반 일정 확정과 양쪽 캘린더 반영, 예약된 슬롯 회피, 공개 범위 준수, 미등록/무권한 Agent 거부, 소유자 토큰 검사, LLM tool calling, 명령 모드.

## 제한 사항 (MVP)
- peer 인증은 공유 토큰입니다. 운영에서는 mTLS 또는 사내 SSO(OAuth2) 토큰으로 바꿉니다.
- 캘린더는 설정 파일과 SQLite입니다. Outlook/Exchange 연동은 아직 없습니다.
- Agent 탐색(디렉터리)은 없습니다. peer URL을 설정 파일에 직접 적습니다.
- 외부 서비스 대행(결제, 예매, 전화)은 없습니다.
