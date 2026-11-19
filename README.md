# Nixie

SK hynix 구성원을 위한 개인형 업무 Agent입니다. 코딩 도구가 아닙니다. Pickle처럼 일상 업무를 대신 처리하는 "사내 업무 expert"입니다.
- 소유자 1명당 Agent 1개가 있습니다. Agent끼리는 A2A Protocol(JSON-RPC 2.0 over HTTP)로 통신합니다.
- Agent 간 대화는 사람이 읽을 수 있습니다. 소유자는 대화를 읽기만 하고, 대화에 개입할 수 없습니다.

## 구조
```
person ─HTTPS─▶ proxy (nginx) ──auth_request──▶ oauth2-proxy ──▶ 사내 IdP (OIDC)
                 │  정적 웹 UI (web/dist)
                 │  X-Auth-Request-User (클라이언트 값은 덮어씀) + X-Nixie-Proxy-Secret
                 ▼
            ┌──────────── always on (게시 포트 없음) ─────┐
            │ gateway:8080 (/u/<user>/...)                │──start/stop──▶ [sandbox: agent of <user>]  (container / Kata / Firecracker)
peer  ──▶  │  · /owner  사람(SSO 식별자)                 │                   │ mounts ONLY <users_root>/<user>/
agents     │  · /a2a    에이전트(피어 토큰)              │                   │ API ─▶ in-house services (room, HR, approval, search, ITSM)
adapters ─▶│  · /ingest 사내 수집 어댑터(서비스 토큰)    │                   │ CLI ─▶ allow-listed programs in workspace/
            │  · 실시간 스트림(디스크에서 읽음, 깨우지 않음)│                   │ LLM ─▶ fast tier | pro tier
            │  · Agent Card from disk, idle reaper        │
            │ browser-pool (Chromium x N)                 │◀──lease per job──
            └─────────────────────────────────────────────┘
disk (permanent): <users_root>/<user>/{agent.yaml, secrets.env, data/nixie.db, workspace/, browser/storage_state.json}
```

## 설계 요구 사항과 구현
| 요구 사항 | 구현 | 검증 |
|---|---|---|
| 1. 사용자별 상시 샌드박스 없음. 디스크만 유지하고, 요청이 오면 컨테이너/microVM을 띄우고 유휴 시간이 지나면 내림 | `nixie/sandbox.py`, `nixie/gateway.py`. 게이트웨이가 `/u/<user>/...` 요청을 받으면 샌드박스를 시작합니다. 유휴 시간(`--idle-seconds`)이 지나면 reaper가 종료합니다. 처리 중인 요청이 있으면 종료하지 않습니다. Agent Card는 디스크의 설정으로 응답하므로 탐색 요청은 샌드박스를 깨우지 않습니다. Backend: `process`(개발), `container`(docker/nerdctl/podman, `--runtime`으로 `runc`, `io.containerd.kata.v2`, `runsc`, `aws.firecracker` 선택). 컨테이너는 `--read-only`, `--cap-drop ALL`, `no-new-privileges`, CPU·메모리 제한, 디스크 소유자 uid로 실행합니다. | `tests/test_sandbox.py`: 요청 시 기동 → 유휴 종료 → 재기동 후 승인 대기 상태 유지(디스크), A2A 요청이 상대 샌드박스를 깨움. Docker(runc) 컨테이너로 같은 수명 주기 확인 |
| 2. 샌드박스에 Chromium 없음. 필요할 때만 공용 워커 풀에서 빌리고, 사용자 프로필/쿠키만 마운트 | `nixie/browser_pool.py`. 풀이 Chromium 워커 N개를 유지합니다. 작업 1건마다 워커 1개를 빌리고, 새 browser context에 그 사용자의 `browser/storage_state.json`(쿠키, localStorage)만 넣습니다. 작업 후 프로필을 사용자 디스크에 다시 쓰고 context를 닫습니다. 단계는 고정 DSL(goto, click, fill, select, press, wait_for, text)이고 임의 JavaScript는 없습니다. 허용 도메인 밖의 요청은 차단합니다. Agent 이미지(`Dockerfile`)에는 브라우저가 없습니다. | `tests/test_browser_pool.py`(실제 Chrome): SSO 쿠키가 다음 lease에서도 유지, 다른 사용자에게는 보이지 않음, 도메인·사용자 ID·토큰 검사 |
| 3. API/CLI로 가능하면 shell에서 처리. 실제 클릭이 필요할 때만 브라우저 | `nixie/tools.py`. skill마다 경로를 여러 개 정의하고, 항상 API → CLI → 브라우저 순서로 시도합니다. 브라우저 경로는 API와 CLI 경로가 모두 없을 때만 사용합니다. API 오류(4xx/5xx)는 브라우저로 넘어가지 않습니다. CLI는 allowlist 프로그램만 shell 해석 없이 실행합니다(`argv`). 범용 `browse` 도구는 이유가 있어야 하고, API가 있는 호스트에는 거부됩니다. | `tests/test_tasks.py`: API 우선, API 없으면 CLI, 둘 다 없을 때만 브라우저로 사내 포털 양식 제출 |
| 4. 코딩 도구가 아닌 사내 업무 expert. 필요할 때 사내 고성능 모델 연결 | `nixie/skills/hynix-work.yaml`: 회의실 조회·예약, 휴가 신청·잔여 조회, 결재 대기, 사내 문서·규정 검색, IT 요청. 되돌릴 수 없는 작업(`confirm: true`)은 소유자 승인 후 실행합니다. `nixie/models.py`: 기본은 `fast` tier입니다. 문서 요약 같은 무거운 작업, 긴 문맥, 긴 도구 루프, `fast` 실패 시에만 `pro` tier로 올립니다. `max_tier`로 상한을 정합니다. `pro`는 Kimi K3.1 Pro(`kimi-k3.1-pro`) 또는 DeepSeek V4.1 Flash 수준의 사내 OpenAI 호환 엔드포인트입니다. `extra`로 `reasoning_effort` 같은 모델별 값을 전달합니다. | `tests/test_models.py`: 기본 fast, 조건별 pro 승격, 실패 시 승격, 상한 적용, 문서 검색 후 요약만 pro 사용. `tests/test_tasks.py`: 승인 전 미실행 |

