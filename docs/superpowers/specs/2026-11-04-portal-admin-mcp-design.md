# Portal Admin MCP — Phase 1: Keycloak OAuth + Read-Only Serving Tools — Design

**Date:** 2026-11-04
**Status:** Approved (user: "Claude Code에서 MCP 붙여서 레시피 만들고 배포까지"; super users only;
log in through Keycloak OAuth2; split into three phases, MCP foundation first; no offline
tokens; Claude Code runs as the VS Code extension or the plain CLI; mounted in the backend,
not the budget MCP; the operator picks the Keycloak client)

## Context

Operators (portal super users) want to drive serving work from Claude Code: describe a model
("Qwen3-32B on 2×H100, long context"), have the agent write a recipe, deploy it, check it came
up, validate it, benchmark it, and keep adjusting flags until performance stops improving.

Today that is all manual in the portal UI. An agent *could* call the REST API with a
super user's Keycloak access token pasted by hand, but there is no supported way to connect
an agent, and the agent has no discoverable tool surface.

The work is split into three phases, each usable on its own:

1. **This document — MCP foundation:** an MCP endpoint Claude Code logs into through Keycloak,
   usable only by super users, with **read-only** tools over the serving catalog so the agent
   can ground itself in what exists (clusters, storages, GPU profiles, recipes, deployments).
2. Recipe + deploy write tools: preview / save recipe, deploy, status, events, **pod logs**
   (no API exists today), re-apply.
3. Validation + benchmark loop: smoke request, run benchmark with fixed presets, compare runs,
   and guidance for the agent's tune-until-no-improvement loop.

Phases 2 and 3 get their own design documents. This one must leave a seam they plug into.

## Goals

- A super user connects Claude Code once:
  ```bash
  claude mcp add --transport http --client-id portal-mcp --callback-port 8765 \
    portal https://<backend-host>/mcp
  ```
  then `/mcp` (or `claude mcp login portal`) opens a Keycloak login in the browser
  (authorization code + PKCE). No portal key, password, or token in any config file.
  Claude Code stores and refreshes the token itself.
- Every tool call is authorized as **that user**; only super users get results.
- Read-only tools return the same data shapes the portal REST API returns for the same
  objects, so the agent sees what an operator sees in the UI.
- Off by default; enabled with one backend setting.

## Non-goals

- Any write/mutating tool (phase 2). Pod logs (phase 2). Benchmarks (phase 3).
- Non-super-user access, per-tool role granularity.
- Changing the existing budget MCP (`mcp/` service, `sk-` key auth). It stays as is.
- Keycloak Dynamic Client Registration, Client ID Metadata Documents, RFC 8707 resource
  indicators.
- A portal UI page for the MCP (connection instructions live in docs).

## Anti-goals (failure even if it "works")

- **Token passthrough:** the MCP must never forward the token it received to another service
  (MCP spec forbids it; confused-deputy risk).
- **Audience bleed:** a normal portal login token must not be accepted by the MCP, and an MCP
  token must not be accepted by the portal REST API.
- **Logic drift:** tool handlers must not re-implement queries/serialization that REST routes
  already do; the two must not be able to disagree about what a recipe or deployment looks like.
- A non-super user learning anything through the MCP beyond "super user only".

## Constraints

- Air-gapped: no runtime fetches. The `mcp` Python SDK is installed into the backend image at
  build time (the `mcp/` service already uses `mcp` 2.0.0).
- Backend is exposed on its own host (`ingress.backend.host`, path `/`), so `/mcp` and
  `/.well-known/...` on that host already reach the backend.
- Existing identity rules apply unchanged: user id = `preferred_username` upper-cased; user row
  upserted via `upsert_portal_user` (incl. `keycloak_sub_pinning`); super user =
  `CustomUser.global_role == SUPER_USER` (what `require_super_user` checks).

## Design (decided)

### Placement

The MCP server is **mounted inside the backend FastAPI app** at `/mcp` (streamable HTTP),
not added to the `mcp/` service. Reason: phase 2/3 tools must run the backend's own recipe
validators, manifest builders, K8s clients, and benchmark logic; the `mcp/` service imports
nothing from `backend/` by design, and calling the backend over HTTP would need token
passthrough or a second credential.

