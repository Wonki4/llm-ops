# Portal Admin MCP (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Super users connect Claude Code to the portal backend's `/mcp` endpoint by logging in through Keycloak (OAuth authorization code + PKCE), and get read-only tools over clusters, GPU profiles, storages, recipes and deployments.

**Architecture:** An MCP SDK 2.x `MCPServer` runs in resource-server mode inside the backend process. It uses a Keycloak token verifier (`aud` + `azp` checks) and stateless streamable HTTP. A small ASGI path-dispatch middleware routes `/mcp` and its RFC 9728 metadata path to the SDK app, and leaves every other request on FastAPI. Tools call the existing REST route functions directly, passing `user=` and `db=`, so the tools and the REST API return the same data shapes. The portal REST Bearer path rejects tokens issued to the MCP client.

**Tech Stack:** FastAPI, Starlette, MCP Python SDK `mcp>=2.0,<3`, python-jose (existing `verify_token`), SQLAlchemy async, pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-11-04-portal-admin-mcp-design.md`

## Global Constraints

- **Air-gapped.** No runtime fetches. `mcp` is installed into the backend image at build time through `backend/pyproject.toml` (`pip install -e .` in the Dockerfile).
- **Super users only.** The check is `CustomUser.global_role == GlobalRole.SUPER_USER` after `upsert_portal_user`, the same check as `require_super_user`.
- **Fixed error messages:**
  - Non-super user: `"super user only"`.
  - `IdentityMismatchError`: `"계정 확인이 필요합니다. 관리자에게 문의하세요."`, the same text as `deps.get_current_user`.
- **Settings defaults:**
  - `mcp_enabled=False`
  - `mcp_client_id="portal-mcp"`
  - `mcp_token_audience="portal-mcp"`
  - `mcp_public_url=""`
- **The Keycloak client is the operator's choice.** Code never names a client. The local realm files get a sample `portal-mcp` client for development only.
- **No offline tokens.** The sample client gets `optionalClientScopes: []`.
- **No token passthrough.** The MCP never forwards the token it received.
- **Callback.** Fixed port 8765, redirect URI `http://localhost:8765/callback`.
- **Running tests:** run from `backend/` with the main checkout's venv: `/Users/wongibaek/Documents/litellm-ops/backend/.venv/bin/pytest`. The suite has about 21 baseline failures that need Postgres. Ignore those and list any new failures by name.
- **Commits:** follow the commit steps in each task. Push and PR only when the user asks.

## Review Focus

1. **Real Keycloak token shape.** The token has `aud: ["litellm-portal", "portal-mcp"]` and `azp: "portal-mcp"`. The portal REST API must reject it (401), and the MCP must accept it. Task 1 and Task 2 test this.
2. **First-time user via MCP who is not a super user.** `upsert_portal_user` creates the user as USER, and every tool returns `"super user only"`. Nothing else leaks. Task 3 tests this.
3. **Expired token mid-session.** The verifier sets `expires_at=exp`, so the SDK answers 401 and Claude Code refreshes. Task 2 tests this.
4. **Multiple backend replicas.** In stateless mode, a request without an `mcp-session-id` header still succeeds. Task 4 tests this.
5. **Existing API unaffected when MCP is enabled.** An unknown path still returns FastAPI's JSON `{"detail": "Not Found"}`, and a wrong method on an existing route still returns 405. Task 4 tests this.

---

### Task 1: Settings + portal REST rejects MCP-client tokens

**Files:**
- Modify: `backend/app/config.py` (add 4 settings next to the `gateway_*` block)
- Modify: `backend/app/auth/deps.py` (`get_current_token`, Bearer branch only)
- Test: `backend/tests/test_mcp_rest_separation.py`

**Interfaces:**
- Produces:
  - `settings.mcp_enabled: bool`
  - `settings.mcp_client_id: str`
  - `settings.mcp_token_audience: str`
  - `settings.mcp_public_url: str`

