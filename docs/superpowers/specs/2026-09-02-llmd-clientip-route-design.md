# llm-d ClientIP direct-route set — design

**Date:** 2026-09-02
**Status:** Approved (brainstorming complete)

## Problem

An llm-d stack is an ArgoCD Application (Helm chart → EPP router + Envoy
sidecar, entry Service `{argo_app_name}-epp`). The portal separately lays down
**one Ingress** (`{argo_app_name}-ingress`) fronting that `-epp` router service.

All ingress traffic therefore goes through the EPP, which does cache-aware
endpoint scoring and load-spreads across model-server pods. That defeats
client-to-pod stickiness: consecutive requests from one client can land on
different vLLM pods, so prefix / KV-cache locality is lost.

We want an **optional, additional** front door that routes a given client IP
straight to one model-server pod and keeps it there.

## Goal

When a stack has the ClientIP toggle on, the portal lays down — **alongside**
the existing EPP ingress (which is unchanged) — a **Service with
`sessionAffinity: ClientIP`** whose selector is the stack's
`router.modelServers.matchLabels` (i.e. it points straight at the model-server
pods), plus an **Ingress** in front of that Service. A given client IP then
sticks to one vLLM pod, bypassing EPP scoring. Default **OFF**; existing stacks
are untouched until the toggle is enabled.

## Decisions (locked during brainstorming)

1. **Add alongside** — the existing EPP ingress (`{app}-ingress` → `{app}-epp`)
   stays; the ClientIP Service+Ingress is a *second* entry point. Two paths
   coexist: EPP smart-routing, and ClientIP sticky-direct.
2. **Per-stack toggle, default OFF** — a boolean column on the stack. Existing
   stacks are unaffected; enabling it deploys the pair, disabling it removes it.
3. **Derived host + override** — the ClientIP ingress host defaults to
   `{argo_app_name}-direct.{effective_ingress_domain}`, with a per-stack
   override column. Distinct host from EPP → no host/path collision.

## Non-goals

- No change to the EPP path (its Application, values, or `{app}-ingress`).
- No `sessionAffinityConfig.clientIP.timeoutSeconds` knob in v1 — use the
  Kubernetes default (10800s / 3h) by omitting `sessionAffinityConfig`.
- No separate ingress-class column — the ClientIP ingress reuses the stack's
  existing `_ingress_class` resolution and `settings.llmd_ingress_path`.
- No accuracy/benchmark wiring; this is a serving-path routing feature only.

## Architecture

### Source of truth for the selector

The ClientIP Service and the EPP router must select the **same** pods, so both
derive from the stack's rendered `values_snapshot` (the effective Helm values
already persisted on the stack):

- **selector** = `values_snapshot["router"]["modelServers"]["matchLabels"]`
- **targetPort** = `values_snapshot["router"]["modelServers"]["targetPorts"][0]["number"]`,
  defaulting to `8000` when absent.

Reading from `values_snapshot` (not re-parsing a selector string) guarantees
the Service tracks exactly what the router targets, including any hand-edits the
user made to `values.yaml`.

If `matchLabels` is empty while the toggle is on, a selector-less Service would
bind no endpoints — so this is rejected at the API layer (400) rather than
deployed broken.

### Manifest builders — `backend/app/services/llmd_manifests.py` (pure)

Add three pure functions next to the existing `build_llmd_ingress`:

- `clientip_service_name(stack) -> str` → `f"{stack.argo_app_name}-clientip"`.
- `build_clientip_service(stack, *, match_labels: dict, target_port: int) -> dict`:
  ```
  apiVersion: v1
  kind: Service
  metadata:
    name: {argo_app_name}-clientip
    namespace: {stack.namespace}
    labels: {app.kubernetes.io/managed-by: litellm-portal}
  spec:
    type: ClusterIP
    sessionAffinity: ClientIP
    selector: <match_labels>
    ports:
      - {name: http, port: 80, targetPort: <target_port>, protocol: TCP}
  ```
