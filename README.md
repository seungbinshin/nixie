# Nixie

SK hynix 구성원을 위한 개인형 업무 Agent입니다. 코딩 도구가 아닙니다. Pickle처럼 일상 업무를 대신 처리하는 "사내 업무 expert"입니다.
- 소유자 1명당 Agent 1개가 있습니다. Agent끼리는 A2A Protocol(JSON-RPC 2.0 over HTTP)로 통신합니다.
- Agent 간 대화는 사람이 읽을 수 있습니다. 소유자는 대화를 읽기만 하고, 대화에 개입할 수 없습니다.

## 구조
```
            ┌──────────── always on ────────────┐
owner ──▶  │ gateway (/u/<user>/...)            │──start/stop──▶ [sandbox: agent of <user>]  (container / Kata / Firecracker)
peer  ──▶  │  · Agent Card from disk (no start) │                   │ mounts ONLY <users_root>/<user>/
agents     │  · idle reaper                     │                   │ API ─▶ in-house services (room, HR, approval, search, ITSM)
            │ browser-pool (Chromium x N)       │◀──lease per job──│ CLI ─▶ allow-listed programs in workspace/
            └───────────────────────────────────┘                   │ LLM ─▶ fast tier | pro tier (Kimi K3.1 Pro / DeepSeek V4.1 Flash class)
disk (permanent): <users_root>/<user>/{agent.yaml, data/nixie.db, workspace/, browser/storage_state.json}
```

## 설계 요구 사항과 구현
| 요구 사항 | 구현 | 검증 |
|---|---|---|
| 1. 사용자별 상시 샌드박스 없음. 디스크만 유지하고, 요청이 오면 컨테이너/microVM을 띄우고 유휴 시간이 지나면 내림 | `nixie/sandbox.py`, `nixie/gateway.py`. 게이트웨이가 `/u/<user>/...` 요청을 받으면 샌드박스를 시작합니다. 유휴 시간(`--idle-seconds`)이 지나면 reaper가 종료합니다. 처리 중인 요청이 있으면 종료하지 않습니다. Agent Card는 디스크의 설정으로 응답하므로 탐색 요청은 샌드박스를 깨우지 않습니다. Backend: `process`(개발), `container`(docker/nerdctl/podman, `--runtime`으로 `runc`, `io.containerd.kata.v2`, `runsc`, `aws.firecracker` 선택). 컨테이너는 `--read-only`, `--cap-drop ALL`, `no-new-privileges`, CPU·메모리 제한, 디스크 소유자 uid로 실행합니다. | `tests/test_sandbox.py`: 요청 시 기동 → 유휴 종료 → 재기동 후 승인 대기 상태 유지(디스크), A2A 요청이 상대 샌드박스를 깨움. Docker(runc) 컨테이너로 같은 수명 주기 확인 |
| 2. 샌드박스에 Chromium 없음. 필요할 때만 공용 워커 풀에서 빌리고, 사용자 프로필/쿠키만 마운트 | `nixie/browser_pool.py`. 풀이 Chromium 워커 N개를 유지합니다. 작업 1건마다 워커 1개를 빌리고, 새 browser context에 그 사용자의 `browser/storage_state.json`(쿠키, localStorage)만 넣습니다. 작업 후 프로필을 사용자 디스크에 다시 쓰고 context를 닫습니다. 단계는 고정 DSL(goto, click, fill, select, press, wait_for, text)이고 임의 JavaScript는 없습니다. 허용 도메인 밖의 요청은 차단합니다. Agent 이미지(`Dockerfile`)에는 브라우저가 없습니다. | `tests/test_browser_pool.py`(실제 Chrome): SSO 쿠키가 다음 lease에서도 유지, 다른 사용자에게는 보이지 않음, 도메인·사용자 ID·토큰 검사 |
| 3. API/CLI로 가능하면 shell에서 처리. 실제 클릭이 필요할 때만 브라우저 | `nixie/tools.py`. skill마다 경로를 여러 개 정의하고, 항상 API → CLI → 브라우저 순서로 시도합니다. 브라우저 경로는 API와 CLI 경로가 모두 없을 때만 사용합니다. API 오류(4xx/5xx)는 브라우저로 넘어가지 않습니다. CLI는 allowlist 프로그램만 shell 해석 없이 실행합니다(`argv`). 범용 `browse` 도구는 이유가 있어야 하고, API가 있는 호스트에는 거부됩니다. | `tests/test_tasks.py`: API 우선, API 없으면 CLI, 둘 다 없을 때만 브라우저로 사내 포털 양식 제출 |
| 4. 코딩 도구가 아닌 사내 업무 expert. 필요할 때 사내 고성능 모델 연결 | `nixie/skills/hynix-work.yaml`: 회의실 조회·예약, 휴가 신청·잔여 조회, 결재 대기, 사내 문서·규정 검색, IT 요청. 되돌릴 수 없는 작업(`confirm: true`)은 소유자 승인 후 실행합니다. `nixie/models.py`: 기본은 `fast` tier입니다. 문서 요약 같은 무거운 작업, 긴 문맥, 긴 도구 루프, `fast` 실패 시에만 `pro` tier로 올립니다. `max_tier`로 상한을 정합니다. `pro`는 Kimi K3.1 Pro(`kimi-k3.1-pro`) 또는 DeepSeek V4.1 Flash 수준의 사내 OpenAI 호환 엔드포인트입니다. `extra`로 `reasoning_effort` 같은 모델별 값을 전달합니다. | `tests/test_models.py`: 기본 fast, 조건별 pro 승격, 실패 시 승격, 상한 적용, 문서 검색 후 요약만 pro 사용. `tests/test_tasks.py`: 승인 전 미실행 |

