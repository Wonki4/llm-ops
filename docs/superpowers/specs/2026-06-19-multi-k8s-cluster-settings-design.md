# Multi K8s cluster management in Portal Settings (tabbed)

**Date:** 2026-06-19
**Branch:** `feat/multi-k8s-cluster-settings`

## Problem

The portal targets a single Kubernetes cluster — `K8sClient` loads one mounted
kubeconfig (`APP_KUBECONFIG_PATH` + `APP_K8S_DEFAULT_NAMESPACE`, `config.py`).
Operators run **multiple** clusters and need to register them in the portal. The
current settings page is a flat stack of cards with no organization.

## Scope

In scope:
- Re-shape `/admin/settings` into **3 tabs by concern**: 일반 / 팀 / 클러스터.
- A **클러스터** tab that manages *N* K8s clusters (create / edit / delete).
- A cluster is defined by a pasted **kubeconfig** + a **context** name.
- kubeconfig is **encrypted at rest**, **masked on read** (never returned to the
  client), and validated via a **connection test**.

Explicitly **out of scope** (later work, in each menu):
- Deployments / benchmarks actually running against a *selected* cluster
  (targeting). This task only stores configs and exposes a stable `id` +
  non-secret summary so those menus can reference a cluster later.
- Reconcilers iterating over multiple clusters.

## Data model — new table `custom_k8s_cluster`

A dedicated ORM table (not the `custom_portal_settings` key-value store):
kubeconfigs are large, sensitive, and need stable IDs for later targeting.

| column | type | notes |
|---|---|---|
| `id` | UUID PK | stable identifier other menus will target |
| `name` | varchar(128), unique | display label |
| `context` | varchar(256) | context to select within the kubeconfig |
| `namespace` | varchar(128), default `default` | default namespace |
| `kubeconfig_encrypted` | text | Fernet-encrypted kubeconfig YAML |
| `api_server` | varchar(512), nullable | parsed from kubeconfig — non-secret, for display |
| `is_default` | bool, default false | single default cluster |
| `description` | text, nullable | "concern" note |
| `created_by` / `updated_by` | varchar(128) | audit |
| `created_at` / `updated_at` | timestamptz | audit |

Migration `021_k8s_clusters`, `down_revision = "020_benchmark_ephemeral"`.

## Encryption — `app/services/crypto.py`

- Fernet symmetric encryption.
- Key from `APP_ENCRYPTION_KEY` (env). If unset, derive deterministically from
  `session_secret_key` (`SHA256` → urlsafe base64 32 bytes) so it works
  out-of-the-box; log a warning that a dedicated key is recommended.
- `encrypt(plaintext) -> str`, `decrypt(token) -> str`.
- Decryption only ever happens server-side (connection test; future targeting).

## Backend API — `app/api/k8s_clusters.py` (super-user only)

Prefix `/api/admin/k8s-clusters`. Mirrors `model_deployments` CRUD style
(`require_super_user`, ORM session, `_serialize`).

- `GET ""` → list, **masked**: `{id, name, context, namespace, api_server,
  is_default, description, has_kubeconfig, created_by, created_at, updated_at}`.
  Never includes kubeconfig.
- `POST ""` → create `{name, context, namespace?, kubeconfig, description?,
  is_default?}`. Validate kubeconfig parses as YAML and the context exists;
  parse `api_server`; encrypt; if `is_default` unset others.
- `PUT "/{id}"` → update; `kubeconfig` optional (omitted = keep existing).
- `DELETE "/{id}"`.
- `POST "/test"` → test an unsaved config `{kubeconfig, context}`; and
  `POST "/{id}/test"` → test a stored config. Loads kubeconfig + context and
  calls the cluster `/version`. Returns `{ok, message, server_version}`.

Register in `main.py` after `portal_settings.router`.

## K8s client change (minimal)

Add a helper that builds an `ApiClient` from a kubeconfig **dict** + context
(`kubernetes_asyncio.config.load_kube_config_from_dict`) — used by the test
endpoint only. The existing `_api_client()` (single mounted kubeconfig) is
untouched; real targeting wiring is a later task.

## Frontend

- Convert `admin/settings/page.tsx` to shadcn `Tabs` (already vendored):
  - **일반**: API key limits + cache catalog suffixes.
  - **팀**: auto-register default team + extra team rules + hidden teams.
  - **클러스터**: new.
- `components/cluster-settings-tab.tsx`: list (table/cards showing name,
  context, namespace, api_server, default badge), Add/Edit dialog
  (name, context, namespace, kubeconfig `<textarea>`, description, default
  checkbox, **연결 테스트** button), delete with confirm. On edit the kubeconfig
  field is masked (placeholder "변경하려면 새로 붙여넣기"); empty = keep existing.
- Hooks in `use-api.ts`: `useK8sClusters`, `useCreateK8sCluster`,
  `useUpdateK8sCluster`, `useDeleteK8sCluster`, `useTestK8sCluster`.
- `K8sClusterSummary` type in `types/index.ts`.
- i18n keys added to `messages/en.json` and `messages/ko.json`.

## Testing

- Backend: crypto round-trip; CRUD; default-flag exclusivity; kubeconfig
  validation rejects bad YAML / missing context; `/test` with a mocked client.
- Manual: rebuild, migrate, drive the UI in a browser — tabs render, cluster
  create/edit/delete works, kubeconfig is masked on reload.

## Risks

- **Encryption key rotation**: changing `APP_ENCRYPTION_KEY` invalidates stored
  kubeconfigs. Documented; out of scope to handle re-encryption.
- **Connection test from the portal pod** needs network reach to each cluster's
  API server; a failing test is surfaced, not fatal to saving.
