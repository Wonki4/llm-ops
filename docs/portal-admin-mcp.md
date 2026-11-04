# 포털 관리자 MCP (Claude Code 연결)

포털 super user가 Claude Code(VS Code 확장 또는 CLI)에서 포털의 서빙 정보를 직접 조회할 수 있게
백엔드가 MCP 서버를 `/mcp`로 제공합니다. 로그인은 Keycloak OAuth(authorization code + PKCE)로
하고, 설정 파일에는 키·비밀번호·토큰을 넣지 않습니다. Claude Code가 토큰을 직접 보관하고(macOS는
키체인, Linux·Remote-SSH 등은 `~/.claude/.credentials.json`) 스스로 갱신합니다.

> 백엔드 Helm chart의 `ingress.mcp`(`portal-mcp.example.com`)는 별도 서비스인 예산 조회 MCP(`mcp/`)이고,
> 이 문서의 MCP와는 다릅니다. 이 MCP는 백엔드 호스트의 `/mcp`입니다.

- **super user만** 결과를 받습니다. 그 외 사용자는 모든 도구에서 `super user only`만 받습니다.
- 지금(1단계)은 **조회 전용**입니다. 레시피 작성·배포(2단계), 검증·벤치마크 루프(3단계)는 이후에 추가됩니다.
- 기본값은 꺼져 있습니다(`APP_MCP_ENABLED=false`).

## 1. Keycloak 클라이언트 준비 (운영)

어떤 클라이언트를 쓸지는 운영자가 정합니다(기존 클라이언트든 새 클라이언트든, public이든
confidential이든). 포털은 설정으로 받은 클라이언트 id와 audience만 확인합니다. 고른 클라이언트는
다음을 만족해야 합니다.

- [ ] **MCP 전용 클라이언트**일 것. 포털 REST API는 이 클라이언트로 발급된 토큰(`azp`)을 MCP 설정이 꺼져
      있어도 401로 거절합니다. 그래서 다른 앱이 이 클라이언트 토큰으로 포털 API를 부르고 있다면 그 앱이 깨집니다.
      특히 포털 로그인 클라이언트(`APP_KEYCLOAK_CLIENT_ID`, 기본 `litellm-portal`)와 같으면 백엔드가 시작하지 않습니다.
- [ ] Standard flow 켜기, PKCE `S256` 필수. Implicit flow, direct access grants는 필요 없습니다.
- [ ] Valid redirect URI: `http://localhost:8765/callback` (Claude Code를 `--callback-port 8765`로 고정).
- [ ] **offline token 금지**: Client scopes에서 `offline_access`를 optional scope에서 제거합니다.
      로그인은 Keycloak SSO 세션 동안만 유지되고, 세션이 끝나면 브라우저로 다시 로그인합니다.
- [ ] Audience 매퍼 추가: Mapper type `Audience`, Included Custom Audience = `APP_MCP_TOKEN_AUDIENCE`
      값(기본 `portal-mcp`), Add to access token 켜기.
- [ ] 토큰에 포털 로그인과 같은 정보가 실리게 하기: default client scopes에 `basic`(`sub`), `profile`,
      `roles`, Full scope allowed 켜기, 포털 클라이언트와 같은 `preferred_username` 매퍼, 그리고 포털에서
      super user 판정에 쓰는 신호(realm role, `litellm-portal` client role 매퍼, 또는 `APP_ADMIN_GROUPS`용
      groups 매퍼)를 똑같이 붙입니다.

로컬 realm 파일(`deploy/keycloak/litellm-realm.json`, `deploy/helm/litellm-platform/files/litellm-realm.json`)에는
이 조건을 만족하는 샘플 public 클라이언트 `portal-mcp`가 들어 있습니다. 운영 realm에는 자동으로
들어가지 않으니 위 체크리스트대로 직접 만들어야 합니다.

## 2. 백엔드 설정

| env | 기본값 | 설명 |
|---|---|---|
| `APP_MCP_ENABLED` | `false` | `true`일 때만 `/mcp`를 엽니다. |
| `APP_MCP_PUBLIC_URL` | (빈 값) | 외부에서 접근하는 MCP URL. 켤 때 필수이며 경로가 정확히 `/mcp`여야 합니다. 예: `https://portal-api.example.com/mcp` |
| `APP_MCP_CLIENT_ID` | `portal-mcp` | 위에서 고른 Keycloak 클라이언트 id(토큰의 `azp`). |
| `APP_MCP_TOKEN_AUDIENCE` | `portal-mcp` | 그 클라이언트의 audience 매퍼가 넣는 값(토큰의 `aud`). |

- kustomize: `deploy/kustomize/overlays/{dev,stg,prd}/kustomization.yaml`의 backend 설정.
- Helm: `deploy/helm/litellm-platform/values.yaml`의 `backend.env`.
- 백엔드 호스트(`ingress.backend.host`, 경로 `/`)로 들어오는 `/mcp`와
  `/.well-known/oauth-protected-resource/mcp`가 그대로 백엔드에 닿아야 합니다. 별도 ingress 설정은 필요 없습니다.
- 여러 replica에서 sticky session 없이 동작합니다(stateless).
- 설정이 잘못되면(공개 URL 누락, 경로가 `/mcp`가 아님 등) 백엔드가 시작할 때 바로 실패합니다.
  `APP_MCP_CLIENT_ID`가 포털 로그인 클라이언트와 같으면 MCP가 꺼져 있어도 시작하지 않습니다.

