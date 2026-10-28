# Gateway Keycloak SSO — user token → per-user LiteLLM key — Design

Date: 2026-10-27 (token-exchange model added 2026-10-28)
Status: implemented on feat/personal-keys (uncommitted), including the
token-exchange client model on Keycloak 26.3.3 and verified end to end on the
local stack (2026-10-28). Remaining items are under "Remaining work".
Related: `gateway/app/sso.py`, `gateway/app/proxy.py`,
`backend/app/api/external.py` (`POST /api/external/token-auth`),
`backend/app/services/sso_keys.py`, `backend/app/services/portal_keys.py`,
`backend/app/services/user_provisioning.py`, `backend/app/auth/jwt.py`,
`deploy/keycloak/litellm-realm.json`, personal keys spec
`2026-10-07-personal-keys-user-model-grants-design.md`

## Goal

Internal web apps already log their users in with Keycloak. They should be
able to call the inference gateway **as that user** without the user ever
minting, copying or storing a LiteLLM key:

- the gateway accepts a Keycloak access token in `Authorization: Bearer`,
- swaps it for a LiteLLM key that belongs to that user (personal) or to a
  team the user is a member of (`x-team-id`),
- and LiteLLM then enforces and accounts for the call exactly as for a
  portal key: model scope, budget, TPM/RPM, spend logs per user/team.

**Anti-goal:** the token a user can see in their browser must *not* be usable
against the gateway. A user who opens devtools must not be able to take the
app's token and call LLMs directly, outside the app's own limits. That is
what the token-exchange model below is for.

## Decisions

| Topic | Decision |
|---|---|
| Which LiteLLM key | One auto-minted **gateway SSO key** per (user, team). Personal by default; `x-team-id: <team_id>` selects the team key. |
| Who calls | Internal web apps, **from their server side**, forwarding a user token. CLI/device flow and service accounts are out of scope. |
| Where the token is verified | **Backend** (`/api/external/token-auth`, signature + `iss` + `aud` + `azp`). The gateway only peeks at `iss` to route and caches the backend's answer. |
| How an app gets a gateway token | **Keycloak Standard Token Exchange (V2)** done by a server-only client. Browser-visible tokens never carry the gateway audience. |

## Request flow

```
Browser ──login (web-app, public or confidential)──▶ Keycloak
        ◀── access token: aud=[web-app-gw], azp=web-app       (no litellm-gateway)

Browser ──app request + session/token──▶ App server
App server ──token-exchange (client web-app-gw + secret,
              subject_token=<user token>)──▶ Keycloak
           ◀── gateway token: aud=litellm-gateway, azp=web-app-gw, same user

App server ──Authorization: Bearer <gateway token> [, x-team-id]──▶ gateway /v1/*
gateway:  iss == APP_KEYCLOAK_ISSUER ?  no → normal key path (sk- normalisation, pass-through)
                                        yes → cache[sha256(token|team)]
          miss → POST backend /api/external/token-auth {token, team_id}  (X-Api-Key)
                 backend: verify(aud=litellm-gateway, azp ∈ allowlist) → user_id=UPPER(preferred_username)
                          → upsert/provision user → personal grant or team membership check
                          → find or mint SSO key → rebuild sk- key from metadata
          gateway: drop all credential headers + x-team-id,
                   Authorization: Bearer sk-<sso key> → LiteLLM
```

The LiteLLM key exists only between backend, gateway and LiteLLM. Neither
the app nor the user ever sees it.

### Why token exchange (and why a separate exchange client)

First tried on a throwaway Keycloak 26.4. Then the final setup was verified
on the local stack, Keycloak 26.3.3 importing `deploy/keycloak/litellm-realm.json`
(2026-10-28):

- The login client's token has **no `litellm-gateway` audience**: portal token
  `aud=[litellm-portal-gw, litellm-portal]`. `token-auth` answers 401, so the
  gateway returns 401 "Invalid or expired token".