Built with the SDK's resource-server mode: `MCPServer(token_verifier=..., auth=AuthSettings(
issuer_url=<keycloak issuer>, resource_server_url=<public MCP URL>))`. The SDK answers
unauthenticated calls with `401` + `WWW-Authenticate` and serves OAuth Protected Resource
Metadata (RFC 9728) whose `authorization_servers` is the Keycloak realm issuer — this is what
Claude Code discovers before opening the browser.

### Settings (backend `Settings`, env prefix `APP_`)

- `mcp_enabled: bool = False` — when false nothing is mounted (404).
- `mcp_token_audience: str = "portal-mcp"` — required `aud` of MCP tokens.
- `mcp_client_id: str = "portal-mcp"` — the Keycloak client MCP tokens are issued to (`azp`).
- `mcp_public_url: str = ""` — the externally reachable MCP URL
  (e.g. `https://portal-api.example.com/mcp`), used as the RFC 9728 resource identifier.
  Required when `mcp_enabled`; startup fails loudly if missing.

### Token verification and identity

- Verifier: `verify_token(token, audience=settings.mcp_token_audience)` (the existing function
  in `app/auth/jwt.py`, which already takes `audience`; signature/issuer/expiry checks as for
  the portal). Invalid → verifier returns `None` → SDK sends `401` (Claude Code re-auths).
- Per tool call: resolve the portal user with `upsert_portal_user(db, token_payload)` — same
  as `get_current_user` — then require `global_role == SUPER_USER`. Not a super user → tool
  error `"super user only"` (not 401: the token is valid; a 401 would make Claude Code loop
  re-authenticating). `IdentityMismatchError` → tool error with the same message the REST API
  uses.
- **Separation is enforced in code, on `azp`, in both directions** (builder check: Keycloak's
  `roles` scope "audience resolve" mapper adds `litellm-portal` to `aud` for any user holding a
  `litellm-portal` client role, so an MCP token can carry *both* audiences — `aud` alone cannot
  separate them):
  - MCP verifier: `aud` must contain `mcp_token_audience` **and** `azp == mcp_client_id`.
  - Portal REST (`get_current_token`, Bearer branch): reject a token whose
    `azp == mcp_client_id` with the usual 401. Session-cookie logins are unaffected
    (their `azp` is `litellm-portal`). This is the only change to the existing auth path.
  - Consequence: the Keycloak client can keep full scope and the same role/group mappers as
    `litellm-portal`, so super-user detection works whichever signal prod uses (realm role,
    `litellm-portal` client role, or `admin_groups` group) — no prod-specific role setup.

### Keycloak

**Which Keycloak client the MCP uses is the operator's decision (user decision)** — an
existing client or a new one, public or confidential. The portal only takes its id
(`mcp_client_id`) and the audience it puts in tokens (`mcp_token_audience`) from settings;
nothing in code names a client. The one hard rule, checked at startup: `mcp_client_id` must
differ from the portal login client (`keycloak_client_id`), otherwise the `azp` separation
above would lock portal Bearer callers out. Startup fails loudly on a match.

Connecting from Claude Code:
- public client: `claude mcp add --transport http --client-id <id> --callback-port 8765 portal <url>`
- confidential client: the same plus `--client-secret` (Claude Code prompts for it and keeps it
  in the OS keychain).

Whatever client is chosen must satisfy (documented as a checklist for prod):
- Standard flow on, PKCE `S256`; implicit and direct access grants are not needed.
- Redirect URI `http://localhost:8765/callback` (fixed callback port; users pass
  `--callback-port 8765`). Users run Claude Code as the VS Code extension or the plain CLI,
  each on their own machine, so one fixed port is enough. The documented fallback when the
  browser cannot reach the callback (e.g. VS Code Remote-SSH without port forwarding) is
  `claude mcp login portal --no-browser` and pasting the callback URL back.