## 파일럿 (사용자 5명, 에이전트 간 방 5개)
| 요구 사항 | 구현 | 검증 |
|---|---|---|
| 사용자 5명, 방은 최대 2인, 에이전트 간 방 5개 | `nixie pilot init --plan examples/pilot/plan.yaml`(`nixie/pilot.py`). 방 1개 = 에이전트 2개. 참여자가 2명이 아니면 거부합니다. 사용자마다 `agent.yaml`(비밀 없음)과 `secrets.env`(권한 600, 그 사용자 샌드박스에만 마운트)를 만듭니다. 방 토큰은 두 사용자가 공유합니다. 다시 실행해도 토큰은 유지됩니다. | `tests/test_pilot.py::test_plan_makes_five_rooms_of_two` |
| API 위치 `http://gateway:8080/u/<user>/...` | 그대로 유지. 에이전트 간 호출도 같은 주소의 `/a2a`를 사용합니다. 게이트웨이는 게시 포트가 없습니다. | |
| 인증: X-Owner-Token 또는 nixie_session → SSO | 게이트웨이 `--auth token|both|sso`. `token`: 소유자 토큰 또는 `nixie_session` 쿠키(SSO 전, 1회용 로그인 링크 `nixie login-link`). `both`: 전환 기간. `sso`: 프록시 헤더만 허용. | `test_login_link_session_cookie_and_logout`, `test_sso_mode_refuses_token_and_cookie` |
| SSO 프록시(oauth2-proxy) 앞단, 식별자는 X-Auth-Request-User | `deploy/nginx/templates/nixie.conf.template`, `deploy/oauth2-proxy/oauth2-proxy.cfg`. nginx가 `proxy_set_header`로 클라이언트가 보낸 X-Auth-Request-User를 덮어씁니다. 게이트웨이는 X-Nixie-Proxy-Secret이 맞을 때만 이 헤더를 신뢰합니다. | `tests/test_edge_proxy.py`(실제 nginx): 위조 헤더 무시, `_` 변형 헤더 제거 |
| 에이전트는 헤더를 신뢰 경계로, 위조 방지 | 게이트웨이가 샌드박스를 시작할 때마다 비밀 값(`NIXIE_GATEWAY_SECRET`)을 새로 만들어 그 샌드박스에만 줍니다. 에이전트는 이 값이 같을 때만 X-Auth-Request-User를 믿고, 값이 `sso_user`와 같아야 소유자로 인정합니다. 다른 샌드박스는 이 값을 모르므로 위조할 수 없습니다. 게이트웨이 비밀(`NIXIE_ADMIN_TOKEN`, `NIXIE_SESSION_SECRET`, `NIXIE_PROXY_SECRET`)은 샌드박스에 전달하지 않습니다. | `test_sandbox_trusts_identity_only_with_its_gateway_secret`, `test_sso_header_trusted_only_from_proxy` |
| /owner(사람)와 /a2a(에이전트) 인증 경로 분리 | `/u/<user>/owner`: SSO 식별자/세션/토큰. `/u/<user>/a2a`: 메시지 안의 피어 토큰만. `/u/<user>/ingest`: 어댑터 서비스 토큰. 게이트웨이는 사람의 자격 증명(쿠키, 토큰, 식별 헤더)을 샌드박스로 보내지 않습니다. 그 밖의 경로는 404입니다. nginx는 `/a2a`, `/ingest`, `/.well-known`, `/admin`을 외부에서 막습니다. | `test_internal_paths_are_closed_at_the_edge` |
| 로그아웃 시 프록시 세션과 nixie_session 함께 정리 | `/auth/logout`: `nixie_session` 삭제, `Clear-Site-Data`, 그다음 `/oauth2/sign_out`(프록시 쿠키 삭제)로 이동. IdP 로그아웃까지 하려면 `NIXIE_LOGOUT_URL`에 IdP `end_session_endpoint`를 `rd`로 넣습니다. | `test_logout_clears_both_sessions` |
| 시간대 Asia/Seoul, 저장 UTC ISO 8601, 표기 KST | `nixie/timeutil.py`. DB와 A2A 데이터는 `2026-11-17T04:00:00Z` 형식입니다. 오프셋 없는 입력은 KST로 해석합니다. 에이전트 간 대화 문장과 UI는 KST로 표시합니다(`11/17 13:00–14:00 KST`). | `test_rooms_notifications_realtime_and_single_approval`, `web/src/__tests__/time.test.ts` |
| 에이전트 수명: 요청 시 기동, 유휴 시 종료 | 기존 구조 유지. 실시간 스트림은 게이트웨이가 사용자 디스크의 변경 피드(`changes` 테이블)를 읽어서 보냅니다. 그래서 화면을 열어 두어도 샌드박스가 종료되고 메모리가 해제됩니다. | `test_stream_does_not_keep_agent_alive` |
| 동일 작업 승인 2번 → 1번만 전달 | 서버: 승인 상태를 원자적으로 `pending → approving`으로 바꾼 요청 1개만 실행합니다. 같은 결정의 두 번째 요청은 첫 결과(`duplicate: true`), 반대 결정은 409. UI: 요청 중 버튼 비활성화, 같은 키 단일 실행. | 동시 요청 2개 → 실행 1번(`meeting.confirm` 1줄, 일정 1개). UI E2E: 키보드 Enter 2번 → 1번 |
| 웹 UI(별도 저장소, 정적 빌드) | `web/`(독립 프로젝트, `git subtree split --prefix web`로 분리). 자세한 내용은 `web/README.md`. | `tests/test_web_ui.py`, Lighthouse |
| Lighthouse 접근성 90 이상, 콘솔 오류 없음 | 3개 화면 × 모바일/데스크톱: 접근성 100, 권장 사항 100, `errors-in-console` 통과 | Lighthouse 12.8 |
| Windows 데스크톱 웹앱 | PWA(manifest, 서비스 워커, 아이콘, `launch_handler`, 바로 가기, 작업 표시줄 배지, 데스크톱 알림) | `test_pwa_installable_and_sso_redirect`: Chromium 설치 가능 오류 0 |
| 사내 시스템 수집 어댑터, 메일(mailbrief) | 어댑터는 사내에서 구현합니다. 연결 계약: `POST /u/<user>/ingest/notifications`(헤더 `X-Ingest-Token`, 내부망 전용). 아래 "수집 어댑터 계약"을 참고합니다. | `test_stream_does_not_keep_agent_alive`(수집 → 알림함 → 실시간) |