- [ ] **Step 1: Write the failing tests.** Call `get_current_token(request, response, credentials)` directly. Monkeypatch these:
  - `app.auth.deps.refresh_session_if_needed` → `AsyncMock(return_value=None)`
  - `app.auth.deps.verify_token` → returns a `TokenPayload`

  ```python
  async def test_bearer_token_issued_to_mcp_client_is_rejected(monkeypatch): ...
      # TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp")
      # credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="x")
      # with pytest.raises(HTTPException) as e: ...; assert e.value.status_code == 401

  async def test_bearer_token_from_other_client_still_accepted(monkeypatch): ...
      # azp="litellm-portal" -> returns the payload unchanged

  async def test_session_cookie_token_is_not_subject_to_the_mcp_rule(monkeypatch): ...
      # refresh_session_if_needed returns SessionData-like obj with access_token="cookie";
      # verify_token returns azp="portal-mcp" -> still returned (rule applies to Bearer only)
  ```

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `cd backend && /Users/wongibaek/Documents/litellm-ops/backend/.venv/bin/pytest tests/test_mcp_rest_separation.py -v`
  Expected: the first test FAILS (no exception raised). The other two pass.

- [ ] **Step 3: Implement.**
  - In `config.py`, add the four settings with the defaults from Global Constraints.
  - In the `get_current_token` Bearer branch, after `verify_token`: if `settings.mcp_client_id` is set and `token.azp == settings.mcp_client_id`, raise `HTTPException(401, detail="MCP tokens cannot call the portal API")`.

- [ ] **Step 4: Run the tests and confirm they pass.** Same command. Expected: 3 passed.

- [ ] **Step 5: Run the full suite** (`pytest -q > /tmp/... ; tail`). Expected: only the known Postgres baseline failures.

- [ ] **Step 6: Commit**
  ```bash
  git add backend/app/config.py backend/app/auth/deps.py backend/tests/test_mcp_rest_separation.py
  git commit -m "feat(auth): portal REST rejects Bearer tokens issued to the MCP client"
  ```

### Task 2: MCP token verifier and caller identity

**Files:**
- Modify: `backend/pyproject.toml` (add `"mcp>=2.0,<3"` to `dependencies`)
- Create: `backend/app/mcp/__init__.py` (empty)
- Create: `backend/app/mcp/auth.py`
- Test: `backend/tests/test_mcp_auth.py`

**Interfaces:**
- Consumes:
  - the Task 1 settings
  - `app.auth.jwt.verify_token(token, audience=...) -> TokenPayload`, which raises `ValueError`
  - `app.services.user_provisioning.upsert_portal_user(db, token) -> CustomUser`
- Produces:
  - `class McpTokenVerifier`, with `async def verify_token(self, token: str) -> AccessToken | None`. This satisfies the SDK `TokenVerifier` protocol.
  - `def current_token_payload() -> TokenPayload`. It reads `mcp.server.auth.middleware.auth_context.get_access_token()` and rebuilds `TokenPayload(**access_token.claims)`. It raises `ToolError("unauthenticated")` when there is no token.
  - `async def require_mcp_super_user(db: AsyncSession) -> CustomUser`. It raises `ToolError` with the fixed messages.

- [ ] **Step 0: Install the dependency.** Add the dependency line to `pyproject.toml`, then run `/Users/wongibaek/Documents/litellm-ops/backend/.venv/bin/pip install "mcp>=2.0,<3"`.
  Expected: `python -c "import mcp.server.mcpserver"` succeeds.

