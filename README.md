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

## 메뉴별 기능 상세

### 일반 유저

#### 모델 캘린더
- 월별 캘린더 그리드에 모델 상태 전환 일정을 색상별로 표시
- 상태 필터 (Testing, Prerelease, LTS, Deprecating, Deprecated)
- 이전/다음 달 이동, 오늘로 이동 버튼
- 통계 카드: 예정된 전환 수, 이번 달 이벤트, 폐기 예정 모델 수
- 예정 전환 테이블: 다음 20건의 상태 변경 목록 (D-Day 카운트다운, 긴급도별 색상)

#### 모델 대시보드
- 통계 카드: 전체 모델 수, 카탈로그 등록, LiteLLM 활성, 폐기 예정
- 상태 분포 바: 상태별 비율을 스택 바 차트로 표시
- 최근 변경 패널: 최근 5건의 상태 변경 이력 (카탈로그 display_name으로 표시)
- 모델 목록 테이블: 검색, 상태 필터, 50건 단위 페이지네이션
  - 컬럼: 모델명, 상태 배지, Input/Output 비용, 다음 전환 예정일
  - 모델 클릭 시 상세 시트 (비용, 제한, 기능, 카탈로그 정보, 변경 이력)
- `visible=false` 모델 및 카탈로그 미등록 모델은 제외

#### 내 팀
- 소속 팀 카드 그리드 (팀명, 모델 수 배지)
- `all-proxy-models` 팀은 "모든 모델"로 표시
- 팀 카드 클릭 시 팀 상세 페이지로 이동
- 팀이 없으면 팀 탐색 링크 표시

#### 팀 상세 (5개 탭)

**개요 탭**
- 통계 카드: 팀 예산, 내 키 수, 모델 수, 내 예산
- 예산 상세: 사용량/총량 프로그레스 바, 예산 주기, 초기화 일시
- 예산 변경 요청: 현재 예산 표시 + 변경 금액 입력 + 변경 후 미리보기

**내 키 탭**
- 키 테이블: 별칭 (`display_alias`), 마스킹된 키 (복사 버튼), 만료일, 모델 수, 생성일, 삭제
- 키 복사: reveal API 호출 후 클립보드에 복사 (sk- prefix 미노출)

**모델 탭**
- 카탈로그에 등록된 모델만 표시 (카탈로그 display_name 사용)
- 컬럼: 모델명, 상태 배지, Input/Output 비용
- 모델 클릭 시 상세 시트

**멤버 탭** (관리자에게만 표시)
- 멤버 목록: 검색, 50건 페이지네이션
- 컬럼: 사용자 ID, 역할 배지 (관리자/멤버), 키 수, 예산 사용 (TeamMembership 기준)
- 역할 변경: 확인 모달 -> 관리자 <-> 멤버 전환
- 예산 변경: 현재 예산 표시 + 변경 금액 입력 모달
- 멤버 삭제: 확인 후 제거
- 행 확장: 해당 멤버의 개별 키 목록 표시

**설정 탭** (관리자에게만 표시)
- 멤버 기본 예산: 신규 멤버 추가 시 자동 할당되는 예산 설정

#### 팀 탐색
- 팀명 검색 + 상태 필터 (전체/가입됨/미가입/요청중)
- 팀 카드: 팀명, 모델 수 (`all-proxy-models` -> "모든 모델"), 관리자 목록
- 가입 요청 모달: 메시지 입력 후 요청 전송
- 숨김 팀은 일반 유저에게 미표시
- API 응답에 예산/멤버 정보 미포함 (보안)

#### 내 전체 키
- 통계 카드: 전체 키 수, 소속 팀 수
- 검색 (별칭/키), 팀 필터 드롭다운
- 키 테이블: 별칭 (`display_alias`), 마스킹된 키 + 복사 버튼, 팀 링크, 생성일, 삭제
- 키 생성 버튼 -> 키 생성 페이지로 이동