- `build_clientip_ingress(stack, *, host, ingress_class, ingress_path) -> dict`:
  mirrors `build_llmd_ingress` but the backend service name is
  `clientip_service_name(stack)` on port **number 80** (the ClientIP Service's
  own port). Name `f"{stack.argo_app_name}-clientip-ingress"`. `ingress_class`
  empty omits `ingressClassName` (cluster default), same as the EPP ingress.

A small extractor helper reads the two values from a rendered values dict:

- `modelservers_target(values: dict) -> tuple[dict, int]` → `(match_labels,
  target_port)`, tolerant of missing keys (`{}`, `8000`).

### Data model — `custom_llmd_stack` + migration `045_llmd_clientip_route`

Two new columns on `CustomLlmdStack`:

- `clientip_enabled: bool` — `nullable=False`, `default=False`,
  `server_default="false"`.
- `clientip_ingress_host: str | None` — `String(253)`, nullable. NULL →
  derived `{argo_app_name}-direct.{effective_ingress_domain}`.

Migration `045_llmd_clientip_route.py`:
- `revision = "045_llmd_clientip_route"`, `down_revision = "044_serving_recipe"`.
- `upgrade`: `add_column` both (boolean with `server_default="false"`, string
  nullable).
- `downgrade`: `drop_column` both.

### API — `backend/app/api/llmd.py`

- `CreateLlmdStackRequest` / `UpdateLlmdStackRequest` gain
  `clientip_enabled: bool | None = None` and
  `clientip_ingress_host: str | None = None`. (Create defaults `clientip_enabled`
  to `False` when omitted; Update leaves unset fields unchanged, matching the
  existing per-field update pattern.)
- Helpers:
  - `_clientip_host(stack)` → `stack.clientip_ingress_host or
    f"{stack.argo_app_name}-direct.{settings.effective_ingress_domain}"`.
  - `_clientip_manifests(stack) -> list[dict]`: when `stack.clientip_enabled`,
    extract `(match_labels, target_port)` from `stack.values_snapshot` and
    return `[build_clientip_service(...), build_clientip_ingress(..., host=
    _clientip_host(stack), ingress_class=_ingress_class(stack), ingress_path=
    settings.llmd_ingress_path or "/")]`; else `[]`.
  - `_require_clientip_selector(stack)`: raise **400** when
    `stack.clientip_enabled` and the extracted `match_labels` is empty
    (`"modelServers.matchLabels is required to enable the ClientIP direct route"`).
- **create_stack** — after computing `values_snapshot` and before/around the
  existing K8s writes:
  1. call `_require_clientip_selector(stack)` (validation before any K8s write).
  2. apply the Application + EPP ingress (unchanged).
  3. if enabled: `create_or_patch(namespace, _clientip_manifests(stack))`.
     (When disabled at create there is nothing to remove.)
- **update_stack** — after recomputing `values_snapshot`:
  1. `_require_clientip_selector(stack)`.
  2. apply Application + EPP ingress (unchanged).
  3. if enabled: `create_or_patch(namespace, _clientip_manifests(stack))`;
     **else best-effort `delete`** of `clientip_service_name(stack)` +
     `{app}-clientip-ingress` (so toggling OFF cleans up). The delete goes
     through the existing `k8s.delete(namespace, {"service": ..., "ingress":
     ...})` helper and is wrapped so cleanup failure doesn't fail the update.
- **delete_stack** — after the EPP ingress cleanup, best-effort `delete` of the
  clientip service + ingress (same `delete` helper), logged like the existing
  ingress cleanup.
- **`_serialize`** — add:
  - `clientip_enabled: stack.clientip_enabled`
  - `clientip_ingress_host: _clientip_host(stack)` (effective)
  - `clientip_service: clientip_service_name(stack)`
  - override under a `clientip_overrides: {ingress_host: stack.clientip_ingress_host}`
    block (parallel to the existing `ingress_overrides`).

The K8s client already handles `Service` + `Ingress` in `create_or_patch` and
`delete` — no client change needed.

