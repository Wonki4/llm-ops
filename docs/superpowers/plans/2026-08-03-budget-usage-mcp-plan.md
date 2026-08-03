# Budget & Usage Self-Service MCP — Implementation Plan

> **For agentic workers:** Execute task-by-task; each task ends with a runnable/tested deliverable. Steps use checkbox syntax.

**Goal:** A standalone MCP server (separate from the backend) that lets an agent, authenticated by its `sk-<jwt>` key, read the caller's own in-team budget and spend.

**Architecture:** New top-level `mcp/` service mirroring `gateway/` (own `app/`, `Dockerfile`, `pyproject.toml`, `tests/`). Official **MCP Python SDK (FastMCP)** over **streamable HTTP**, served by uvicorn. Connects directly to the same Postgres DBs (portal + LiteLLM) with its own async engines. No imports from `backend/` (fully decoupled) — the few needed pieces (JWT decode, 3 SQL reads) are small and re-implemented locally.

**Spec:** `docs/superpowers/specs/2026-08-03-budget-usage-mcp-design.md`

## Global Constraints

- Identity from the `sk-<jwt>` key only: strip `sk-`, decode with `jose` HS256 and secret `litellm-portal-key-sign` (config `APP_KEY_JWT_SECRET`, same default), parse the `sub` JSON → `regUserId` (user_id) + `prjId` (team_id). No `exp`/`aud` in the token → disable aud verification.
- Data scope is the **caller's own** in-team budget/spend — never team-wide totals.
- Budget = the member's effective team_member budget row (dedicated `LiteLLM_TeamMembership.budget_id`, else team default `metadata.team_member_budget_id`). Spend = `LiteLLM_TeamMembership.spend`. `remaining = max_budget - spend` (null when max_budget null).
- Visibility: reuse the portal rule — a non-super user cannot see `hidden_teams_strict` teams (from `custom_portal_settings`); discovery-hidden stay visible. Applied to BOTH tools.
- Env prefix `APP_`; settings `database_url`, `litellm_database_url` (empty → fall back to `database_url`), `key_jwt_secret`, `debug`. Ruff line-length 120, `asyncio_mode = auto`.

---

## Task 1: Service skeleton

**Files (create):** `mcp/pyproject.toml`, `mcp/Dockerfile`, `mcp/app/__init__.py`, `mcp/app/config.py`, `mcp/app/db.py`, `mcp/tests/__init__.py`

**Details:**
- `pyproject.toml` — mirror `gateway/pyproject.toml` (hatchling, py311, ruff line-length 120, pytest asyncio auto). Dependencies: `mcp>=1.2.0`, `uvicorn[standard]>=0.32.0`, `sqlalchemy[asyncio]>=2.0.0`, `asyncpg>=0.29.0`, `python-jose>=3.3.0`, `pydantic>=2.10.0`, `pydantic-settings>=2.6.0`. Dev extras: `pytest`, `pytest-asyncio`, `ruff`.
- `Dockerfile` — copy of `gateway/Dockerfile` but `EXPOSE 8000` and `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- `config.py` — `Settings(BaseSettings)` with `database_url`, `litellm_database_url: str = ""`, `key_jwt_secret: str = "litellm-portal-key-sign"`, `debug: bool = False`, `model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}`.
- `db.py` — two async engines/session factories exactly like `backend/app/db/session.py` (`async_session_factory` for portal, `litellm_session_factory` for `litellm_database_url or database_url`), plus `get_db()` / `get_litellm_db()` async context helpers (plain `async with`, no FastAPI).

- [ ] Create the six files. Verify `python -c "import app.config, app.db"` (or a trivial import test) works.

## Task 2: Identity from the key JWT

**Files:** Create `mcp/app/identity.py`, `mcp/tests/test_identity.py`

**Interface (produces):**
```python
class Identity(BaseModel):
    user_id: str      # regUserId
    key_team_id: str  # prjId (the key's own team)

