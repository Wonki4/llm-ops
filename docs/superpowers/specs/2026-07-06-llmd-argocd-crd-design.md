# llm-d Provisioning via ArgoCD CRD — Design

**Date:** 2026-07-06
**Status:** Approved

## Problem

llm-d stacks are provisioned by calling the ArgoCD **REST API** through a
registered ArgoCD connection (`custom_argocd_connection`: server URL + encrypted
bearer token). The portal already builds an `argoproj.io/v1alpha1` Application
object — it just POSTs it to ArgoCD's REST endpoint instead of applying it to
the cluster. Operationally this means the portal must hold and rotate ArgoCD
bearer tokens, reach the ArgoCD API server, and maintain a separate connection
registry — parallel to the K8s cluster registry the portal already has.

## Goal

Provision, update, delete, and read the status of llm-d stacks by applying the
Application **custom resource** directly to the ArgoCD control-plane namespace
via the K8s API (reusing the existing registered-cluster/kubeconfig plumbing),
letting the ArgoCD controller reconcile. Remove the ArgoCD REST client and the
connection registry entirely.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| REST vs CRD | Full replacement — remove the REST path and the ArgoCD connection concept |
| Cluster access | Reuse registered clusters: `cluster_id` → `k8s_for_cluster`; null → portal default kubeconfig |
| ArgoCD control-plane namespace | Per **cluster**: new `custom_k8s_cluster.argocd_namespace` (default `argocd`); null-cluster stacks fall back to global `APP_ARGOCD_NAMESPACE` (default `argocd`) |
| Resource inspection | Simplify to the Application CR `.status.resources[]` list; drop the live per-resource manifest fetch |
| Existing stacks | Auto-migrate: drop the connection column/table; stacks keep their `argo_app_name` + `cluster_id`, so a CRD apply adopts the already-existing Application CRs |

## Architecture

Replace `ArgoCDClient` (httpx REST) with K8s `CustomObjectsApi` operations on the
`applications.argoproj.io/v1alpha1` resource, in the cluster's ArgoCD namespace.
The Application object the portal already builds is applied to the K8s API rather
than POSTed to ArgoCD's REST API. ArgoCD's controller reconciles it exactly as
before; the portal reads sync/health straight from the CR's `.status`.

llm-d Applications target `destination.server = https://kubernetes.default.svc`
(in-cluster), so the ArgoCD cluster is also the deployment cluster — the same
registered cluster the portal applies the CR to.

## Components

### Backend

**`clients/k8s.py`** — add Application-CR methods using `CustomObjectsApi`
(`group="argoproj.io"`, `version="v1alpha1"`, `plural="applications"`):
- `apply_application(namespace: str, manifest: dict) -> None` — read-then
  create-or-patch (upsert), mirroring the existing `_upsert` pattern.
- `get_application(namespace: str, name: str) -> dict | None` — the CR object, or
  None on 404.
- `delete_application(namespace: str, name: str) -> None` — foreground/background
  cascade; 404 swallowed (idempotent).

**`services/llmd_manifests.py`** — `build_argo_application` sets
`metadata.namespace = <argocd namespace>` (new required kwarg). Everything else
unchanged.

**`api/llmd.py`** — swap the REST client for `k8s_for_cluster(db, stack.cluster_id)`
plus the new methods:
- create/update: `apply_application(argocd_ns, build_argo_application(...))`.
- status (`_live_status`): `get_application(argocd_ns, argo_app_name)` →
  `_argo_status(obj)` (reads `.status.sync.status` / `.status.health.status` —
  identical shape to today, since these fields live in the CR).
- `/applied`: build the resource list from the CR's `.status.resources[]`
  (name/kind/namespace/status/health).
- delete: `delete_application(argocd_ns, argo_app_name)`.
- **Remove** the `/resource` endpoint and its handler.
- Error mapping: replace `_argo_error_message` (httpx-specific) with an
  `ApiException` mapper — 403 → "portal lacks RBAC on applications.argoproj.io in
  `<ns>`", 404 → "ArgoCD Application CRD/namespace not found — is ArgoCD
  installed?", connection errors → "cluster unreachable". Surfaced as 502 with
  the reason, as today.

**Resolving the argocd namespace** (`services/clusters.py` or a small helper):
`argocd_namespace_for(db, cluster_id)` → the cluster's `argocd_namespace`, or the
global `settings.argocd_namespace` when `cluster_id` is null.