- Only a **confidential client with Standard token exchange on** can exchange.
  - The login client itself gets "Standard token exchange is not enabled for
    the requested client". Public clients cannot exchange at all.
  - The exchange client without its secret gets "Invalid client or Invalid
    client credentials".
  - The exchange client cannot log users in: "Client not allowed for direct
    access grants".
- **The gateway audience must not be reachable from the login client, not
  even as an optional client scope.**
  - Optional scopes are granted to whoever asks for them in the authorize
    request. A user can edit that request to add the scope.
  - Tested on 26.4: `scope=openid litellm-gateway` on a client with that
    optional scope returned `aud=litellm-gateway`.
  - When the client lacks the scope, the same request fails with `invalid_scope`.
  - Hence the audience lives only on the exchange client.
- **The exchange client must be in the subject token's `aud`.**
  - Otherwise Keycloak answers `access_denied` "Client is not within the
    token audience".
  - So the login client gets an audience mapper adding `<app>-gw`.
  - That audience only lets the token be exchanged. The backend ignores it.
- **Exchanged token:**
  - `aud=litellm-gateway` only, and `azp=<app>-gw`.
  - `preferred_username`, `email` and the `exp` of the user session are kept.
  - `expires_in` equals the realm access-token lifespan.
  - No refresh token (default), so the app exchanges again after refreshing
    the user token.

So `aud=litellm-gateway` can only be obtained with a client secret that lives
on an app server, and `azp` names that server-side client. That makes
`APP_GATEWAY_ALLOWED_AZP` a real second lock: it lists exchange clients,
which users cannot impersonate. With the earlier model, where apps forwarded
the login token, an allowed `azp` was just a token anyone could copy out of
the browser.

## Keycloak version

**Keycloak 26.3.3** is pinned in `docker-compose.yml` and helm
`keycloak.image.tag`.

- Standard Token Exchange (V2) is supported and on by default from 26.2.
- 26.0, the previous pin, only has the legacy preview exchange, which needs
  fine-grained admin permissions. Do not use it.
- If production apps log in against a corporate Keycloak rather than the
  bundled one, that server must be ≥ 26.2 as well.
- In the air-gapped environments, mirror `quay.io/keycloak/keycloak:26.3.3`
  into the internal registry.

No realm-wide objects are needed: no shared client scope and no
`litellm-gateway` client. The gateway audience is a hardcoded audience mapper
(`included.custom.audience`) on each exchange client.

This is a deliberate choice. A shared client scope would mean one fewer
mapper per app. But putting a `clientScopes` array in the realm import stops
Keycloak from creating its built-in scopes (`profile`, `email`, …), and a
per-client mapper keeps the audience off every client that is not an
exchange client.

## Onboarding an app

Every app gets two clients. The portal itself is set up this way in
`deploy/keycloak/litellm-realm.json` as `litellm-portal` and
`litellm-portal-gw`.

| | `<app>` (login) | `<app>-gw` (exchange) |
|---|---|---|
| Type | public (SPA) or confidential | **confidential**, secret only on the app server |
| Flows | standard flow (+ PKCE), as today | standard, implicit, direct access grants and service accounts all **off** |
| Token exchange | off | **Standard token exchange: on** (`standard.token.exchange.enabled=true`) |
| Client scopes | the app's usual ones, **including `basic`** (puts `sub` in the token, needed for account pinning) | default `basic`, `profile`, `email` (the backend needs `sub`, `preferred_username`, `email`); `fullScopeAllowed=false` |
| Mappers | Audience → included client audience `<app>-gw` (access token only) | Audience → included custom audience `litellm-gateway` (access token only) |

Then add `<app>-gw` to `APP_GATEWAY_ALLOWED_AZP` on the backend
(`["<app>-gw", …]`, JSON list).

App server, per user session:

```bash
curl -s "$KC/realms/litellm/protocol/openid-connect/token" \
  -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d client_id=<app>-gw -d client_secret=$APP_GW_SECRET \
  -d subject_token="$USER_ACCESS_TOKEN" \
  -d subject_token_type=urn:ietf:params:oauth:token-type:access_token
# → access_token: aud=litellm-gateway, azp=<app>-gw

curl -s "$GATEWAY/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "x-team-id: <team_id>" \   # optional; omit for the personal SSO key
  -H 'Content-Type: application/json' \
  -d '{"model":"…","messages":[…]}'
```

App guidance:

- **Keep the gateway token server-side.** Never return it to the browser,
  and never log it.
- **Cache it per user session until shortly before `exp`.**
  - The gateway cache is keyed by the token.
  - Exchanging on every request costs a Keycloak round trip plus a backend
    round trip each time.
- **Exchange again after refreshing the user token.** No refresh token comes
  with the exchanged token.
- **Map the gateway's errors** (`{"error":{"message","type":"auth_error"}}`):
  - **401**: token invalid or expired. Re-exchange, or re-login if the user
    token itself is dead.
  - **403**: no personal grant (or beta off), or not a member of `x-team-id`.
    Show "no access"; retrying will not help.
  - **503**: auth service unavailable. Retry with backoff.
- **Call from the server only.** The gateway has no CORS on purpose, and
  browser calls are not supported.

## Backend and gateway (implemented)

- **Gateway** (`gateway/app/sso.py`, `proxy.py`):
  - The SSO path is taken when the Bearer value's unverified `iss` equals `APP_KEYCLOAK_ISSUER`.
  - Portal keys are shown to users as `eyJ…` HS256 JWTs without `iss`, so they keep the normal path.
  - The cache key is `sha256(token|team)`, with TTL `min(APP_SSO_CACHE_TTL=60, exp − now)` and at most `APP_SSO_CACHE_MAX_ENTRIES` entries. Failures are never cached.
  - Before forwarding, the gateway removes the incoming credential headers and `x-team-id`. The Keycloak token never reaches LiteLLM.
- **Backend** (`POST /api/external/token-auth`, `X-Api-Key` = `APP_EXTERNAL_API_KEY`):
  - Returns 404 when `APP_GATEWAY_SSO_ENABLED=false`.
  - Verification: signature, `iss`, `aud == APP_GATEWAY_TOKEN_AUDIENCE` (`litellm-gateway`), and `azp ∈ APP_GATEWAY_ALLOWED_AZP`. Every failure gets a uniform 401.
  - The allowlist is required: SSO enabled with an empty `APP_GATEWAY_ALLOWED_AZP` fails closed (503, logged as an error; the gateway answers 503 "Authentication service unavailable").
  - `user_id` is `preferred_username.upper()`, the same canonical id the portal uses.
  - First-time users are upserted and auto-provisioned, as on portal login.
  - Personal calls require the beta switch plus a personal grant (`require_personal_access`). Team calls require a `LiteLLM_TeamMembership` row.
  - SSO key (`services/sso_keys.py`):
    - The key is looked up by `metadata.issued_via="gateway_sso"`.
    - If missing, it is minted under `pg_advisory_xact_lock` so that concurrent first calls create exactly one key.
    - Personal SSO keys also carry `key_type="personal"`, so they show in the personal usage views.
    - Blocked keys are returned as-is and never re-minted, so an admin block stays effective.
    - The raw key is rebuilt from `sk_key_id`/`sk_iat` and never stored.

No code change is needed for the token-exchange model. The backend already
checks `aud` and `azp`. The model only changes which clients can obtain
such a token.

## Keycloak account pinning (`sub`)

**Problem.** The portal identifies a person only by `preferred_username`
upper-cased (사번), for example `USER001`. Keys, team membership, personal
grant, budget, spend and the portal role (`global_role`, which is never
demoted) are all keyed on that id. Whoever later holds the same username
inherits all of it. Ways that can happen:

- A 사번 is reused for a new hire after the old account was deleted.
- `editUsernameAllowed` is turned on, letting someone rename themselves to a
  deleted 사번.
