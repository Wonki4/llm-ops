# Storage as a first-class object (per-cluster, per-namespace bindings)

**Date:** 2026-10-19
**Branch:** `feat/cluster-storage`

## Problem

Storage reachable from a workload differs per K8s cluster (and, for PVCs, per
namespace), but the portal has no notion of it:

- Serving recipes and deployments carry a single free-text `pvc_name` +
  `pvc_mount_path`. A recipe therefore hard-codes a claim that only exists in
  one cluster/namespace — **deploying the same recipe to another cluster breaks**
  (pod stuck `Pending` on a missing claim). This is the primary pain.
- Serving cannot mount NFS at all; only benchmarks can (cluster
  `default_nfs_*`, raw `model_name` targets only — not used in production).
- Nothing tells an operator which data exists on which cluster, whether a PVC
  is actually `Bound`, or which recipes/deployments depend on it.

## Goals

- A **Storage** is a first-class, admin-managed object ("llama-weights",
  "hf-cache") with **bindings** that say what it physically is in each
  cluster × namespace (an existing PVC, or an NFS export).
- Recipes reference **storages** (any number), never claims. The concrete
  volume is resolved from the deploy target's cluster + namespace.
- One place to see bindings, live PVC status and usage.

## Non-goals

- Creating PVCs / PVs / StorageClasses. Bindings reference existing claims only.
- NFS reachability checks (the portal cannot mount NFS itself).
- Copying data between clusters.
- llm-d router stacks (they mount nothing).

## Decisions

| Topic | Decision |
|---|---|
| Model | Logical `Storage` + N `StorageBinding`s (not per-cluster objects matched by a string key). |
| Binding scope | `(storage, cluster, namespace)`; `namespace = NULL` means every namespace of the cluster; `cluster = NULL` is the portal default cluster (as for deployments). |
| PVC binding | `namespace` required (PVCs are namespaced). |
| NFS binding | `namespace` optional; a namespace-specific binding overrides the cluster-wide one. |
| Per recipe | Any number of storages, each with its own `mount_path` and `read_only`. |
| Resolution | Exact `(cluster, namespace)` → `(cluster, NULL)` → unresolved (deploy blocked). |
| Deployment snapshot | The resolved volumes are stored on the deployment; binding edits never touch running deployments, only the next (re)deploy. |
| Who manages | Storage + bindings: super user. Picking storages in a recipe: whoever can edit recipes. |
| Cluster default NFS | Replaced by a per-cluster "benchmark default storage" (+ mount path); existing values migrated. |

## Data model

```
custom_storage
  id            uuid pk
  name          varchar(64) unique, slug [a-z0-9-], ≤ 50 chars (volume name prefix "st-" must fit 63)
  description   text null
  kind          varchar(16)  weights | cache | data | other   (display/filter only)
  created_by / updated_by / created_at / updated_at

custom_storage_binding
  id            uuid pk
  storage_id    fk custom_storage  ON DELETE CASCADE
  cluster_id    fk custom_k8s_cluster null ON DELETE CASCADE   -- NULL = portal default cluster
                                              -- (mounted kubeconfig), same meaning as deployment.cluster_id
  namespace     varchar(63) null              -- NULL = whole cluster (NFS only)
  type          varchar(8)  pvc | nfs
  claim_name    varchar(253) null             -- pvc
  nfs_server    varchar(255) null             -- nfs
  nfs_path      varchar(1024) null            -- nfs
  sub_path      varchar(1024) null            -- optional volumeMount.subPath
  created_by / updated_by / created_at / updated_at
  UNIQUE (storage_id, COALESCE(cluster_id, '00000000-0000-0000-0000-000000000000'), COALESCE(namespace, ''))
  CHECK  (type = 'pvc' AND claim_name IS NOT NULL AND namespace IS NOT NULL
          AND nfs_server IS NULL AND nfs_path IS NULL)
      OR (type = 'nfs' AND nfs_server IS NOT NULL AND nfs_path IS NOT NULL
          AND claim_name IS NULL)
```

Recipe (`custom_serving_recipe`) gains `storages JSONB not null default '[]'`:

```json
[{"storage_id": "<uuid>", "mount_path": "/models", "read_only": true}]
```

