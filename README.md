# LLM Ops Portal

LiteLLM 프록시를 감싸는 LLM 운영 관리 포털입니다. Keycloak SSO 인증, 팀/키/모델/예산 관리, 요청 워크플로우를 제공합니다.

## 기술 스택

| 구분 | 기술 |
|------|------|
| **Backend** | FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis |
| **Frontend** | Next.js 16, React 19, TypeScript, TailwindCSS 4, shadcn/ui |
| **인증** | Keycloak 26 (SSO/SAML), 세션 기반 인증 |
| **프록시** | LiteLLM Proxy |
| **배포** | Docker Compose (로컬), Kubernetes (Kustomize + Helm) |

## 아키텍처

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Frontend   │────>│   Backend    │────>│  LiteLLM    │
│  (Next.js)  │     │  (FastAPI)   │     │  Proxy      │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
              ┌─────▼─────┐ ┌─────▼─────┐
              │ Portal DB │ │ LiteLLM DB│
              │ (custom_*)│ │ (LiteLLM_*)│
              └───────────┘ └───────────┘
                    │
              ┌─────▼─────┐
              │   Redis   │
              │  (캐시)   │
              └───────────┘
```

### 이중 데이터베이스 구조

- **Portal DB** (`custom_*` 테이블): 포털 자체 데이터 (유저, 모델 카탈로그, 요청, 설정 등)
- **LiteLLM DB** (`LiteLLM_*` 테이블): LiteLLM 프록시 데이터 (팀, 키, 예산, 멤버십 등)

## 주요 기능

### 일반 유저

| 메뉴 | 기능 |
|------|------|
| **모델 캘린더** | 모델 상태 변경 일정을 캘린더 뷰로 확인 |
| **모델 대시보드** | 사용 가능한 모델 현황, 상태 분포, 최근 변경 이력 |
| **내 팀** | 소속 팀 목록, 팀별 예산/키/모델 관리 |
| **팀 탐색** | 전체 팀 검색 및 가입 요청 |
| **내 전체 키** | API 키 생성/조회/복사/삭제 |
| **내 요청** | 팀 가입 및 예산 변경 요청 현황 |

### 관리자 (Super User)

| 메뉴 | 기능 |
|------|------|
| **요청 관리** | 팀 가입/예산 변경 요청 승인/거절 |
| **관리자 대시보드** | 전체 모델 현황 (숨김 모델 포함) |
| **모델 관리** | 모델 카탈로그 CRUD, 상태 관리, 일정 설정 |
| **모델 캐시 관리** | Redis 기반 모델 캐시 관리 |
| **예산 관리** | 전체 예산 현황 조회 |
| **포털 설정** | TPM/RPM 기본값, 신규 유저 자동 등록, 팀 숨기기 |

### 핵심 기능 상세

#### 팀 관리
- **팀 가입 요청** -> 관리자 승인/거절 워크플로우
- **멤버 역할 변경** (관리자 <-> 멤버) 확인 모달
- **멤버 예산 변경** - 관리자가 직접 멤버별 예산 설정
- **팀 설정** - 멤버 기본 예산 설정
- **숨김 팀** - 일반 유저에게 특정 팀 비노출
- `all-proxy-models` 팀은 "모든 모델"로 표시

#### API 키 관리
- **sk-JWT 형식** - `sk-` prefix + JWT (HS256 서명)
- **키 별칭** - `metadata.display_alias`에 저장, LiteLLM 내부 `key_alias`와 분리
- **키 복사** - `sk-` prefix 제거 후 사용자에게 제공 (네트워크에도 미노출)
- **키 생성 시** - 포털 설정의 기본 TPM/RPM 적용

#### 모델 카탈로그
- **상태 흐름**: Testing -> Prerelease -> LTS -> Deprecating -> Deprecated
- **상태 일정** - 각 상태별 전환 예정일 설정
- **가시성 제어** - `visible=false` 모델은 일반 유저 API 응답에서 제외
- **변경 이력** - 모든 상태 변경이 기록되며 대시보드에서 조회 가능

#### 신규 유저 자동 등록
- SSO 로그인 시 자동으로 LiteLLM에 유저 생성
- **기본 팀** - 모든 신규 유저에게 부여 (쉼표로 여러 팀 지원)
- **사번 규칙** - prefix 기반으로 추가 팀 배정 (기본 팀 + 규칙 팀)
- 유저 ID는 내부적으로 대문자 (`.upper()`) 사용, Keycloak은 소문자

#### 모델 캐시 (Redis)
- Redis Hash Set 기반 (`display_name` -> JSON)
- JSON 구조: `{"model", "apiBase", "apiKey", "options", ...}` 확장 가능
- 일반/클러스터 Redis 모두 지원
- 모델 카탈로그와 독립 운영

## 프로젝트 구조

```
litellm-ops/
├── backend/
│   ├── app/
│   │   ├── api/           # API 엔드포인트
│   │   │   ├── auth.py          # SSO 인증, 자동 프로비저닝
│   │   │   ├── teams.py         # 팀 관리, 멤버, 설정
│   │   │   ├── keys.py          # API 키 CRUD
│   │   │   ├── models_catalog.py # 모델 카탈로그, 이력
│   │   │   ├── catalog.py       # Redis 모델 캐시
│   │   │   ├── team_requests.py # 요청 워크플로우
│   │   │   ├── budgets.py       # 예산 조회
│   │   │   └── portal_settings.py # 포털 설정
│   │   ├── auth/          # 인증/인가
│   │   ├── clients/       # 외부 서비스 클라이언트 (LiteLLM, Redis)
│   │   ├── db/            # DB 모델, 세션
│   │   └── config.py      # 설정
│   ├── migrations/        # Alembic 마이그레이션
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/(app)/     # 인증 필요 페이지
│   │   │   ├── teams/           # 팀 목록, 상세, 탐색
│   │   │   ├── keys/            # 키 목록, 생성
│   │   │   ├── models/          # 대시보드, 캘린더
│   │   │   ├── requests/        # 내 요청
│   │   │   └── admin/           # 관리자 페이지
│   │   ├── components/    # UI 컴포넌트
│   │   ├── hooks/         # React Query 훅 (use-api.ts)
│   │   ├── types/         # TypeScript 타입
│   │   └── lib/           # 유틸리티
│   └── package.json
├── deploy/
│   ├── kustomize/         # K8s 배포 (base + overlays)
│   ├── helm/              # Helm 차트
│   └── keycloak/          # Keycloak 설정
├── docker-compose.yml
└── .env.example
```

## API 엔드포인트

### 인증
| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/auth/login` | SSO 로그인 시작 |
| GET | `/api/auth/callback` | SSO 콜백 + 자동 프로비저닝 |
| GET | `/api/auth/me` | 현재 유저 정보 |
| GET | `/api/auth/logout` | 로그아웃 |