#### 키 생성
- 팀 선택 (필수), 키 별칭 (필수)
- 사용 가능 모델 표시 (팀 설정 기준, 읽기 전용)
- TPM/RPM 제한 표시 (포털 설정 기준, 읽기 전용)
- 생성 완료 시 성공 모달: 키 표시 + 복사 버튼, 확인 시 이전 페이지로 복귀
- `sk-` prefix는 서버에서 제거 후 반환 (네트워크 미노출)

#### 내 요청
- 팀명 검색, 상태 탭 (전체/대기중/승인/거절)
- 요청 테이블 (20건 페이지네이션): 유형 배지, 팀, 내용 (truncate + 클릭 모달), 상태, 요청일, 처리 코멘트
- 상세 모달: 유형, 팀, 상태, 요청일, 변경 금액 (예산), 요청 내용, 처리 코멘트

### 관리자 (Super User)

#### 요청 관리
- 검색 (요청자/팀명), 유형 탭 (전체/팀 가입/예산 변경), 상태 탭
- 요청 테이블 (20건 페이지네이션): 유형, 요청자, 팀, 내용, 상태, 요청일, 처리 (승인/거절)
- 승인/거절 확인 모달: 코멘트 입력
- 상세 모달: 요청자, 팀, 변경 금액, 요청 내용, 처리 코멘트
- 예산 변경 승인 시 BudgetTable에 예산 생성/연결

#### 관리자 대시보드
- 모델 대시보드와 동일 구조이나 숨김 모델 포함 전체 모델 표시
- 통계, 상태 분포, 최근 변경, 모델 테이블

#### 모델 관리
- 카탈로그 등록/수정/삭제
- 검색, 상태 필터, 50건 페이지네이션
- 등록/수정 모달: 모델명 (datalist 자동완성), Display Name, 설명, 상태, 상태별 일정, 가시성
- 상태 변경 이력: 모든 변경이 기록되어 모델 상세 시트에서 조회
- 상태 흐름: Testing -> Prerelease -> LTS -> Deprecating -> Deprecated

#### 모델 캐시 관리
- Redis Hash Set 기반 모델 캐시 CRUD
- 카탈로그 선택기 (chat, embeddings 등)
- 검색 (Display Name / Model / API Base)
- 항목 테이블: Display Name, Model, API Base, API Key 상태, Options (JSON)
- 등록/수정 모달: Display Name, Model, API Base, API Key, Options (JSON)
- 카탈로그 설정: 카탈로그 목록 관리
- 일반/클러스터 Redis 모두 지원, 모델 카탈로그와 독립 운영

#### 예산 관리
- 통계 카드: 전체 예산 수, 미연결 예산 수 (정리 버튼)
- 검색, 금액 필터, 미연결만 토글
- 일괄 선택/삭제 (체크박스)
- 예산 테이블 (50건 페이지네이션, 확장 가능)
  - 컬럼: Budget ID, 최대 한도, 주기, 초기화일, 멤버십/키/조직 수
  - 확장 시: 연결된 멤버십, API 키, 조직, 프로젝트 목록

#### 포털 설정
- **API 키 기본 제한**: TPM/RPM 기본값 설정
- **신규 유저 자동 등록**:
  - 기본 팀 ID (모든 유저, 쉼표로 여러 팀 지원)
  - 추가 팀 규칙 (사번 prefix 기반): 기본 팀 + 규칙 팀 합산 배정
- **팀 숨기기**: 일반 유저에게 비노출할 팀 ID 관리

### 공통 기능

- **유저 ID**: 내부적으로 대문자 (`.upper()`) 사용, Keycloak은 소문자
- **키 형식**: sk-JWT (HS256 서명), `sk-` prefix는 사용자/네트워크에 미노출
- **키 별칭**: `metadata.display_alias`에 저장, LiteLLM 내부 `key_alias`와 분리
- **숨김 팀**: 팀 목록, 팀 탐색, 키 목록에서 일반 유저에게 미표시
- **모델 가시성**: `visible=false` 모델은 API 응답, 대시보드, 이력에서 일반 유저에게 미표시

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