### 소유자 API (웹 UI가 사용)
| 메서드 | 경로 (`/u/<user>/owner` 기준) | 설명 |
|---|---|---|
| GET | `/auth/me` (게이트웨이) | 로그인 사용자, API 경로, 스트림 경로. 샌드박스를 깨우지 않음 |
| GET | `/stream` | Server-Sent Events. `change`(topic: room, chat, approval, notification) |
| GET | `/rooms`, `/rooms/{room_id}` | 방 목록(최근 활동 순, 참여자, 마지막 메시지, 미읽음), 방 대화(읽기 전용) |
| POST | `/rooms/{room_id}/read` | 읽음 위치 이동. 대화에 쓰지 않음 |
| GET, POST | `/chat` | 내 에이전트와의 대화 기록, 메시지 보내기 |
| GET | `/approvals?status=pending` | 승인 대기(요약 문장 포함) |
| POST | `/approvals/{id}/approve`, `/decline` | 1번만 실행. 반복 요청은 `duplicate: true`, 반대 결정은 409 |
| GET, POST | `/notifications`, `/notifications/{id}/read`, `/notifications/read-all` | 알림함 |

쿠키 인증으로 상태를 바꾸는 요청에는 `X-Nixie-Request: 1` 헤더가 필요합니다(CSRF 방지).