def identity_from_key(token: str, secret: str) -> Identity  # raises ValueError on any failure
```

- [ ] **Step 1 — failing test** (`test_identity.py`): mint a token exactly like the backend
  ```python
  import json
  from jose import jwt
  SECRET = "litellm-portal-key-sign"
  def _key(user_id="E12345", team_id="team-1"):
      sub = json.dumps({"keyId": 10001, "prjId": team_id, "keyType": "PRJ",
                        "regUserId": user_id, "iat": 1}, separators=(",", ":"))
      return "sk-" + jwt.encode({"sub": sub}, SECRET, algorithm="HS256")
  def test_decodes_reguserid_and_team():
      ident = identity_from_key(_key(), SECRET)
      assert ident.user_id == "E12345" and ident.key_team_id == "team-1"
  def test_rejects_tampered():
      import pytest
      with pytest.raises(ValueError):
          identity_from_key(_key()[:-2] + "xx", SECRET)
  def test_rejects_non_sk():
      import pytest
      with pytest.raises(ValueError):
          identity_from_key("not-a-key", SECRET)
  ```
- [ ] **Step 2 — run, expect fail** (`identity.py` missing).
- [ ] **Step 3 — implement** `identity.py`: strip `sk-` prefix (ValueError if absent); `jwt.decode(raw, secret, algorithms=["HS256"], options={"verify_aud": False})`; `json.loads(payload["sub"])`; pull `regUserId`/`prjId` (ValueError if missing). Wrap `JWTError`/`KeyError`/`JSONDecodeError` → `ValueError`.
- [ ] **Step 4 — run, expect pass. Step 5 — commit.**

## Task 3: Budget/usage data readers

**Files:** Create `mcp/app/data.py`, `mcp/tests/test_data.py`

**Interface (produces):**
```python
async def list_visible_teams(portal_db, litellm_db, user_id: str) -> list[dict]   # [{team_id, team_alias}]
async def member_budget_usage(litellm_db, team_id: str, user_id: str) -> dict | None  # None if not a member
```
- `list_visible_teams`: SELECT teams where `user_id = ANY(...)` via `LiteLLM_UserTable.teams` (mirror `teams.py:list_my_teams` query, columns team_id+team_alias); then read `hidden_teams_strict` from `custom_portal_settings` (portal_db) and drop those ids. (Super-user elevation is out of scope here — the MCP caller is always treated as a member; note in a comment.)
- `member_budget_usage`: verify membership (row in `LiteLLM_TeamMembership` for user+team; else return `None`). Read `TeamMembership.budget_id`, `spend`. Resolve budget row: `budget_id` → else team default (`LiteLLM_TeamTable.metadata->>'team_member_budget_id'`). Read `max_budget`, `budget_duration`, `budget_reset_at`. Return `{team_id, team_alias, max_budget, spend, remaining, budget_duration, reset_at}` (remaining null when max_budget null).

- [ ] **Step 1 — failing tests** using a fake async session whose `.execute()` returns queued mapping/scalar results (pattern like `backend/tests`), covering: member with dedicated budget, member on team default, member with no budget (max_budget null → remaining null), non-member → None, and strict-hidden filtering in `list_visible_teams`.
- [ ] **Step 2 run→fail; Step 3 implement `data.py`; Step 4 run→pass; Step 5 commit.**

## Task 4: MCP server + tools + auth

**Files:** Create `mcp/app/main.py`, `mcp/tests/test_tools.py`

**Details:**
- Build a `FastMCP` server exposing two tools. Auth: a Starlette middleware on the streamable-HTTP ASGI app reads `Authorization: Bearer <sk-...>`, calls `identity_from_key`, and stores the `Identity` in a `ContextVar` (401 on failure). Tools read the ContextVar to get `user_id`.
  - `list_my_teams()` → `{"teams": await list_visible_teams(...)}`.
  - `get_team_budget_usage(team_id: str)` → `member_budget_usage(...)`; if `None` or team not in visible set → MCP error "not a member of this team, or team not visible".
- `app` = the streamable-HTTP ASGI app wrapped with the auth middleware, served by `uvicorn app.main:app`. (Confirm exact FastMCP API — `FastMCP(...).streamable_http_app()` — against the installed `mcp` version before finalizing; adjust import/mount accordingly.)
- [ ] **Step 1 — tests** (`test_tools.py`): call the underlying tool functions directly with a seeded ContextVar identity + fake sessions, asserting the output shapes and the not-a-member error. (Full protocol/transport is exercised manually; keep unit tests on the tool logic.)
- [ ] **Step 2 run→fail; Step 3 implement; Step 4 run→pass; Step 5 commit.**

## Task 5: Deploy wiring

**Files:** Modify `docker-compose.yml`; create `mcp/.dockerignore` (copy gateway's if present); note kustomize/helm as follow-up.

- [ ] Add an `mcp` service to `docker-compose.yml` mirroring `gateway`: `build: {context: ./mcp}`, a host port (e.g. `3005:8000`), env `APP_DATABASE_URL` + `APP_LITELLM_DATABASE_URL` (same values as `backend`) + `APP_KEY_JWT_SECRET`, `depends_on: [db]`.
- [ ] Add a `deploy/kustomize` + `deploy/helm` entry only if trivially mirrored; otherwise leave a `TODO(deploy)` note in the plan's follow-up and surface it to the user. (K8s deploy can be a separate PR.)

---

## Verification

- `cd mcp && pip install -e '.[dev]' && python -m pytest -q` → all green (identity, data, tools).
- `ruff check mcp/app mcp/tests` → clean.
- Manual smoke: `docker compose up mcp`, then an MCP client (or `curl` streamable-HTTP `tools/list`) with `Authorization: Bearer sk-<real key>` → `list_my_teams` returns the caller's teams; `get_team_budget_usage` returns their in-team budget/spend; a team they're not in → error; bad token → 401.

## Follow-ups (not in this plan)

- K8s (kustomize/helm) deployment manifests for the `mcp` service.
- Optional: super-user elevation (see all teams) if a portal admin ever calls it.
- Optional later tools: usage trend, per-model, per-tag.