- [ ] **Step 1: Write the failing tests.** Monkeypatch `app.mcp.auth.verify_token` and `app.mcp.auth.upsert_portal_user`.

  ```python
  async def test_valid_mcp_token_maps_to_access_token(monkeypatch):
      # verify_token returns TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp", exp=1_900_000_000)
      # result = await McpTokenVerifier().verify_token("t")
      # assert result.client_id == "portal-mcp" and result.expires_at == 1_900_000_000
      # assert result.claims["preferred_username"] == "admin001"
      # and the fake saw audience == settings.mcp_token_audience

  async def test_token_for_other_client_is_rejected(monkeypatch):     # azp="litellm-portal" -> None
  async def test_invalid_token_is_rejected(monkeypatch):              # verify_token raises ValueError -> None
  async def test_realistic_dual_audience_token_is_accepted(monkeypatch):
      # payload as Keycloak issues it for a user with a litellm-portal client role:
      # azp="portal-mcp" (aud list is checked inside verify_token) -> AccessToken

  async def test_super_user_passes(monkeypatch):       # set auth_context_var, upsert returns SUPER_USER user -> returned
  async def test_regular_user_gets_super_user_only(monkeypatch):    # ToolError("super user only")
  async def test_identity_mismatch_message(monkeypatch):            # upsert raises IdentityMismatchError -> ToolError(<fixed text>)
  async def test_no_token_in_context_is_unauthenticated():          # ToolError("unauthenticated")
  ```

  To put a token in the context, set `auth_context_var` (from `mcp.server.auth.middleware.auth_context`) to `AuthenticatedUser(AccessToken(...))`, and reset it in `finally`.

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `pytest tests/test_mcp_auth.py -v`
  Expected: errors with `ModuleNotFoundError: app.mcp.auth`.

- [ ] **Step 3: Implement `backend/app/mcp/auth.py`.**
  - The verifier calls `verify_token(token, audience=settings.mcp_token_audience)`. On `ValueError` it returns `None`.
  - If `azp != settings.mcp_client_id`, it returns `None`.
  - Otherwise it returns `AccessToken(token=token, client_id=payload.azp, scopes=[], expires_at=payload.exp, subject=payload.sub, claims=dataclasses.asdict(payload))`.

- [ ] **Step 4: Run the tests and confirm they pass.** Same command. Expected: 8 passed.

- [ ] **Step 5: Commit**
  ```bash
  git add backend/pyproject.toml backend/app/mcp/__init__.py backend/app/mcp/auth.py backend/tests/test_mcp_auth.py
  git commit -m "feat(mcp): Keycloak token verifier and super-user gate for the portal MCP"
  ```

### Task 3: Read-only tools

**Files:**
- Create: `backend/app/mcp/tools.py`
- Test: `backend/tests/test_mcp_tools.py`

**Interfaces:**
- Consumes:
  - `require_mcp_super_user` (Task 2)
  - `app.db.session.async_session_factory`
  - these route functions, imported as modules to avoid name clashes:
    - `k8s_clusters.list_clusters(user, db)`
    - `gpu_profiles.list_gpu_types(user, db)`
    - `gpu_profiles.list_gpu_profiles(cluster_id, user, db)`
    - `storages.list_storages(kind, q, user, db)`
    - `storages.get_storage(storage_id, user, db)`
    - `serving_recipes.list_recipes(user, db)`
    - `serving_recipes.get_recipe(recipe_id, user, db)`
    - `model_deployments.list_deployments(user, db)`
    - `model_deployments.get_deployment(deployment_id, user, db)`
    - `model_deployments.list_deployment_events(deployment_id, limit, user, db)`
    - `serving_overview.serving_overview(user, db)`
- Produces:
  - `def register_tools(server: MCPServer) -> None`. It registers 12 tools: `whoami`, `list_clusters`, `list_gpu_types`, `list_gpu_profiles(cluster_id)`, `list_storages()`, `get_storage(storage_id)`, `list_recipes`, `get_recipe(recipe_id)`, `list_deployments`, `get_deployment(deployment_id)`, `get_deployment_events(deployment_id, limit=100)`, and `serving_overview`.
  - Every tool except `whoami` goes through one helper: `async def _run(route_fn, **kwargs) -> dict`. The helper does the following:
    1. Opens `async_session_factory()` as `db`.
    2. Calls `require_mcp_super_user(db)`.
    3. Awaits `route_fn(**kwargs, user=user, db=db)`.
    4. Converts `HTTPException` to `ToolError(f"{status_code}: {detail}")`.
    5. Commits and returns the route's dict unchanged.
  - `whoami` returns `{"user_id": user.user_id, "global_role": user.global_role.value}`.