## 3. Claude Code 연결

각 super user가 자기 PC에서 한 번 등록합니다. VS Code 확장도 같은 설정을 씁니다.

```bash
# public 클라이언트
claude mcp add --transport http --client-id portal-mcp --callback-port 8765 \
  portal https://portal-api.example.com/mcp

# confidential 클라이언트: --client-secret 을 붙이면 시크릿을 물어보고 토큰과 같은 곳에 보관합니다
claude mcp add --transport http --client-id <client-id> --client-secret --callback-port 8765 \
  portal https://portal-api.example.com/mcp
```

모든 프로젝트에서 쓰려면 `--scope user`를 추가합니다.

로그인:

- Claude Code 안에서 `/mcp` → `portal` → 로그인. 브라우저에 Keycloak 로그인 화면이 열립니다.
- 터미널에서는 `claude mcp login portal`.
- 브라우저가 `localhost:8765`로 돌아오지 못하는 환경(예: 포트 포워딩 없는 VS Code Remote-SSH)에서는
  `claude mcp login portal --no-browser`로 URL을 받아 로그인한 뒤, 브라우저 주소창의 callback URL을
  붙여 넣습니다.

로컬(compose)에서는 compose가 MCP를 켜 둡니다(`APP_MCP_ENABLED` 기본 `true`).

```bash
# 백엔드 이미지에 mcp SDK와 새 코드를 넣으려면 --build 가 필요합니다
docker compose up -d --build backend
# Keycloak은 realm이 이미 있으면 import를 건너뜁니다. 샘플 클라이언트 portal-mcp를 넣으려면
# 컨테이너를 새로 만듭니다(계정 sub가 바뀌므로 로컬은 APP_KEYCLOAK_SUB_PINNING=off 가 기본입니다)
docker compose up -d --force-recreate keycloak

claude mcp add --transport http --client-id portal-mcp --callback-port 8765 \
  portal http://localhost:8002/mcp
```

그다음 `/mcp`에서 `admin001`로 로그인합니다.

## 4. 도구

모두 조회 전용이고, 포털 UI가 쓰는 REST API와 같은 데이터를 돌려줍니다.

| 도구 | 내용 |
|---|---|
| `whoami` | 이 세션이 어떤 포털 사용자로 동작하는지(사용자 id, 역할) |
| `list_clusters` | 배포 대상 Kubernetes 클러스터 (kubeconfig는 포함하지 않음) |
| `list_gpu_types` | GPU 종류별로 제공하는 클러스터, resource key, VRAM |
| `list_gpu_profiles(cluster_id)` | 한 클러스터의 GPU 프로필 |
| `list_storages` / `get_storage(storage_id)` | 모델 스토리지와 클러스터별 바인딩, 사용처 |
| `list_recipes` / `get_recipe(recipe_id)` | 서빙 레시피 (모델 경로, 이미지, 엔진 인자, GPU, env 등) |
| `list_deployments` / `get_deployment(deployment_id)` | 모델 배포와 상태, llm-d 스택 |
| `get_deployment_events(deployment_id, limit)` | 배포의 상태 이벤트 (최신순, 최대 500) |
| `serving_overview` | 모델별 레시피 → 배포 → 벤치마크 → 카탈로그 진행 현황 |

## 5. 토큰과 보안

- MCP 토큰은 MCP에서만 받습니다. 같은 토큰을 포털 REST API에 `Authorization: Bearer`로 보내면 401입니다.
  반대로 포털 로그인 토큰은 MCP에서 401입니다. 구분은 `aud`와 `azp`(발급 클라이언트)로 합니다.
- MCP는 받은 토큰을 다른 서비스로 전달하지 않습니다. 도구는 백엔드 안에서 그 사용자로 실행됩니다.
- offline token을 쓰지 않으므로 SSO 세션이 끝나면(로컬 realm 기준 2시간 미사용 또는 최대 12시간)
  다시 로그인해야 합니다. Claude Code는 401을 받으면 재로그인을 안내합니다.

## 6. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 로그인 후에도 계속 401 | 클라이언트의 audience 매퍼 값이 `APP_MCP_TOKEN_AUDIENCE`와 같은지, `APP_MCP_CLIENT_ID`가 그 클라이언트 id인지, `APP_KEYCLOAK_BASE_URL`이 토큰의 `iss`와 같은 주소인지 |
| `super user only` | 그 사용자가 포털에서 super user인지(포털 로그인 시 판정과 같은 role/group 신호가 토큰에 실리는지) |
| `계정 확인이 필요합니다. 관리자에게 문의하세요.` | 사용자 id가 다른 Keycloak 계정에 고정되어 있음(`APP_KEYCLOAK_SUB_PINNING`) |
| Keycloak `Invalid parameter: redirect_uri` | 클라이언트 redirect URI가 `http://localhost:8765/callback`인지, `--callback-port 8765`로 등록했는지 |
| Claude Code가 `Protected resource ... does not match ...` 오류 | `APP_MCP_PUBLIC_URL`이 `claude mcp add`에 넣은 URL과 정확히 같은지(스킴·호스트·포트·`/mcp`) |
| Keycloak `Client not found` (로컬) | Keycloak 컨테이너를 `--force-recreate`로 새로 만들었는지 |
| 로그인 창이 다시 뜸 | SSO 세션 만료(정상 동작) |