- The `preferred_username` mapper is pointed at a user-editable attribute.
- A future LDAP or IdP federation renames or reuses accounts upstream.

**Design.** `custom_users.keycloak_sub` records the Keycloak account id
(`sub`, immutable, never reused) first seen for each user id. All checks
live in `upsert_portal_user`, which both the portal (`deps.get_current_user`)
and the gateway (`token-auth`) go through.

- **New user:** the row is inserted with the token's `sub`.
- **Existing row with NULL:** an atomic first claim (`UPDATE … WHERE
  keycloak_sub IS NULL`). Of two concurrent first logins only one pins; the
  loser re-reads the row and is compared with the winner.
- **Same `sub`:** pass, with no write.
- **Different `sub`:** the mismatch is logged as an error with both subs and
  the `azp`. Under `enforce` the request is rejected:
  - portal: 403 "계정 확인이 필요합니다. 관리자에게 문의하세요."
  - `token-auth`: the uniform 401 "Invalid token"
- **Token without `sub`:**
  - `enforce` rejects it.
  - `record` skips the check.
  - `jwt.verify_token` no longer substitutes the username for a missing
    `sub`.
- **No unique index on `sub`.** One account under two user ids (a username
  rename) takes nothing from anyone, and a unique index would only lock that
  person out.

**Mode:** `APP_KEYCLOAK_SUB_PINNING`, one of `off | record | enforce`.

| Where | Value | Why |
|---|---|---|
| code default, helm | `record` | |
| dev/stg/prd overlays | `record` | |
| compose | `off` | Every Keycloak container re-create re-imports the realm with new account ids. |

Rollout:

1. Run `record`. Check that login tokens carry `sub` (`basic` client scope).
   Watch the log for `Keycloak sub mismatch`; each hit is a reused or
   re-mapped id to clean up.
2. Switch to `enforce`.
3. Remember that a row's first login after the column exists becomes its
   owner. Clean up departed employees' ids before or during `record`.

**`sub` requires the `basic` client scope.** Keycloak 25+ emits `sub`
only through that scope.

- The repo realm's `litellm-portal` lacked it, and its tokens had no `sub`.
  Both realm clients now list it.
- Every app login client in production needs it too. The
  token-exchange clients already put `sub` in the exchanged token.

**Admin release.** `DELETE /api/admin/users/{user_id}/keycloak-sub` (super
user) sets the pin to NULL; the next login pins whoever logs in.

- When the id goes to a different person, first remove the previous owner's
  keys, teams and personal grant.
- After a realm rebuild that changes every `sub` (a new Keycloak, re-imported
  users), release all pins at once:

  ```sql
  UPDATE custom_users SET keycloak_sub = NULL;
  ```

- The admin user detail (`GET /api/admin/users/{id}`) now returns
  `keycloak_sub`. There is no UI button yet.

## Revocation and lifetimes

- Disabling a user in Keycloak does not kill tokens already issued. Each
  token stays valid until its `exp`, plus up to 60 s of gateway cache.
  - The realm's `accessTokenLifespan` is 1800 s in the repo realm.
  - Production should use around 300 s. The exchange runs once per token, so a short lifespan only means more refreshes.
- To cut off a user immediately, block their SSO keys in LiteLLM or remove
  the personal grant or team membership. Blocked keys are not re-minted.
  Grant and membership changes take effect on the next cache miss.

## Remaining work

1. Set `APP_GATEWAY_ALLOWED_AZP` in each overlay before turning SSO on there.
   - It is `[]` in dev/stg/prd today, where SSO is off. With SSO on and an
     empty list, token-auth fails closed (503).
   - Compose sets `["litellm-portal-gw"]`.
   - The helm chart does not template it yet.
2. Production secrets:
   - `litellm-portal-gw`'s secret in the realm file is a placeholder
     (`change-me-portal-gw`), like the portal's.
   - Real exchange-client secrets go into each app's own secret store. The
     portal stack itself does not need them.