Deployment (`custom_model_deployment`) gains the same `storages` plus the
resolved snapshot per entry:

```json
[{"storage_id": "<uuid>", "mount_path": "/models", "read_only": true,
  "volume_name": "st-llama-weights",
  "resolved": {"binding_id": "<uuid>", "type": "pvc", "claim_name": "models-pvc",
               "namespace": "serving-prod", "sub_path": null}}]
```

`volume_name` is stored (not derived) so migrated deployments keep their
current name and their manifests stay byte-identical (no rollout).

Cluster (`custom_k8s_cluster`): drop `default_nfs_server/path/mount_path`, add
`bench_storage_id` (fk custom_storage, ON DELETE SET NULL) and
`bench_mount_path`.

### Validation (recipe and deployment `storages`)

- `storage_id` exists; no storage listed twice.
- `mount_path` absolute, normalised, unique within the list, not `/dev/shm`
  or a prefix/child of another entry's path.
- `volume_name` unique and not a reserved name (`shm`).
- `model_path` not under any `mount_path` → **warning** in the response
  (`warnings: ["model_path_outside_storages"]`), not an error — HF repo ids
  are legal.

## Resolution service

`app/services/storages.py`:

```python
resolve(db, storage_ids, cluster_id, namespace) -> dict[storage_id, Binding | None]
to_volumes(entries) -> (volumes, volume_mounts)   # from resolved snapshots
```

- Lookup per storage: binding with `(cluster_id, namespace)`, else
  `(cluster_id, NULL)`, else `None`. `cluster_id` matches with `IS NOT DISTINCT
  FROM`, so a NULL (portal default) target only uses NULL-cluster bindings.
- `to_volumes` emits
  - pvc: `{"name": vn, "persistentVolumeClaim": {"claimName": c, "readOnly": ro}}`
  - nfs: `{"name": vn, "nfs": {"server": s, "path": p, "readOnly": ro}}`
  - mount: `{"name": vn, "mountPath": mp, "readOnly": ro, "subPath": sp?}`
  - Migrated entries (`read_only: false`, legacy name `model-weights`) emit
    exactly today's dicts (no `readOnly` key) — covered by a golden test.
- `model_deployment_manifests._volumes` uses `to_volumes(dep.storages)` then
  appends the existing shm volume. Prefill/decode pools share the same list.

## API

`/api/admin/storages` (super user):

| Method | Path | Notes |
|---|---|---|
| GET | `/` | list with `binding_count`, `recipe_count`, `deployment_count`; `?kind=&q=` |
| POST | `/` | create |
| GET | `/{id}` | detail: bindings + usage (recipes, deployments, `stale` flag) |
| PUT | `/{id}` | name/description/kind |
| DELETE | `/{id}` | 409 if any recipe/deployment/cluster bench default references it |
| POST | `/{id}/bindings` | create; 409 on duplicate scope |
| PUT | `/{id}/bindings/{bid}` | update |
| DELETE | `/{id}/bindings/{bid}` | 200 with `affected_deployments` when running deployments resolved through it (UI confirms first via `?dry_run=true`) |
| GET | `/{id}/bindings/status` | live PVC phase per pvc binding: `Bound`/`Pending`/`Lost`/`missing`/`error`; nfs → `null` |

