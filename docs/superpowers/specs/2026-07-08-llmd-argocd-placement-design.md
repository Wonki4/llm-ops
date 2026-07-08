# llm-d ArgoCD Placement Per Cluster — Design

**Date:** 2026-07-08
**Status:** Approved (user: "각각 설정하게해" — per-cluster configuration)

## Problem

`build_argo_application` hardcodes `spec.destination.server` to
`https://kubernetes.default.svc` and the portal applies the Application CR
into the *target* cluster's `argocd_namespace`. That is correct only for the
ArgoCD-per-cluster topology. In a central-ArgoCD topology (one ArgoCD
managing several clusters) both choices are wrong: the CR lands on a cluster
with no ArgoCD, and the destination points at the wrong cluster.

## Decision

Make ArgoCD placement configurable **per registered cluster** with two new
nullable columns on `custom_k8s_cluster` (migration 037):

- `argocd_host_cluster_id UUID NULL` — FK to `custom_k8s_cluster.id`,
  `ON DELETE SET NULL`. The cluster whose ArgoCD manages this cluster.
  NULL = this cluster itself (today's behavior).
- `argocd_dest_server VARCHAR(512) NULL` — the Application's
  `spec.destination.server`. NULL = `https://kubernetes.default.svc`
  (today's behavior). When set, it must be the server URL the managing
  ArgoCD's cluster secret registers the target under.

## Resolution rule

New helper in `app/services/clusters.py`:

```
argocd_placement_for(db, cluster_id) -> (K8sClient, argocd_namespace, destination_server)
```

For a stack targeting cluster T:

- `cluster_id` NULL or unresolvable → portal-default kubeconfig,
  `settings.argocd_namespace`, local dest URL (unchanged behavior).
- host = T.`argocd_host_cluster_id` → that cluster's row, else T itself.
  Resolution is **one hop only** — the host's own `argocd_host_cluster_id`
  is ignored. A dangling host id (row deleted) falls back to T itself.
- The CR is applied with the **host's** kubeconfig into the **host's**
  `argocd_namespace`; `destination.server` = T.`argocd_dest_server` or the
  local URL; `destination.namespace` stays `stack.namespace`.

All five `llmd.py` handler sites (create, update, delete, `_live_status`,
`applied_values`) switch from the `argocd_namespace_for` + `k8s_for_cluster`
pair to this single helper. `argocd_namespace_for` is deleted (llmd.py was
its only consumer); `k8s_for_cluster` stays for deployments/benchmarks.
`build_argo_application` gains a required `destination_server` kwarg.

## API + frontend

- `k8s_clusters.py`: both fields on Create/Update request models and in
  `_serialize`. Validation (400): host id must be a UUID, must reference an
  existing cluster, and must not be the cluster itself (empty = self).
  Empty strings clear either field on update (same sentinel style as the
  NFS fields).
- Cluster settings dialog: a "managing ArgoCD cluster" select (default
  option "this cluster itself", other registered clusters by name,
  excluding the cluster being edited) and a destination-server text input
  (placeholder `https://kubernetes.default.svc`). i18n keys under
  `settings.clusters` in en/ko.

## Compatibility

- Both columns nullable, no server defaults needed — existing rows and the
  4 existing stacks keep exactly today's all-local behavior.
- Placement is resolved from the cluster record at every operation, so
  fixing a cluster's config in ops retargets existing stacks on their next
  create/update/delete/status call without touching stack rows.
- Deleting a cluster that others name as host → FK sets their
  `argocd_host_cluster_id` NULL, silently reverting them to self-managed.
  Acceptable: admin-only surface, and status reads degrade to "Unknown"
  rather than erroring.

## Non-goals

- No multi-hop host chains, no cycle detection beyond the self-reference
  guard (one-hop resolution makes cycles harmless).
- No ArgoCD cluster-secret management — the admin registers the target in
  their ArgoCD themselves; the portal only writes the CR.
- No `destination.name`-based addressing (server URL only, v1).

## Verification

- Backend: pytest 0 NEW failures (baseline 21), ruff 0 NEW (baseline 78).
  New tests: placement resolution (null / self / host / dangling host),
  `destination_server` in the manifest, API field round-trip + validation.
- Migration applies cleanly on the local docker DB (alembic → 037).
- Frontend: lint 0 NEW (baseline 4 errors/13 warnings), build passes.

## v1 implementation notes (post final review)

Final whole-branch review: Ready to merge — 10/10 named checks PASS
(backward-compat traced byte-identical for NULL fields; central-ArgoCD flow
verified end-to-end incl. get/delete via the same placement). Known
ship-as-is minors:

- Orphaned-CR window: changing a cluster's placement (or deleting its host,
  FK SET NULL) between a stack's create and delete leaves the old CR on the
  former host; delete 404-swallows and reports ok. Spec-accepted; follow-up
  candidate: surface "no matching Application found" on delete.
- `clusters.py` module docstring understates the new placement-resolution
  scope; uuid-coercion two-liner duplicated in k8s_for_cluster and
  argocd_placement_for; create_cluster kwargs ordered after NFS fields.
  All cosmetic.