### Frontend — `admin/llmd/new` + `[id]`, types, i18n

- `frontend/src/types/index.ts`:
  - The stack detail type gains `clientip_enabled: boolean`,
    `clientip_ingress_host: string`, `clientip_service: string`, and
    `clientip_overrides: { ingress_host: string | null }`.
  - The create/update body types gain `clientip_enabled?: boolean` and
    `clientip_ingress_host?: string | null`.
- **new/page.tsx** and **[id]/page.tsx**: add, in the ingress section, a
  checkbox/toggle "ClientIP 직결 경로" bound to `clientip_enabled`, and a host
  override `Input` (shown when enabled) mirroring the existing `ingress_host`
  field (placeholder = derived default on the edit page). Submit maps
  `clientip_enabled` and `clientip_ingress_host.trim() || null` into the body.
- **[id]/page.tsx** detail view: when enabled, show effective ClientIP host and
  the `clientip_service` name as read-only `Field`s.
- **i18n** `frontend/messages/en.json` + `ko.json` (`llmd*` block): add keys —
  `clientipLabel`, `clientipHint`, `clientipHostLabel`, `clientipHostHint`,
  `clientipServiceLabel`. Keep en/ko key counts equal (project convention).

## Testing

- **`backend/tests/test_llmd_manifests.py`** (pure, no DB):
  - `build_clientip_service`: `spec.sessionAffinity == "ClientIP"`,
    `spec.selector == match_labels`, single port `port: 80` with the given
    `targetPort`, `managed-by` label, name `{app}-clientip`.
  - `build_clientip_ingress`: backend service name `{app}-clientip`, port number
    80, name `{app}-clientip-ingress`; `ingressClassName` present/omitted per
    the class arg.
  - `modelservers_target`: extracts labels+port; missing keys → `({}, 8000)`.
- **`backend/tests/test_llmd.py`** (mock_db + mocked K8s, following the file's
  existing fixtures):
  - Create with `clientip_enabled=True` and non-empty matchLabels → 201, and
    `create_or_patch` was called with a manifest list containing the ClientIP
    Service and Ingress.
  - Create with `clientip_enabled=True` but empty matchLabels → **400**, no K8s
    write for the ClientIP pair.
  - Update toggling from enabled → disabled calls `k8s.delete` with the
    clientip service + ingress names.
  - Delete removes the clientip service + ingress (best-effort; delete called).

Gate: **0 new failures vs the origin/main baseline** (conftest doesn't mock
`get_litellm_db`, so ~21 suite failures/errors are pre-existing — compare the
set/count, not the absolute number).

## Naming note

The ingress host default uses `-direct` (`{app}-direct.{domain}`) while the
Service/Ingress objects are named `{app}-clientip` / `{app}-clientip-ingress`.
This is intentional: the host advertises "direct route", the resources advertise
the "ClientIP" affinity mechanism. Both are cosmetic and can be unified later.

## File-by-file summary

| File | Change |
|------|--------|
| `backend/app/services/llmd_manifests.py` | `clientip_service_name`, `build_clientip_service`, `build_clientip_ingress`, `modelservers_target` |
| `backend/app/db/models/custom_llmd_stack.py` | `clientip_enabled`, `clientip_ingress_host` columns |
| `backend/migrations/versions/045_llmd_clientip_route.py` | add/drop the two columns |
| `backend/app/api/llmd.py` | request fields, helpers, create/update/delete wiring, serialize |
| `backend/tests/test_llmd_manifests.py` | builder unit tests |
| `backend/tests/test_llmd.py` | API tests (enable, empty-labels 400, toggle-off cleanup, delete cleanup) |
| `frontend/src/types/index.ts` | stack + body types |
| `frontend/src/app/(app)/admin/llmd/new/page.tsx` | toggle + host field |
| `frontend/src/app/(app)/admin/llmd/[id]/page.tsx` | toggle + host field + detail display |
| `frontend/messages/en.json`, `ko.json` | i18n keys (en/ko parity) |
