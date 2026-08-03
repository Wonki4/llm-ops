# Budget & Usage Self-Service MCP — Design

**Date:** 2026-08-03

## Context

Users (and their AI agents) want to ask *"how much of my budget is left in team X? how much have I spent?"* without opening the portal. The portal already holds all the data (LiteLLM budget + spend tables) and already encodes the caller's identity inside the `sk-<jwt>` virtual key. This design adds a **self-service MCP server** hosted in the portal that answers those questions, scoped strictly to the **calling user's own in-team budget and spend**.

## Goal

An MCP server, mounted in the portal backend, exposing two tools that let a caller — identified by their LiteLLM `sk-` key — see **their own** budget and spend within a team they belong to.

## Non-goals

- **No team-wide totals.** Only the caller's own in-team budget/spend (never the team's aggregate).
- No usage trends, per-model, or per-tag breakdowns (possible later).
- No admin / cross-user views.
- **No changes to LiteLLM** (fork/submodule). Pure portal addition.

## Architecture

- A new MCP server mounted into the portal FastAPI app, exposed over **streamable HTTP**, at a portal route (e.g. `POST /mcp` — exact path decided in the plan).
- **Transport implementation:** prefer the official MCP Python SDK's streamable-HTTP server mounted as an ASGI sub-app. Adding the `mcp` pip package is baked into the portal image (not a runtime/CDN fetch), so it is air-gap safe. If we want zero new dependencies, the fallback is a minimal hand-rolled JSON-RPC endpoint implementing `initialize` / `tools/list` / `tools/call` — decided in the plan.
- Reuses existing portal helpers and DB sessions (`get_litellm_db`, `get_db`); runs in-process with no external calls beyond the portal's own databases.

## Authentication & identity

- The caller passes their LiteLLM key as `Authorization: Bearer sk-<jwt>`.
- The server **decodes the JWT locally** with `_KEY_JWT_SECRET` (HS256) — the same secret that mints it in `keys.py:_generate_sk_jwt`. The `sub` claim is a JSON string:
  ```json
  { "keyId": <int>, "prjId": "<team_id>", "keyType": "PRJ", "regUserId": "<user_id>", "iat": <ts> }
  ```
- `regUserId` is the caller's identity (사번 = portal `user_id`). No Keycloak session is required for the agent.
- A missing, malformed, unsigned, or expired token → MCP "unauthenticated" error; no data is returned.

## Tools

### 1. `list_my_teams()`

- **Input:** none.
- **Behavior:** returns the teams the caller (`regUserId`) belongs to, applying the **same visibility rules as the portal "my teams" view** (`teams.py:list_my_teams`): for a non-super user, teams in the `strict` hidden set (`_get_hidden_team_settings`) are excluded; discovery-hidden teams remain visible. Super users see all their teams.
- **Output:**
  ```json
  { "teams": [ { "team_id": "...", "team_alias": "..." } ] }
  ```
- **Why:** `team_id` is a required argument to the budget tool, so the agent needs a way to discover valid ids.

### 2. `get_team_budget_usage(team_id: str)`

- **Input:** `team_id` (string).
- **Authorization:** `team_id` must be in the caller's **visible team set** (member of it AND not strict-hidden for a non-super user). Otherwise → error. Do not leak whether a non-visible team exists.
- **Behavior — the caller's OWN in-team budget & spend:**
  - **budget:** the member's effective in-team budget row — their dedicated `LiteLLM_TeamMembership.budget_id` row when set, else the team default (`metadata.team_member_budget_id`). Fields: `max_budget`, `budget_duration`, `budget_reset_at`. This is `resolve_effective_budget` extended to also surface duration/reset (see Data sources).
  - **spend:** `LiteLLM_TeamMembership.spend` for `(regUserId, team_id)`.
  - **remaining:** `max_budget - spend` when `max_budget` is not null; else null (unlimited).
- **Output:**
  ```json
  {
    "team_id": "...", "team_alias": "...",
    "max_budget": 20.0,
    "spend": 7.3,
    "remaining": 12.7,
    "budget_duration": "30d",
    "reset_at": "2026-09-01T00:00:00Z"
  }
  ```
  `max_budget` / `remaining` / `reset_at` may be null (unlimited / no reset configured).

## Data sources (all already exist)

- **`resolve_effective_budget(litellm_db, team_id, user_id)`** (`services/member_budget_boost.py`) — the member's effective max_budget (dedicated row → team default). Extend (or add a sibling reader) to also return `budget_duration` and `budget_reset_at` from the same row.
- **`LiteLLM_TeamMembership.spend`** — the member's in-team spend. It is the *same scope* as the budget above (both are the team_member budget), so `remaining = budget - spend` is exact. It is reset by the existing `jobs/reset_team_membership_budget.py` cron.
- **`_get_hidden_team_settings(db)`** (`api/teams.py`) — returns `(discovery_hidden, strict_hidden)` sets.
- **The portal `list_my_teams` query** (`api/teams.py:173`) — teams where the user is a member (via `LiteLLM_UserTable.teams`), plus the strict-hidden filter — reused by both tools.

**Why not `LiteLLM_DailyUserSpend`:** that table has no `team_id` and is attributed to teams via api-key → VerificationToken (a key-based analytics view). It is a *different scope* from the member's team_member budget counter, so it would not line up with `max_budget`. The enforcement counter (`TeamMembership.spend`) is the correct pairing.

## Error handling

- Missing/invalid bearer token → MCP unauthenticated error.
- `team_id` not in the caller's visible set → MCP error (forbidden / not a member), without revealing existence.
- No membership row / no budget configured → `max_budget` null (unlimited), `spend` 0, `remaining` null.

## Testing strategy

- **Token decode (unit):** valid `sk-<jwt>` → `regUserId`/`prjId`; tampered signature / expired / non-`sk-` / malformed `sub` → error.
- **`get_team_budget_usage` (unit):** member with a dedicated budget, member on the team default, member with no budget (unlimited); non-member and strict-hidden team → forbidden; `remaining` math incl. null budget.
- **`list_my_teams` (unit):** excludes strict-hidden for a non-super user, includes discovery-hidden, super user sees all.
- **MCP protocol (integration):** `tools/list` returns both tools with their schemas; `tools/call` returns the shapes above; bad auth is rejected before any tool runs.

## Open questions (defaults chosen)

- **Mount path** and MCP transport lib (official SDK vs hand-rolled JSON-RPC) — decided in the plan; default is official SDK at `/mcp`.
- **Key `prjId` vs `team_id` param:** `team_id` is a free parameter authorized by membership (not forced to equal the key's own `prjId`), so an agent can query any team the user belongs to. Default: free param + membership/visibility check.