### 수집 어댑터 계약
사내 시스템 어댑터와 메일 수집(mailbrief 기반)은 이 계약으로 알림을 넣습니다. 경로는 내부망에서만 열려 있습니다.
```bash
curl -X POST http://gateway:8080/u/user1/ingest/notifications \
  -H "X-Ingest-Token: <user1의 secrets.env INGEST_TOKEN>" -H 'Content-Type: application/json' \
  -d '{"items":[{"title":"결재 요청: 장비 구매","body":"...","kind":"approval","source":"approval",
                 "external_id":"A-9","source_url":"https://approval.example/doc/A-9"}]}'
```
- `source_url`은 http/https만 허용합니다.
- 같은 `source` + `external_id`는 1번만 저장합니다. 다시 보내도 됩니다.
- 요청은 그 사용자의 샌드박스를 깨웁니다. 열린 웹 UI는 실시간으로 알림을 받습니다.

## 파일럿 2차 개선 (사용자 의견 반영)
| 의견 | 구현 | 검증 |
|---|---|---|
| 1. 사내 사이트 접근 안 됨 | 사내 네트워크 문제입니다. 이 저장소에서 바꿀 부분은 없습니다. | |
| 2. Markdown 형식 글이 그대로 보임 | 웹 UI가 Markdown(GitHub 형식: 굵게, 목록, 표, 코드)을 표시합니다. 원시 HTML은 표시하지 않습니다. 링크는 http/https만 허용하고, 다른 시스템 링크는 새 탭에서 엽니다. | `web/src/__tests__/customize.test.tsx` |
| 3. 글꼴(굴림처럼 보임) | 둥근 글꼴을 웹 빌드에 넣었습니다(외부 CDN 없음, SIL OFL): 본문 고운돋움, 제목 주아. 설정에서 주아, 동글, 시스템 글꼴을 고를 수 있습니다. | E2E: 글꼴 변경 |
| 4. 색상 선택 자유도 | "꾸미기" 창: 테마 색 8개 + 직접 고르기(모든 색), 밝게/어둡게/시스템, 글꼴. 어떤 색을 골라도 글자·버튼 대비가 4.5:1 이상이 되도록 색을 자동 조정합니다. 설정은 사용자 디스크(`profile.json`)에 저장하므로 다른 PC에서도 같습니다. | 단위 테스트: 14개 색 × 밝게/어둡게 대비 검사 |
| 5. 캐릭터(하댕이/하냥이), 프로필 사진과 이름 | 원본 캐릭터 2종(SVG): 하댕이(강아지, 기본), 하냥이(고양이). 목걸이는 테마 색을 따릅니다. 첫 방문에 캐릭터를 고릅니다. 기본 이름은 캐릭터 이름이고 자유롭게 바꿉니다(1~20자). "내 에이전트" 대화 상단 가운데에 프로필 사진과 이름표를 표시합니다(참고 이미지 배치: 왼쪽 원형 메뉴, 오른쪽 "꾸미기"). 에이전트 간 대화에도 양쪽 에이전트의 캐릭터와 이름을 표시합니다(Agent Card 확장 `https://nixie.dev/a2a/ext/profile/v1`, 디스크에서 읽으므로 상대 에이전트를 깨우지 않음). | `tests/test_workspace.py`, E2E |
| 6. 영역 최소화/최대화 | 대화방, 대화, 승인 대기, 알림함마다 최소화(머리글만 남김)와 최대화(창 전체) 버튼이 있습니다. `Esc`로 원래 크기로 돌아갑니다. 최소화 상태는 브라우저에 기억합니다. | E2E: 키보드로 최소화·최대화 |
| 7. 작업 중에도 다른 요청 | 입력창이 잠기지 않습니다. 요청은 서버에서 동시에 처리됩니다. 응답은 해당 요청을 가리킵니다(`reply_to`). 처리 중인 요청 수를 표시합니다. | 느린 LLM으로 요청 2개 동시 처리(2초가 아닌 약 1초), E2E |
| 8. 업무 자동 아카이브(수정 불가, 로컬 wiki, 에이전트 검색) | 아래 "업무 위키" 참고. | `tests/test_workspace.py`, `web/src/__tests__/wikiSync.test.ts`, E2E |