3. Production realm lifespan: `accessTokenLifespan` is 1800 s in the repo
   realm; ~300 s is recommended.
4. Turn `APP_KEYCLOAK_SUB_PINNING` to `enforce` per environment after a
   clean `record` period (see "Keycloak account pinning"). Optional: an admin
   UI button for the release endpoint.
5. Out of scope, still open:
   - CORS: not planned, by design.
   - Per-app SSO keys keyed by `azp`. Today one user shares one SSO key
     across apps, so per-app spend is not separable.
   - Helm has no gateway and no `APP_EXTERNAL_API_KEY`.
   - The hardcoded portal-key signing secret (`portal_keys.KEY_JWT_SECRET`),
     shared by all portal and SSO keys. Separate hardening task.

## Verification

Unit tests:

- Backend: `test_external_token_auth.py` (16), `test_user_provisioning.py`
  (4), `test_auth_jwt.py`. Suite result: 500 passed, 21 failed; the 21 are
  the Postgres baseline failures, identical to HEAD.
- Gateway: 56 passed.

First local E2E (Keycloak 26.0). The login token carried the gateway
audience through a mapper that has since been removed.

- Personal key with beta off → 403.
- Team member → 200; one key minted (10007) and reused on the next call.
- Non-member → 403.
- Tampered token → 401.
- 5 concurrent first calls → exactly 1 key (10008).
- Chat and SSE streaming are relayed.
- Spend logs attribute to USER001 / alpha team.
- An existing `eyJ…` portal key still works.

Token-exchange E2E (Keycloak 26.3.3, realm imported from the repo, backend
`APP_GATEWAY_ALLOWED_AZP=["litellm-portal-gw"]`, 2026-10-28, user001):

| # | Request | Result |
|---|---|---|
| 1 | portal password-grant token | `aud=[litellm-portal-gw, litellm-portal]`, `azp=litellm-portal` |
| 2 | that token → backend `/api/me` | 200 (portal login unaffected) |
| 3 | that token → gateway (alpha team) | **401** "Invalid or expired token" |
| 4 | `litellm-portal` exchanges itself | "Standard token exchange is not enabled for the requested client" |
| 5 | `litellm-portal-gw` without secret | `unauthorized_client` |
| 6 | `litellm-portal-gw` password grant | "Client not allowed for direct access grants" |
| 7 | exchange via `litellm-portal-gw` | `aud=litellm-gateway`, `azp=litellm-portal-gw`, same `exp`, no refresh token |
| 8, 9 | exchanged token, alpha team | 200 twice; SSO key 10007 reused, no new key |
| 10 | exchanged token, personal (beta off) | 403 "개인 키(Beta)가 꺼져 있습니다…" |
| 11 | exchanged token, beta team (non-member) | 403 "해당 팀의 멤버가 아닙니다." |
| 12 | exchanged token, bad signature | 401 |
| 13 | exchanged token, `stream: true` | SSE chunks relayed |

Sub pinning E2E (2026-10-28, migration `054_user_keycloak_sub` applied on
start, backend `APP_KEYCLOAK_SUB_PINNING=enforce`, realm with `basic`):

| # | Request | Result |
|---|---|---|
| 1 | portal token | carries `sub` = Keycloak user id |
| 2–4 | user001 `/api/me`, then gateway via exchange | 200, 200; `USER001` pinned to that `sub` |
| 5 | delete user001 in Keycloak, create a new user001 (new `sub`) | — |
| 6 | new person `/api/me` | **403** "계정 확인이 필요합니다…" |
| 7 | new person → gateway (exchanged) | **401**; pin unchanged; two `sub mismatch … rejected` errors (azp `litellm-portal`, `litellm-portal-gw`) |
| 9 | admin001 `DELETE /api/admin/users/USER001/keycloak-sub` | 200 `unpinned`; the same call as a regular user → 403 |
| 10–12 | new person `/api/me`, gateway | 200, 200; `USER001` now pinned to the new `sub` |
