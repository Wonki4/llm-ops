# Benchmark model-weights mount (cluster default + per-run override)

**Date:** 2026-06-20
**Branch:** `feat/multi-k8s-cluster-settings`

## Update (2026-06-21): NFS instead of a referenced PVC

The benchmark mount now attaches an **inline NFS volume** rather than referencing
a pre-created PVC. The rest of the design (cluster default + per-run override,
benchmarks only, manual tokenizer path, precedence, both-or-neither validation)
is unchanged — only the volume type and field set differ.

- Cluster columns: `default_nfs_server` / `default_nfs_path` /
  `default_nfs_mount_path` (migration `024_cluster_default_nfs`, drops the `023`
  `default_pvc_*` columns). All nullable, non-secret.
- Per-run override `params` keys: `nfs_server` / `nfs_path` / `nfs_mount_path`.
- `build_vllm_bench_job` emits `volumes: [{name: model-weights, nfs: {server,
  path, readOnly: true}}]`. A **deployment** target still mounts its own PVC
  (`deployment.pvc_name` / `pvc_mount_path`) — unchanged; the NFS path applies
  only to raw `model_name` targets.
- Validation: server + export path + mount path are all-or-nothing (else 400).
- Resolution precedence (raw model_name): run override (`params`) → cluster
  default → none.

The PVC-based design below is kept as historical context.

---

## Problem

Performance benchmarks run the official `vllm bench serve`, which needs a
tokenizer. In an air-gapped cluster the tokenizer must come from a mounted PVC
(no HF download). Today `build_vllm_bench_job` already supports
`pvc_name` + `pvc_mount_path`, but they are only populated when the run targets a
portal-managed **deployment** (from `deployment.pvc_name`). A benchmark against a
raw `model_name` (LiteLLM alias) has `pvc_name = pvc_mount = None` — there is no
way to mount a PVC. With multi-cluster targeting, the PVC also differs per cluster.

## Decisions

- **Where:** cluster-level default **+** per-run override.
- **Scope:** benchmarks only. Deployments keep their existing
  `pvc_name`/`pvc_mount_path` fields untouched.
- **Tokenizer:** manual path input. The PVC only mounts the volume; the user
  points the existing `tokenizer` param at a path under the mount (e.g.
  `/models/qwen`). No auto-resolution.

## Data model

- `custom_k8s_cluster` gains two nullable columns (non-secret; safe to return in
  the masked serialization):
  - `default_pvc_name VARCHAR(253) NULL`
  - `default_pvc_mount_path VARCHAR(512) NULL`
  - Migration `023_cluster_default_pvc`, `down_revision = "022_bench_deploy_cluster"`.
- **Per-run override** lives in the existing benchmark `params` blob under keys
  `pvc_name` / `pvc_mount_path`. No `custom_benchmark_run` migration — params is
  stored verbatim and already round-trips for re-runs.
  - *Alternative considered:* dedicated columns on `custom_benchmark_run` (like
    `cluster_id`). Rejected — PVC override is never queried on, so params suffices.

## Backend

- `app/api/k8s_clusters.py`:
  - `CreateClusterRequest` / `UpdateClusterRequest` gain `default_pvc_name` and
    `default_pvc_mount_path` (both `str | None`).
  - `_serialize` returns both fields.
  - create/update persist them (update treats `None` as "leave unchanged" only
    for the standard PATCH semantics already used; empty string clears).
- `app/api/benchmarks.py` `_manifest_for` (performance branch): resolve PVC with
  precedence **deployment self PVC → run override (`params`) → cluster default →
  none**. Requires loading the run's `CustomK8sCluster` when `run.cluster_id` is
  set. Pass the resolved `pvc_name` / `pvc_mount_path` into `build_vllm_bench_job`
  (already supports them).
- **both-or-neither validation** at benchmark create: if exactly one of
  `pvc_name` / `pvc_mount_path` is set (after resolution from params), return
  `400` — "PVC name and mount path must be set together." Same rule applies to
  the cluster default at cluster create/update.
- Accuracy (lm-eval) runs do not use a PVC — unchanged.

## Frontend

- `components/cluster-settings-tab.tsx`: Add/Edit dialog gets two optional inputs
  — default PVC name, default mount path — with a short hint.
- `admin/benchmarks/new/page.tsx` (performance only): optional PVC name + mount
  path override inputs written into `params`. Hidden/disabled when targeting a
  deployment (which carries its own PVC). Hint next to the existing `tokenizer`
  field that it should point under the mount path.
- `types/index.ts`: `K8sClusterSummary` + create/update bodies gain the two
  `default_pvc_*` fields. `CreateBenchmarkRequest.params` is already arbitrary.
- `hooks/use-api.ts`: create/update cluster bodies include the new fields.
- i18n keys added to `messages/en.json` and `messages/ko.json`.

## Testing

- Backend unit:
  - Precedence resolution: deployment > run override > cluster default > none.
  - both-or-neither validation (cluster default and run override).
  - `_serialize` includes the default PVC fields.
  - `build_vllm_bench_job` emits the `model-weights` volume + volumeMount when a
    PVC is set (extend existing manifest coverage).
- Manual: register a cluster with a default PVC, launch a raw `model_name`
  performance bench, confirm the YAML preview shows the volume, mount, and the
  tokenizer path under the mount.

## Migrations

`022_bench_deploy_cluster → 023_cluster_default_pvc` (single head).

## Risks

- A PVC that exists in one cluster may not exist in another; a wrong claim name
  surfaces as a K8s scheduling error on the Job, not at create time. Acceptable —
  the run reconciler reports the Job failure.