- [ ] **Step 1: Write the failing tests.** Patch `app.mcp.tools.async_session_factory` with a factory that yields the `mock_db` fixture as an async context manager, and patch `app.mcp.tools.require_mcp_super_user`.

  ```python
  async def test_registers_the_phase1_tools():
      # server = MCPServer("t"); register_tools(server)
      # names = {t.name for t in await server.list_tools()}
      # assert names == {"whoami","list_clusters","list_gpu_types","list_gpu_profiles","list_storages",
      #   "get_storage","list_recipes","get_recipe","list_deployments","get_deployment",
      #   "get_deployment_events","serving_overview"}

  async def test_list_recipes_equals_rest_route_output(mock_db, super_user, monkeypatch):
      # mock_db.execute returns rows of _recipe() (copy helper from test_serving_recipes)
      # tool_out = await tools.list_recipes()   (module-level async fn, not via the server)
      # rest_out = await serving_recipes.list_recipes(user=super_user, db=mock_db)
      # assert tool_out == rest_out

  async def test_get_recipe_missing_becomes_tool_error(...):
      # mock_db returns scalar None -> pytest.raises(ToolError, match="404: Recipe not found")

  async def test_non_super_user_gets_no_data(...):
      # require_mcp_super_user raises ToolError("super user only")
      # -> every tool raises it; mock_db.execute never awaited for route queries

  async def test_whoami_returns_identity(super_user, ...):
      # {"user_id": "admin001", "global_role": "super_user"}  (use GlobalRole.SUPER_USER.value)
  ```

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `pytest tests/test_mcp_tools.py -v`
  Expected: errors with `ModuleNotFoundError: app.mcp.tools`.

- [ ] **Step 3: Implement `backend/app/mcp/tools.py`.**
  - Each tool is a module-level async function, so tests can call it directly.
  - `register_tools` applies `server.tool(description=...)(fn)` to each one.
  - Write descriptions for an agent. Each one should say what the tool returns and which other tool supplies the id it needs. For example, "`cluster_id` comes from `list_clusters`".

- [ ] **Step 4: Run the tests and confirm they pass.** Same command. Expected: all pass.

- [ ] **Step 5: Commit**
  ```bash
  git add backend/app/mcp/tools.py backend/tests/test_mcp_tools.py
  git commit -m "feat(mcp): read-only serving tools mirroring the admin REST routes"
  ```

### Task 4: Server wiring into the backend app

**Files:**
- Create: `backend/app/mcp/server.py`
- Modify: `backend/app/main.py` (lifespan, and install after the routers)
- Test: `backend/tests/test_mcp_server.py`

**Interfaces:**
- Consumes:
  - `McpTokenVerifier` (Task 2)
  - `register_tools` (Task 3)
  - `settings.keycloak_issuer`
- Produces:
  - `def validate_mcp_settings(s: Settings) -> None`. It raises `RuntimeError` when `mcp_enabled` is set and any of the following is true:
    - `mcp_public_url` is empty, or its path is not exactly `/mcp`
    - `mcp_client_id` or `mcp_token_audience` is empty
    - `mcp_client_id == s.keycloak_client_id`
  - `def build_mcp_server() -> MCPServer`. It uses:
    - `token_verifier=McpTokenVerifier()`
    - `auth=AuthSettings(issuer_url=settings.keycloak_issuer, resource_server_url=settings.mcp_public_url)`
    - `register_tools`
    - short `instructions` saying the server is read-only, for portal super users
  - `class PathDispatch`, a pure ASGI middleware. Its constructor is `__init__(app, *, target, paths: frozenset[str])`. An `http` scope whose `scope["path"]` is in `paths` goes to `target`. Everything else, including `lifespan` scopes, goes to `app`.
  - `def install_mcp(app: FastAPI) -> MCPServer`. It does the following:
    1. Validates the settings.
    2. Builds the server.
    3. Creates `sdk_app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`.
    4. Calls `app.add_middleware(PathDispatch, target=sdk_app, paths=frozenset({"/mcp", <path of build_resource_metadata_url(mcp_public_url)>}))`.
    5. Returns the server.
  - In `main.py`: `mcp_server = install_mcp(app) if settings.mcp_enabled else None`, placed after all `include_router` calls. The lifespan runs `async with mcp_server.session_manager.run():` when `mcp_server` is set, and `contextlib.nullcontext()` otherwise.