- **No offline tokens** (user decision). `offline_access` is removed from the client's
  optional scopes, so a login lives only as long as the Keycloak SSO session (local realm:
  2 h idle, 12 h max); after that the operator logs in through the browser again. Claude
  Code appends `offline_access` to its requested scopes when the authorization server
  advertises it (Keycloak's metadata does), so the E2E check below confirms the issued refresh
  token is a normal one (`typ: Refresh`), not `Offline`.
- An audience mapper adding `mcp_token_audience` to the access token (same pattern as the
  existing `litellm-portal-gw` client's `gateway-audience-mapper`).
- Tokens carry `sub` (account pinning), `preferred_username`, and the same roles/groups a
  portal login carries (`basic profile roles` scopes and the portal's `preferred_username` /
  groups mappers), so super-user detection and first-time provisioning match the portal.

Local development only: a sample public client `portal-mcp` meeting the checklist is added to
**both** realm files — `deploy/keycloak/litellm-realm.json` and the already-drifted
`deploy/helm/litellm-platform/files/litellm-realm.json` — so the E2E below runs out of the box.
The setting defaults (`mcp_client_id`/`mcp_token_audience` = `portal-mcp`) match it; prod
overrides them with the operator's chosen client.

### Phase 1 tools (all read-only)

Each tool returns exactly what the corresponding REST route returns, by calling the same
service/serializer the route uses (extract a shared function where the logic currently lives
inside the route body).

| tool | mirrors |
|---|---|
| `whoami()` | user id, role |
| `list_clusters()` | `GET /api/admin/k8s-clusters` |
| `list_gpu_types()` | `GET /api/admin/gpu-types` |
| `list_gpu_profiles(cluster_id)` | `GET /api/admin/k8s-clusters/{id}/gpu-profiles` |
| `list_storages()` / `get_storage(storage_id)` | `GET /api/admin/storages[/{id}]` |
| `list_recipes()` / `get_recipe(recipe_id)` | `GET /api/admin/serving-recipes[/{id}]` |
| `list_deployments()` / `get_deployment(deployment_id)` | `GET /api/model-deployments[/{id}]` |
| `get_deployment_events(deployment_id)` | `GET /api/model-deployments/{id}/events` |
| `serving_overview()` | `GET /api/admin/serving/overview` |

Tool descriptions are written for an agent: what the tool is for and which id it needs from
which other tool.

### Deployment wiring

- Backend dependency `mcp>=2.0,<3` in `backend/pyproject.toml`.
- `docker-compose.yml` backend env: `APP_MCP_ENABLED`, `APP_MCP_PUBLIC_URL=http://localhost:8002/mcp`.
- Helm `litellm-platform` backend env values for the same, **and** the kustomize overlays
  (prod runs `deploy/kustomize/overlays/prd`, backend replicas 3).
- The SDK runs in **stateless** streamable-HTTP mode so three backend replicas work without
  sticky sessions; its default localhost-only host check is turned off (the existing `mcp/`
  service does the same); the RFC 9728 metadata route
  (`/.well-known/oauth-protected-resource/mcp`) is served at the app root, not under `/mcp`.
- Tool outputs include recipe/deployment `env` values as the UI shows them (same-shape rule).
- `docs/` how-to: Keycloak client setup (prod), `claude mcp add ...`, `/mcp` login, what the
  tools are.

## Left to the builder

- Exact tool names/descriptions and argument names (keep ids as strings).
- How the SDK app is mounted into FastAPI and how its session manager joins the backend
  lifespan.
- Which REST route bodies need a shared helper extracted.
- Error text wording beyond the two fixed messages above.

## Verification

- Unit (`backend/tests/test_mcp_*.py`):
  - verifier: MCP-audience token accepted; portal-audience token rejected; expired / bad
    signature rejected.
  - super-user gate: non-super user gets `"super user only"` from every tool; super user gets data.
  - each tool's output equals the REST route's output for the same fixture.
  - Protected Resource Metadata: lists the Keycloak issuer, resource = `mcp_public_url`.
  - `mcp_enabled=False` → `/mcp` 404.
  - portal REST API rejects a **realistic** MCP token: `aud=["litellm-portal","portal-mcp"]`,
    `azp="portal-mcp"` (the shape Keycloak actually issues for a user with a portal client role).
  - MCP rejects a token with `aud` containing `portal-mcp` but another `azp`.
- Local E2E (compose): `claude mcp add --transport http --client-id portal-mcp
  --callback-port 8765 portal http://localhost:8002/mcp`, `/mcp` login as `admin001`, call
  `list_recipes`; log in as a non-super user and confirm `"super user only"`.
  - Decode the refresh token Keycloak issued to Claude Code: `typ` is `Refresh`, not `Offline`.
  - Call a portal REST route with the MCP access token as `Authorization: Bearer` → 401.