### 업무 위키 (자동 아카이브)
- **보관 대상:** 동작이 있는 요청(업무 실행, 일정 조율, 질문, 메모, 명령), 승인·거절 결정, 에이전트 간 일정 확정·거절(대화 기록 포함). 단순 조회(`/help`, `/approvals`)는 보관하지 않습니다.
- **수정 불가:** 문서는 추가만 됩니다. SQLite 트리거가 수정·삭제를 막고, 해시 체인으로 무결성을 검사합니다. 사용자 디스크의 파일 사본(`<home>/archive/docs/<id>.md`)은 읽기 전용(0444)입니다. API에도 수정·삭제가 없습니다.
- **발전하는 구조:** 문서마다 같은 주제의 이전 문서를 연결합니다. 내용을 보충하려면 "후속 메모"를 추가합니다. 후속 메모는 원문을 가리키는 새 문서이고, 원문은 바뀌지 않습니다. 주제별 목차와 역링크가 생깁니다.
- **로컬 컴퓨터:** 웹 UI의 "로컬 폴더에 저장"(Edge/Chrome, File System Access API)이 PC 폴더에 Markdown 위키(`index.md`, `topics/`, `docs/`)를 씁니다. 새 문서가 보관되면 자동으로 동기화합니다(권한이 있을 때). PC에서 고친 보관 문서는 다음 동기화 때 원본으로 되돌립니다. 다른 브라우저는 "ZIP 내려받기"를 사용합니다. Obsidian 같은 Markdown 도구로 열 수 있습니다.
- **에이전트 검색:** 요청을 처리할 때 관련 보관 문서 3개를 자동으로 참고합니다. LLM 도구 `search_archive`, `read_archive`와 명령 `/wiki <검색어>`가 있습니다. 한국어 조사("휴가를" → "휴가")를 떼고 찾습니다.
- **API:** `GET /owner/archive?q=&topic=`, `GET /owner/archive/{id}`, `POST /owner/archive/{id}/notes`, `GET /owner/archive/bundle`, `GET /owner/archive/export.zip`, 프로필 `GET/PUT /owner/profile`.

## 기존 기능
| 기능 | 설명 |
|---|---|
| 일정 조율 | 내 Agent가 상대 Agent에게 빈 시간을 묻고, 공통 슬롯을 제안합니다. 상대 소유자가 승인하면 확정됩니다. |
| 정보 공유 제어 | 사실(fact)마다 공개 범위를 지정합니다: `public`, `trusted`, `private`. |
| 신뢰 관계 | 등록한 peer만 요청할 수 있습니다. peer별 토큰과 scope로 검사합니다. |
| Agent 간 대화 열람 | 메시지마다 사람이 읽는 문장 1줄과 JSON 데이터를 보냅니다. 양쪽 소유자가 같은 대화를 읽습니다. 쓰기 API는 없습니다(405). 기록은 추가만 가능하고 hash chain으로 무결성을 검사합니다. |

## 실행 방법

