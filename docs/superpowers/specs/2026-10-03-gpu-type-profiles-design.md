# GPU type profiles — Design

Date: 2026-10-03
Status: draft, awaiting review
Related: `backend/app/services/model_deployment_manifests.py`, `backend/app/services/benchmark_serving.py` (existing `gpu_type` → `gpu-type` label fold), `custom_k8s_cluster`, recipe/deployment forms

## Goal

Let an operator pick a **GPU type by name** ("a100-80g", "h100", "l40s") on a
recipe and at deploy time, and have the portal translate that into whatever
the target cluster needs (node label selector, tolerations, resource key).
Recipes stop carrying cluster-specific placement and become portable across
clusters; the label key is configurable per cluster because clusters are
labelled by hand (no GPU Feature Discovery) and may use different keys.

## Scope

In:

- A per-cluster **GPU profile** registry (name → label key/value,
  tolerations, GPU resource key, display metadata) with admin CRUD.
- Node discovery per cluster: which label values exist, how many GPUs are
  allocatable and already requested per value, so the admin can create
  profiles from what is really there and the deploy dialog can show
  availability.
- `gpu_type` on recipes (name only) and deployments (name + the resolved
  placement, stored at deploy time as today).
- Resolution at deploy time; the same resolver replaces the benchmark's ad
  hoc `gpu-type` label fold.
- Deploy dialog: cluster picker (today a free-text id) and a GPU type picker
  with availability.
- Recipe import: recognise a profile from an external Deployment's
  nodeSelector when the (key, value) matches one.

Out (see Non-goals): MIG partition management, Dynamic Resource Allocation,
namespace-level default placement (deferred earlier; a profile covers the
GPU part of it), automatic node labelling.

## Background — verified facts

- K8s has no "GPU type" concept; only extended resources
  (`nvidia.com/gpu: N`). Type selection is done with node labels +
  `nodeSelector`, optionally paired with pool taints + tolerations.
- The production clusters have no GPU Feature Discovery; nodes will carry a
  hand-managed `gpu-type=<value>` label (decided 2026-10-03). The key must
  stay configurable.
- `benchmark_serving.py::build_ephemeral_deployment` already accepts
  `overrides.gpu_type` and writes `node_selector["gpu-type"] = value`
  (hardcoded key).
- Recipes/deployments carry `gpu_count`, `gpu_resource_key`,
  `node_selector`, `tolerations` as explicit fields; the manifest builder
  renders them verbatim.
- `custom_k8s_cluster` already holds per-cluster defaults
  (`default_nfs_*`), so cluster-scoped settings have a home.
- `K8sClient` has no node listing today.

## Decisions

1. **Profiles live per cluster, named by the operator.** Table
   `custom_gpu_profile`. The same name on several clusters ("a100-80g") is
   how a recipe stays portable: the recipe stores the name, the cluster
   resolves it.
2. **Label key is configurable at two levels.** `custom_k8s_cluster.
   gpu_label_key` (default `gpu-type`) is the cluster's convention; a
   profile may override it with its own `label_key` for odd pools. The
   benchmark path uses the same resolution, so the hardcoded `gpu-type`
   goes away.
3. **A profile is the whole placement bundle for one GPU kind:**
   `{label_key?, label_value, gpu_resource_key, tolerations, vram_gb?,
   description?}`. MIG profiles are just profiles whose
   `gpu_resource_key` is the MIG resource name; nothing special in code.
4. **Resolution happens at deploy time and the result is stored on the
   deployment row** in the existing columns (`node_selector`,
   `tolerations`, `gpu_resource_key`), plus `gpu_type` for display. Running
   deployments never change when a profile is edited; the next deploy or
   update re-resolves. Precedence when a profile is chosen: the profile's
   label pair and resource key win over the recipe's explicit values for
   the same keys; other explicit nodeSelector keys stay; tolerations are
   the union.
5. **`gpu_type` is optional.** `gpu_count = 0` recipes (CPU mocks, tiny
   models) never need it. For `gpu_count > 0` the deploy dialog requires a
   type when the target cluster has at least one profile, and falls back to
   the recipe's explicit placement when it has none (so clusters without
   profiles keep working exactly as today).
6. **Discovery reads nodes, it does not label them.** `GET …/gpu-nodes`
   groups nodes by the configured label key and reports allocatable and
   requested GPUs per value. Unlabelled GPU nodes are listed under
   `(unlabelled)` so the admin sees what to fix on the cluster.
7. **Recipes pick a type by name from the union of profile names across
   clusters**, with free text allowed (a recipe may predate the cluster that
   will run it). Deploy-time validation catches a name the chosen cluster
   does not know (400 with the list of known names).

## Architecture

### Data model (migration 049)

```
custom_k8s_cluster
  + gpu_label_key   varchar(128) not null default 'gpu-type'

custom_gpu_profile
  id                uuid pk
  cluster_id        uuid null  -- null = portal default cluster (as elsewhere)
  name              varchar(64)   -- ^[a-z0-9][a-z0-9.-]*$, unique per cluster
  label_key         varchar(128) null  -- null → cluster.gpu_label_key
  label_value       varchar(128) not null
  gpu_resource_key  varchar(128) not null default 'nvidia.com/gpu'
  tolerations       jsonb null
  vram_gb           int null       -- display only
  description       text null
  enabled           bool not null default true
  created_by/updated_by/created_at/updated_at
  unique (cluster_id, name) nulls not distinct

custom_serving_recipe      + gpu_type varchar(64) null
custom_model_deployment    + gpu_type varchar(64) null
```

### Resolver (`backend/app/services/gpu_profiles.py`, pure)