- [ ] **Step 1: Write the failing tests.** Build a fresh `FastAPI()` app that has one dummy route (`GET /ping`). Monkeypatch `app.mcp.server.settings` fields to: `mcp_enabled=True`, `mcp_public_url="http://test/mcp"`, `keycloak_base_url="http://kc"`, `keycloak_realm="r"`. Call `install_mcp(test_app)` and use `AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")`.

  ```python
  def test_validate_rejects_missing_public_url(): ...        # RuntimeError
  def test_validate_rejects_portal_client_as_mcp_client(): ...  # mcp_client_id == keycloak_client_id -> RuntimeError
  def test_validate_rejects_public_url_not_ending_in_mcp(): ... # "http://x/other" -> RuntimeError
  def test_validate_noop_when_disabled(): ...

  async def test_protected_resource_metadata_points_at_keycloak():
      # GET /.well-known/oauth-protected-resource/mcp -> 200
      # body["resource"] == "http://test/mcp"; body["authorization_servers"] == ["http://kc/realms/r"]

  async def test_unauthenticated_mcp_call_gets_401_with_metadata_hint():
      # POST /mcp (json-rpc tools/list) without Authorization -> 401
      # 'resource_metadata="http://test/.well-known/oauth-protected-resource/mcp"' in resp.headers["www-authenticate"]

  async def test_authenticated_stateless_tools_list(monkeypatch):
      # patch McpTokenVerifier.verify_token to return an AccessToken for "good"
      # async with server.session_manager.run():
      #   POST /mcp initialize, then POST /mcp tools/list — neither sends mcp-session-id
      #   headers: Authorization: Bearer good, Accept: "application/json, text/event-stream"
      # tools/list result contains "list_recipes"

  async def test_other_paths_untouched():
      # GET /ping -> FastAPI route; GET /nope -> 404 {"detail": "Not Found"}; POST /ping -> 405

  async def test_disabled_by_default_mcp_is_404():
      # the real app.main.app (mcp_enabled defaults to False): POST /mcp -> 404
  ```

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run: `pytest tests/test_mcp_server.py -v`
  Expected: errors with `ModuleNotFoundError: app.mcp.server`.

- [ ] **Step 3: Implement `backend/app/mcp/server.py`, then wire it into `main.py`.**
  If SDK 2.x needs a protocol-version header, or rejects `tools/list` without a prior `initialize` in stateless mode, adjust the test's request headers to what the SDK requires and record it as a ruling. The behavior under test, a request with no session id succeeding, must not change.

- [ ] **Step 4: Run the tests and confirm they pass.** Same command, then the full suite.
  Expected:
  - all `test_mcp_*` pass
  - the full suite shows only the baseline failures
  - `test_health.py` passes, because `main.py` imports and works with `mcp_enabled=False`

- [ ] **Step 5: Commit**
  ```bash
  git add backend/app/mcp/server.py backend/app/main.py backend/tests/test_mcp_server.py
  git commit -m "feat(mcp): serve the portal MCP at /mcp with RFC 9728 metadata, stateless"
  ```

### Task 5: Keycloak sample client, deployment config, how-to doc

**Files:**
- Modify: `deploy/keycloak/litellm-realm.json`
- Modify: `deploy/helm/litellm-platform/files/litellm-realm.json`
- Modify: `docker-compose.yml` (backend `environment`, next to the `APP_GATEWAY_*` lines)
- Modify: `deploy/helm/litellm-platform/values.yaml` (backend env, next to `APP_GATEWAY_SSO_ENABLED`)
- Modify: `deploy/kustomize/overlays/{dev,stg,prd}/kustomization.yaml` (backend env, next to `APP_GATEWAY_*`)
- Create: `docs/portal-admin-mcp.md`
- Test: `backend/tests/test_realm_mcp_client.py`