### A. 개발 (프로세스 backend, Docker 없음, SSO 없음)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
(cd web && npm ci && npm run build)            # 웹 UI 정적 빌드
scripts/dev-pilot.sh 8080                      # 5명 계획을 /tmp/nixie-dev-pilot 에 만들고 게이트웨이 시작
```
스크립트가 사용자마다 1회용 로그인 링크를 출력합니다. 링크를 브라우저에서 엽니다.

### B. 파일럿 운영 (Docker Compose, SSO)
```bash
cp examples/.env.example .env                  # IdP 주소, 클라이언트 ID/비밀, 호스트 이름, NIXIE_USERS_ROOT(절대 경로)
vi examples/pilot/plan.yaml                    # 사용자 5명의 sso_user(= IdP의 sub 클레임 값)와 방 5개
nixie pilot init --plan examples/pilot/plan.yaml --users-root "$NIXIE_USERS_ROOT" --env-file .env
cp <인증서> deploy/tls/tls.crt; cp <키> deploy/tls/tls.key
(cd web && npm ci && npm run build)            # 또는 NIXIE_WEB_DIST에 웹 저장소의 빌드 경로
docker build -t nixie:latest .                 # Agent 샌드박스 이미지 (브라우저 없음)
docker compose up -d --build                   # proxy + oauth2-proxy + gateway + browser-pool
```
- IdP에 등록할 redirect URL: `https://<호스트>/oauth2/callback`.
- 게시 포트는 proxy(443, 80)뿐입니다. gateway는 내부망에만 있습니다.
- oauth2-proxy의 `X-Auth-Request-User`는 OIDC `sub` 클레임입니다. 매핑되지 않은 사용자는 403이고, 게이트웨이 로그에 값이 남습니다. 그 값을 계획의 `sso_user`에 넣습니다.
- 사내 표준 OIDC 프록시를 쓰려면 oauth2-proxy 대신 같은 계약(`/oauth2/auth` → 202 + `X-Auth-Request-User`, 또는 401)을 제공하게 합니다.
- 사내 TLS 프록시가 있으면 루트 CA(PEM, `*.crt`)를 `certs/`에 넣고 빌드합니다.

### microVM runtime 선택
| runtime | gateway 옵션 |
|---|---|
| runc (기본 컨테이너) | `--backend container` |
| Kata Containers | `--backend container --runtime io.containerd.kata.v2` |
| gVisor | `--backend container --runtime runsc` |
| Firecracker (firecracker-containerd) | `--backend container --cli nerdctl --cli-arg=--address=/run/firecracker-containerd/containerd.sock --cli-arg=--snapshotter=devmapper --runtime aws.firecracker` |

Kata와 Firecracker는 호스트에 KVM(`/dev/kvm`)이 필요합니다.

## 사용 예 (게이트웨이 경유, `--auth token|both`)
```bash
H="X-Owner-Token: $(grep OWNER_TOKEN $NIXIE_USERS_ROOT/alice/secrets.env | cut -d= -f2)"
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
pytest -q        # 46건. Chromium/웹 빌드/nginx/nixie:latest 이미지가 없으면 해당 테스트는 skip
(cd web && npm test)   # 웹 UI 단위 테스트 19건
```

## 제한 사항
- Kata, Firecracker runtime은 이 저장소의 테스트 환경에 KVM이 없어서 실행 검증을 하지 않았습니다. 같은 코드 경로를 runc로 검증했습니다.
- `Dockerfile.browser`, `Dockerfile.gateway`는 빌드 검증을 하지 않았습니다. 브라우저 풀은 로컬 Chrome으로 검증했습니다.
- 게이트웨이 컨테이너에 Docker socket을 마운트하면 호스트 권한이 커집니다. 운영에서는 containerd API 권한을 분리하거나 Kubernetes(RuntimeClass: kata/firecracker) 구성으로 바꿉니다.
- 서비스 토큰(`HYNIX_*`)은 게이트웨이 환경 변수로 모든 샌드박스에 전달됩니다. 운영에서는 사용자별 토큰(SSO 위임)을 사용자의 `secrets.env` 또는 secret store에 둡니다.
- peer 인증은 방(사용자 쌍)마다 다른 공유 토큰입니다. 운영 확대 시 mTLS 또는 서비스 간 OAuth2로 바꿉니다.
- oauth2-proxy와 실제 IdP의 연동은 이 환경에서 실행하지 않았습니다. 설정 이름은 oauth2-proxy v7.15 소스와 문서로 확인했고, nginx 설정은 실제 nginx와 `/oauth2/auth` 대체 서버로 시험했습니다.
- `docker compose`(proxy, oauth2-proxy 포함)는 이 환경에서 실행하지 않았습니다.
- 사내 수집 어댑터와 메일 수집은 사내 구현 대상입니다. 이 저장소에는 연결 계약(`/ingest/notifications`)만 있습니다.
- 메신저 채널, 그룹 채팅(3인 이상 방)은 없습니다.