```python
def resolve_placement(profile, cluster_label_key, *, node_selector, tolerations, gpu_resource_key) -> Placement
```

- `node_selector = {**explicit, (profile.label_key or cluster_label_key): profile.label_value}`
- `tolerations = union(explicit, profile.tolerations)` de-duplicated on
  (key, operator, value, effect)
- `gpu_resource_key = profile.gpu_resource_key`
- `profile is None` → explicit values unchanged.
- `benchmark_serving.build_ephemeral_deployment` takes a resolved profile
  instead of a raw `gpu_type` string; the API resolves `overrides.gpu_type`
  by name on the run's cluster and falls back to the old `gpu-type` label
  fold only when no profile table rows exist for that cluster (compat).

### K8s client

`K8sClient.list_gpu_nodes(label_key, resource_keys)` → per node: name,
label value (or None), allocatable per resource key, requested per resource
key (sum of container requests of non-terminal pods on that node),
`schedulable` (not cordoned). One `list_node` + one `list_pod_for_all_
namespaces(field_selector=spec.nodeName=…)` batched per cluster; 5 s
timeout like the external scan.

### API

- `GET /api/admin/k8s-clusters/{id}/gpu-profiles`, `POST`, `PUT /{pid}`,
  `DELETE /{pid}` (super user). `{id}` may be `default` for the portal
  cluster.
- `PUT /api/admin/k8s-clusters/{id}` gains `gpu_label_key`.
- `GET /api/admin/k8s-clusters/{id}/gpu-nodes` → `{label_key, groups:
  [{label_value|null, nodes, allocatable, requested, available,
  resource_key}], errors}`; also `GET /api/admin/gpu-types` → union of
  enabled profile names with per-cluster availability for the pickers.
- Recipe body/read: `gpu_type`. Deployment create: `gpu_type` (resolved
  server-side; 400 `unknown gpu_type for this cluster: known=[…]`).
  Deployment read: `gpu_type` plus the resolved columns as today.
- Serving overview deployment entries carry `gpu_type`.

### Frontend

- Cluster settings page: "GPU 라벨 키" field on the cluster, and a "GPU 타입
  프로필" table per cluster (name, label pair, resource key, tolerations,
  VRAM, enabled) with create/edit dialog. A "노드에서 가져오기" panel calls
  `gpu-nodes` and offers one-click profile creation per discovered label
  value (name pre-filled from the value), and lists unlabelled GPU nodes.
- Recipe form: GPU section gets "GPU 타입" (combobox: union of names +
  free text). The explicit nodeSelector/tolerations/resource-key fields move
  under "고급: 직접 지정" with a hint that a profile overrides the same keys.
- Deploy dialog: cluster **select** (replaces the free-text id; default =
  portal default), GPU type select filtered to that cluster's profiles
  showing "available / allocatable", pre-selected from the recipe's
  `gpu_type`; required when `gpu_count > 0` and the cluster has profiles.
  Shows the resolved nodeSelector/tolerations under the picker.
- Deployment detail/list and serving home: type chip (e.g. `a100-80g`),
  detail shows the resolved label pair.
- Benchmark "new" page: its GPU type field becomes the same select.
- Recipe import: when the source nodeSelector contains the cluster's label
  key and the value matches a profile, pre-fill `gpu_type` and drop that
  key from the explicit selector.

### Error handling

- Deploy with unknown `gpu_type` → 400, lists known names for that cluster.
- Node discovery failure (no kubeconfig, timeout) → `errors` in the
  response, UI shows the message; profiles are still editable by hand.
- Profile delete while deployments reference the name → allowed (rows keep
  the resolved values and the name string); the detail page shows the name
  with a "프로필 없음" hint.

## Testing

- Pure: `resolve_placement` (profile wins on the label key and resource
  key, other keys kept, toleration union/de-dupe, None passthrough, custom
  `label_key` override), name regex, unique-per-cluster validation.
- API (mock_db): profile CRUD incl. `default` cluster id, deploy with
  known/unknown `gpu_type`, resolved columns stored, benchmark override
  through the resolver and the compat fold.
- K8s client: `list_gpu_nodes` shaping with a fake API (labels, allocatable,
  pod request sums, cordoned node).
- Importer: nodeSelector → `gpu_type` when matching, untouched otherwise.
- Frontend: tsc/eslint/build; Playwright smoke of the cluster profile table
  and the deploy dialog pickers with mocked proxy.
- Local e2e on minikube `portal-test`: label the node `gpu-type=cpu-mock`,
  create a profile `cpu-mock` (resource key `nvidia.com/gpu`, count 0
  recipes), deploy a mock recipe with `gpu_type=cpu-mock` and assert the
  Deployment's nodeSelector; deploy with a bogus type and assert the 400.

## Non-goals

- Labelling nodes from the portal (cluster team owns node labels).
- MIG partition creation or DRA ResourceClaims. A MIG slice is representable
  today as a profile with the MIG resource key.
- Namespace-level default nodeSelector/tolerations beyond GPU placement
  (deferred 2026-10-03; profiles cover the GPU part).
- Per-recipe multiple GPU types / fallbacks ("prefer H100, else A100").

## Open questions for review

1. Should a profile also carry a default `gpu_count` hint or VRAM-based
   sanity check (e.g. warn when a 70B bf16 recipe picks `l40s` ×1)? Draft:
   display `vram_gb` only, no checks.
2. Benchmarks: keep accepting a raw `gpu_type` string that is not a profile
   (compat fold) for one release, or require profiles from day one?
3. The deploy dialog's cluster select changes a long-standing free-text
   field; confirm there is no automation posting `cluster_id` by hand.