**Interfaces:**
- Consumes: the setting names from Task 1.

- [ ] **Step 1: Write the failing test.** Parametrize it over both realm files and parse each with `json`.

  ```python
  @pytest.mark.parametrize("path", [REPO / "deploy/keycloak/litellm-realm.json",
                                    REPO / "deploy/helm/litellm-platform/files/litellm-realm.json"])
  def test_sample_mcp_client(path):
      c = next(c for c in json.loads(path.read_text())["clients"] if c["clientId"] == "portal-mcp")
      assert c["publicClient"] is True and c["standardFlowEnabled"] is True
      assert c["directAccessGrantsEnabled"] is False and c.get("implicitFlowEnabled") is False
      assert c["attributes"]["pkce.code.challenge.method"] == "S256"
      assert c["redirectUris"] == ["http://localhost:8765/callback"]
      assert c["optionalClientScopes"] == []                        # no offline_access
      assert {"basic", "profile", "roles"} <= set(c["defaultClientScopes"])
      aud = [m for m in c["protocolMappers"] if m["protocolMapper"] == "oidc-audience-mapper"]
      assert aud and aud[0]["config"]["included.custom.audience"] == "portal-mcp"
      assert any(m["name"] == "preferred_username_mapper" for m in c["protocolMappers"])
  ```

- [ ] **Step 2: Run the test and confirm it fails.**
  Run: `pytest tests/test_realm_mcp_client.py -v`
  Expected: FAIL with `StopIteration` (no such client).

- [ ] **Step 3: Implement.**
  - **Both realm files:** add the client. Set `fullScopeAllowed: true`, copy `preferred_username_mapper` from that file's `litellm-portal` client, and add the audience mapper with `access.token.claim: "true"` and `id.token.claim: "false"`.
  - **Compose:** add `APP_MCP_ENABLED: "${APP_MCP_ENABLED:-true}"` and `APP_MCP_PUBLIC_URL: "http://localhost:8002/mcp"`.
  - **Helm and kustomize:** add `APP_MCP_ENABLED=false`, `APP_MCP_PUBLIC_URL=` (empty), `APP_MCP_CLIENT_ID=portal-mcp` and `APP_MCP_TOKEN_AUDIENCE=portal-mcp`. Add a comment saying the operator picks the Keycloak client.
  - **`docs/portal-admin-mcp.md`** covers:
    - the client checklist from the spec's Keycloak section
    - the `claude mcp add` commands for public and confidential clients
    - `/mcp` and `claude mcp login portal [--no-browser]`
    - the tool list
    - the no-offline-token policy, with re-login once the SSO session ends

- [ ] **Step 4: Run the test and confirm it passes.**
  Run: `pytest tests/test_realm_mcp_client.py -v`, then `docker compose config -q` from the repo root.
  Expected: 2 passed. `docker compose config` exits 0. If Docker is unresponsive, record that it was skipped.

- [ ] **Step 5: Manual E2E. Run it only if Docker is up. Otherwise record it as not run.**
  1. Bring up the stack and add the server:
     `docker compose up -d --build keycloak backend` →
     `claude mcp add --transport http --client-id portal-mcp --callback-port 8765 portal http://localhost:8002/mcp` →
     log in with `/mcp` as `admin001`.
  2. Check the following:
     - `list_recipes` returns data.
     - A non-super user gets `"super user only"`.
     - The decoded refresh token has `typ` = `Refresh`.
     - Sending the MCP access token as Bearer to `GET http://localhost:8002/api/me` returns 401.

- [ ] **Step 6: Commit**
  ```bash
  git add deploy/keycloak/litellm-realm.json deploy/helm/litellm-platform/files/litellm-realm.json \
    docker-compose.yml deploy/helm/litellm-platform/values.yaml deploy/kustomize/overlays \
    docs/portal-admin-mcp.md backend/tests/test_realm_mcp_client.py
  git commit -m "feat(mcp): sample Keycloak client, deploy settings and how-to for the portal MCP"
  ```