**Removed:** `clients/argocd.py` (ArgoCDClient + probe_argocd),
`api/argocd_connections.py`, `db/models/custom_argocd_connection.py` (+ its
`__init__` export), the router registration in `main.py`, and any
ArgoCD-connection settings in `config.py`.

### DB (Alembic migration)

- `custom_k8s_cluster`: add `argocd_namespace VARCHAR(128) NOT NULL DEFAULT 'argocd'`.
- `custom_llmd_stack`: drop the `argocd_connection_id` FK column.
- Drop the `custom_argocd_connection` table.
- Existing stacks are retained (name, argo_app_name, cluster_id, values). A CRD
  apply on next reconcile/update adopts the existing Application CRs by name.
- Downgrade re-creates the table + column (empty), for reversibility.

### Config

- `APP_ARGOCD_NAMESPACE` (default `argocd`) — global fallback for null-cluster
  stacks. Remove ArgoCD server/token settings if any exist.

### RBAC (`deploy/rbac/portal-k8s-rbac.yaml`)

- Add an `applications.argoproj.io` rule (verbs: get, list, create, update,
  patch, delete). Namespace-scoped to the ArgoCD namespace in intent; since the
  portal uses a ClusterRole, add the rule there.
- Update the file's header note — it currently states argoproj.io verbs are NOT
  needed because llm-d goes through the REST API. This change reverses that.

### Frontend

- **llm-d create/detail** (`admin/llmd`): remove the ArgoCD-connection selector;
  add a cluster selector reusing the picker the serving/benchmark forms use
  (`useK8sClusters`). Do not ask for the argocd namespace on the stack form — it
  is a per-cluster setting.
- **Cluster settings tab** (`cluster-settings-tab.tsx`): add an "ArgoCD
  namespace" field (default `argocd`) to the create/edit cluster form; wire it
  through the k8s-clusters API create/update request models.
- **Remove** the ArgoCD connections settings tab (`argocd-settings-tab.tsx`),
  its `use-api` hooks, and the settings-page entry.
- Drop the per-resource live-manifest view; keep the applied-resources list
  (from CR status).
- i18n: remove ArgoCD-connection strings; add the cluster "ArgoCD namespace"
  label/hint in en + ko.

## Data flow

```
Create/Update stack
  → resolve argocd_ns = cluster.argocd_namespace | APP_ARGOCD_NAMESPACE
  → k8s = k8s_for_cluster(db, stack.cluster_id)
  → k8s.apply_application(argocd_ns, build_argo_application(stack, ns=argocd_ns, ...))
  → ArgoCD controller reconciles

Status / list / applied
  → k8s.get_application(argocd_ns, argo_app_name)
  → _argo_status(obj) / obj.status.resources[]

Delete
  → k8s.delete_application(argocd_ns, argo_app_name)  (404 = already gone)
```

## Error handling

- `ApiException` 403 → RBAC hint (missing applications.argoproj.io grant).
- `ApiException` 404 on the CRD/namespace → "ArgoCD not installed / wrong
  namespace".
- `K8sNotConfigured` / connection errors → cluster-unreachable message (mirrors
  the reconciler/serving paths).
- All surfaced as HTTP 502 with the reason, preserving today's UX.
- Delete is idempotent (404 swallowed). Status reads are best-effort (failure →
  Unknown), unchanged from today.

## Testing

- Unit: `build_argo_application` includes `metadata.namespace`; the new
  `apply_application`/`get_application`/`delete_application` (mock the K8s
  client as the existing k8s tests do); `_argo_status` from a CR object (same
  shape as the REST object).
- API: create/update/delete/status with mocked `k8s_for_cluster` +
  CustomObjectsApi; a 403 → 502 with the RBAC hint; null-cluster falls back to
  the global argocd namespace.
- Migration: apply against the local dockerized DB — assert the 4 stacks
  survive, `custom_argocd_connection` is gone, `custom_llmd_stack` has no
  `argocd_connection_id`, and `custom_k8s_cluster` gained `argocd_namespace`.

## Out of scope

- Live per-resource manifest inspection (dropped).
- apps-in-any-namespace beyond the per-cluster ArgoCD namespace.
- Multiple destination clusters per stack (llm-d destination is in-cluster).
- Re-plumbing ArgoCD auth/SSO — no longer relevant once the REST client is gone.