Helpers:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/admin/k8s-clusters/{id}/pvcs?namespace=` | claim names + phase + capacity (`K8sClient.list_pvcs`, CoreV1 `list_namespaced_persistent_volume_claim`) |
| POST | `/api/admin/storages/resolve` | `{storage_ids, cluster_id, namespace}` → per storage `{binding, pvc_phase}`; used by the deploy dialog |

Recipe / deployment APIs accept and return `storages`; `pvc_name` /
`pvc_mount_path` are no longer accepted (400 with a hint) or returned.

Deploy (`POST` from recipe) resolves every storage: any unresolved → **400**
listing them; PVC `Pending`/`missing` → allowed, returned as `warnings`.
Redeploy of an existing deployment re-resolves and refreshes the snapshot.

A deployment is **stale** when any entry's current resolution differs from
its snapshot (binding edited, deleted, or a more specific binding added).

## UI

1. **Admin → Storage** (`/admin/storages`): list (name, kind, description,
   bound clusters, usage counts; search + kind filter).
2. **Storage detail** (`/admin/storages/[id]`): editable header; bindings
   table (cluster, namespace or `*`, type, target, sub_path, live status);
   add/edit binding dialog — cluster → namespace → type; for PVC a dropdown of
   that namespace's claims with free-text fallback when listing fails
   (the cluster picker includes "portal default"); usage
   section (recipes, deployments, stale badge).
3. **Recipe form**: the PVC inputs become a "Storage" section of rows
   `[storage ▾] [mount path] [read-only ☑] [✕]`; each picked storage shows
   chips of where it is bound; hint under `model_path` naming the storage it
   falls under, warning if none.
4. **Deploy dialog**: after cluster + namespace, a per-storage resolution
   preview (binding target, PVC phase, ✗ for unresolved). Any ✗ disables
   Deploy and links to the storage detail. Clusters with no binding for some
   required storage are greyed in the picker but still selectable (namespace
   may change the answer).
5. **Deployment detail**: mounted volumes from the snapshot + stale badge
   ("binding changed — redeploy to apply").
6. **Cluster settings**: the three NFS inputs become "Benchmark default
   storage [▾] + mount path".

## Other consumers

- **Ephemeral benchmark servings** (`benchmark_serving.build_ephemeral_deployment`)
  copy `storages` (with snapshots) instead of `pvc_*`.
- **Raw `model_name` benchmarks** (`resolve_bench_nfs` → `resolve_bench_storage`):
  per-run `params` NFS override (API only) still wins; otherwise the cluster's
  `bench_storage_id` is resolved for the job's namespace and mounted read-only
  at `bench_mount_path` as `model-weights`.
- **Self-serving bench jobs / external clones** copy pod volumes — unchanged.
- **Recipe import** (`recipe_import`): each PVC volume in the imported
  manifest is matched to a storage by a binding with the same claim name
  (and namespace when known); matches are attached, unmatched claims produce
  `unregistered_pvc:<claim>` warnings. The `multi_pvc` warning is removed.
- **Recipe drafts from a deployment** (`recipe-drafts.ts`) copy `storages`
  without snapshots.

## Migration (one Alembic revision)

1. Create `custom_storage`, `custom_storage_binding`; add `storages` columns.
2. For each distinct non-null `pvc_name` across recipes and deployments,
   create storage `pvc-<slug(claim)>` (kind `weights`, suffix on collision).
3. For each deployment with `pvc_name`, upsert binding
   `(storage, deployment.cluster_id, deployment.namespace, pvc, claim)`.
   Deployments without `cluster_id` produce a binding with `cluster_id = NULL`
   (portal default cluster).
4. Fill recipe/deployment `storages` with
   `{storage_id, mount_path: pvc_mount_path, read_only: false}`; deployments
   also get `volume_name: "model-weights"` and the resolved snapshot.
5. For each cluster with `default_nfs_*`: create storage
   `<cluster>-bench-weights` + cluster-wide NFS binding; set
   `bench_storage_id` / `bench_mount_path`; then drop `default_nfs_*`.
6. `pvc_name` / `pvc_mount_path` stay in the tables, unread, and are dropped
   in a follow-up release. Downgrade restores `default_nfs_*` from the
   bench storage and drops the new tables/columns.

## Testing

- Resolution order: exact → cluster-wide → unresolved; namespace override of
  a cluster-wide NFS binding.
- `to_volumes` for pvc/nfs/sub_path/read_only; golden test that a migrated
  deployment renders byte-identical volumes to the current `_volumes`.
- Validation: duplicate storage, overlapping mount paths, `/dev/shm`,
  `model_path` warning.
- API: super-user only; 409 delete-in-use; duplicate binding scope; PVC
  binding without namespace → 422; deploy with unresolved storage → 400; PVC
  `Pending` → warning.
- Stale detection after binding edit / delete / more specific binding.
- Migration on a seeded DB (recipes + deployments + cluster NFS) up and down.
- Bench: params override > bench storage > none.
- Frontend: tsc/eslint/build; Playwright (mocked `/api/proxy`) for storage
  list/detail, binding dialog PVC dropdown + fallback, recipe storage rows,
  deploy-dialog resolution preview and disabled Deploy.