## 기존 기능
| 기능 | 설명 |
|---|---|
| 일정 조율 | 내 Agent가 상대 Agent에게 빈 시간을 묻고, 공통 슬롯을 제안합니다. 상대 소유자가 승인하면 확정됩니다. |
| 정보 공유 제어 | 사실(fact)마다 공개 범위를 지정합니다: `public`, `trusted`, `private`. |
| 신뢰 관계 | 등록한 peer만 요청할 수 있습니다. peer별 토큰과 scope로 검사합니다. |
| Agent 간 대화 열람 | 메시지마다 사람이 읽는 문장 1줄과 JSON 데이터를 보냅니다. 양쪽 소유자가 같은 대화를 읽습니다. 쓰기 API는 없습니다(405). 기록은 추가만 가능하고 hash chain으로 무결성을 검사합니다. |

## 실행 방법

### A. 개발 (프로세스 backend, Docker 없음)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
mkdir -p /tmp/nixie-users && cp -r examples/users/* /tmp/nixie-users/
set -a; . examples/.env.example; set +a        # 값을 바꾼 후 사용
nixie gateway --users-root /tmp/nixie-users --backend process --port 8080 --idle-seconds 120 &
nixie browser-pool --users-root /tmp/nixie-users --size 2 --allow-domain skhynix.com &   # Chromium 필요
```
예제 사용자 설정의 `http://gateway:8080`과 `http://browser-pool:8090`은 개발 환경 주소로 바꿉니다.

### B. 운영형 (Docker Compose)
```bash
cp examples/.env.example .env                  # 토큰, NIXIE_USERS_ROOT(절대 경로) 설정
cp -r examples/users/* "$NIXIE_USERS_ROOT"/   # 사용자 디스크 준비
docker build -t nixie:latest .                 # Agent 샌드박스 이미지 (브라우저 없음)
docker compose up -d --build                   # gateway + browser-pool
```
사내 TLS 프록시가 있으면 루트 CA(PEM, `*.crt`)를 `certs/`에 넣고 빌드합니다.

### microVM runtime 선택
| runtime | gateway 옵션 |
|---|---|
| runc (기본 컨테이너) | `--backend container` |
| Kata Containers | `--backend container --runtime io.containerd.kata.v2` |
| gVisor | `--backend container --runtime runsc` |
| Firecracker (firecracker-containerd) | `--backend container --cli nerdctl --cli-arg=--address=/run/firecracker-containerd/containerd.sock --cli-arg=--snapshotter=devmapper --runtime aws.firecracker` |

Kata와 Firecracker는 호스트에 KVM(`/dev/kvm`)이 필요합니다.

## 사용 예 (게이트웨이 경유)
```bash
H="X-Owner-Token: $ALICE_OWNER_TOKEN"
curl -H "$H" localhost:8080/u/alice/owner/skills                       # 사용 가능한 업무와 경로
curl -H "$H" -X POST localhost:8080/u/alice/owner/chat -H 'Content-Type: application/json' \
  -d '{"text":"/do docs.search query=출장비 규정"}'
curl -H "$H" -X POST localhost:8080/u/alice/owner/chat -H 'Content-Type: application/json' \
  -d '{"text":"/do leave.request kind=annual start=2026-11-20 end=2026-11-20 reason=개인"}'   # 승인 대기
curl -H "$H" localhost:8080/u/alice/owner/approvals
curl -H "$H" -X POST localhost:8080/u/alice/owner/approvals/<id>/approve                   # 이때 실행
curl -H "X-Admin-Token: $NIXIE_ADMIN_TOKEN" localhost:8080/admin/sandboxes             # 실행 중 샌드박스
```
명령: `/schedule`, `/ask`, `/remember`, `/approvals`, `/approve`, `/decline`, `/events`, `/skills`, `/do <skill> k=v ...`, `/sh <program> <args...>`. LLM을 설정하면 자연어로 요청합니다.

## 사내 업무 skill 추가
`nixie/skills/hynix-work.yaml`에 skill을 추가합니다. 코드 수정은 필요 없습니다.
```yaml
- id: room.book
  title: 회의실 예약
  params: {room_id: "...", date: "YYYY-MM-DD", start: "HH:MM", end: "HH:MM", title: "text"}
  confirm: true                     # 소유자 승인 후 실행
  strategies:                       # 항상 api -> cli -> browser 순서로 시도
    - {type: api, service: room, method: POST, path: /v1/reservations, body: {room_id: "{room_id}"}}
    - {type: cli, argv: [hynix-room, book, "{room_id}"]}
```
**주의:** 현재 YAML의 서비스 경로, CLI 이름, 포털 selector는 자리 표시자입니다. 실제 사내 시스템 값으로 바꿔야 합니다.

## 테스트
```bash
pip install -e ".[test]"
pytest -q        # 23건. Chrome이 없으면 브라우저 테스트는 skip, nixie:latest 이미지가 없으면 컨테이너 테스트는 skip
```

## 제한 사항
- Kata, Firecracker runtime은 이 저장소의 테스트 환경에 KVM이 없어서 실행 검증을 하지 않았습니다. 같은 코드 경로를 runc로 검증했습니다.
- `Dockerfile.browser`, `Dockerfile.gateway`는 빌드 검증을 하지 않았습니다. 브라우저 풀은 로컬 Chrome으로 검증했습니다.
- 게이트웨이 컨테이너에 Docker socket을 마운트하면 호스트 권한이 커집니다. 운영에서는 containerd API 권한을 분리하거나 Kubernetes(RuntimeClass: kata/firecracker) 구성으로 바꿉니다.
- 서비스 토큰(`HYNIX_*`)은 게이트웨이 환경 변수로 모든 샌드박스에 전달됩니다. 운영에서는 사용자별 토큰(SSO 위임)을 사용자 디스크 또는 secret store에 둡니다.
- peer 인증은 공유 토큰입니다. 운영에서는 mTLS 또는 사내 SSO(OAuth2)로 바꿉니다.
- 메신저 채널, 그룹 채팅, 메일 연동, 선제 알림은 아직 없습니다.