### 팀
| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/teams` | 내 팀 목록 |
| GET | `/api/teams/discover` | 전체 팀 탐색 |
| GET | `/api/teams/{id}` | 팀 상세 (키, 모델, 멤버십) |
| GET | `/api/teams/{id}/members` | 멤버 목록 (페이지네이션) |
| POST | `/api/teams/{id}/members/role` | 멤버 역할 변경 |
| PUT | `/api/teams/{id}/members/{uid}/budget` | 멤버 예산 변경 |
| DELETE | `/api/teams/{id}/members/{uid}` | 멤버 제거 |
| PUT | `/api/teams/{id}/settings` | 팀 설정 (멤버 기본 예산) |

### 키
| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/keys` | 키 생성 (sk-JWT) |
| GET | `/api/keys` | 내 키 목록 |
| GET | `/api/keys/{hash}/reveal` | 키 복사 |
| DELETE | `/api/keys/{hash}` | 키 삭제 |

### 모델
| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/models` | 모델 목록 (LiteLLM + 카탈로그 병합) |
| GET | `/api/models/catalog` | 카탈로그 목록 |
| POST | `/api/models/catalog` | 카탈로그 등록 |
| PUT | `/api/models/catalog/{id}` | 카탈로그 수정 |
| DELETE | `/api/models/catalog/{id}` | 카탈로그 삭제 |
| GET | `/api/models/catalog/history` | 전체 상태 변경 이력 |
| GET | `/api/models/catalog/history/summary` | 이력 통계 |

### 요청
| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/team-requests` | 팀 가입 요청 |
| POST | `/api/team-requests/budget` | 예산 변경 요청 |
| GET | `/api/team-requests` | 요청 목록 (mine_only 지원) |
| POST | `/api/team-requests/{id}/approve` | 요청 승인 |
| POST | `/api/team-requests/{id}/reject` | 요청 거절 |

### 모델 캐시 (Redis)
| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/catalog` | 캐시 목록 |
| POST | `/api/catalog` | 캐시 항목 추가 |
| PUT | `/api/catalog/entry/{name}` | 캐시 항목 수정 |
| DELETE | `/api/catalog/entry/{name}` | 캐시 항목 삭제 |

### 설정
| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/settings` | 포털 설정 조회 |
| PUT | `/api/settings` | 포털 설정 변경 |
| GET/PUT | `/api/settings/hidden-teams` | 숨김 팀 관리 |
| GET/PUT | `/api/settings/default-team-rules` | 사번 기반 팀 규칙 |

## 로컬 개발

```bash
# 환경 변수 설정
cp .env.example .env

# Docker Compose로 실행
docker compose up -d

# Backend 개발 서버
cd backend && pip install -e . && uvicorn app.main:app --reload --port 8000

# Frontend 개발 서버
cd frontend && npm install && npm run dev

# DB 마이그레이션
cd backend && alembic upgrade head
```

## 환경 변수

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | Portal DB 연결 (custom_* 테이블) |
| `LITELLM_DATABASE_URL` | LiteLLM DB 연결 (LiteLLM_* 테이블) |
| `LITELLM_API_URL` | LiteLLM Proxy 주소 |
| `LITELLM_MASTER_KEY` | LiteLLM 마스터 키 |
| `KEYCLOAK_URL` | Keycloak 서버 주소 |
| `KEYCLOAK_REALM` | Keycloak Realm |
| `KEYCLOAK_CLIENT_ID` | Keycloak Client ID |
| `KEYCLOAK_CLIENT_SECRET` | Keycloak Client Secret |
| `APP_REDIS_URL` | Redis 연결 URL |
| `APP_REDIS_PASSWORD` | Redis 비밀번호 |
| `APP_REDIS_CLUSTER` | Redis 클러스터 모드 (true/false) |
| `APP_REDIS_CATALOG_PREFIX` | Redis 카탈로그 키 prefix |
| `SESSION_COOKIE_NAME` | 세션 쿠키 이름 |
| `SESSION_SECRET` | 세션 암호화 키 |

## 배포

### Kubernetes (Kustomize)

```bash
# 개발 환경
kubectl apply -k deploy/kustomize/overlays/dev

# 운영 환경
kubectl apply -k deploy/kustomize/overlays/prd
```

### Kubernetes (Helm)

```bash
helm install llm-ops deploy/helm/litellm-platform -f deploy/helm/values-allinone.yaml
```
