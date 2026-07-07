# Benchmark External Servings via Fresh Clone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the benchmark form pick a discovered external serving; the run clones its live Deployment spec into a fresh ephemeral serving, runs `vllm bench serve` against the clone, and tears it down.

**Architecture:** Reuse the existing ephemeral pipeline untouched — `reconcile_benchmarks._drive_provisioning` reads everything from `run.serving_snapshot` (`model_path`, `vllm_extra_args`, `env`, `pvc_name`, `pvc_mount_path`) and keys on `serving_k8s_name`/`k8s_namespace`/`cluster_id`; `deployment_id` is already nullable. The external path adds: a live-spec reader on K8sClient, a faithful clone builder + facts extractor in `benchmark_serving.py`, an `external_target` branch in the create/preview API that fills the snapshot compatibly, and a grouped picker + clone/direct radio in the form. No schema change.

**Tech Stack:** FastAPI + kubernetes_asyncio (backend); Next.js + react-query + next-intl (frontend); pytest asyncio_mode=auto.

**Spec:** `docs/superpowers/specs/2026-07-07-bench-external-clone-design.md`

## Global Constraints

- Branch: `feat/bench-external-clone` (checked out, based on origin/main). Commits only here.
- Backend tests: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest` (asyncio auto). ~21 PRE-EXISTING failures (teams/keys/me/catalog/e2e); gate = **no NEW failures**.
- Ruff line-length 120 py311; repo-wide `ruff check app/ tests/` has ~78 pre-existing errors; gate = your changed files clean.
- Frontend gates: `npx tsc --noEmit` exit 0; `npm run lint` no NEW errors (4 pre-existing: models/dashboard, models/history, settings, login). i18n in BOTH `messages/en.json` + `messages/ko.json`, valid JSON.
- Clone resource names MUST come from `serving_resource_names(name)` (`<name>-deployment` / `<name>-service`) and the Service MUST listen on port 80 → serving port, because `serving_target_url` returns `http://<name>-service.<ns>.svc.cluster.local` (port 80) and teardown deletes by those names.
- Snapshot contract consumed by the reconciler (do NOT change reconcile_benchmarks.py): `served_model = snap["model_path"]`; `tokenizer = params["tokenizer"] or snap["model_path"]`; `api_key = snap["api_key_override"] or serving_api_key(snap["vllm_extra_args"], snap["env"])`; bench-job PVC mount from `snap["pvc_name"]`/`snap["pvc_mount_path"]`.
- External target identity: `{cluster_id: str|None, namespace: str, deployment_name: str}`. Clone deploys to the SAME cluster and namespace. v1 is performance tools only (`vllm_serving`, `sglang_serving`); `lm_eval` + external_target → 400.
- External option encoding in the form: `ext::<cluster_id|"">::<namespace>::<deployment_name>` (same convention as the llm-d form on branch feat/llmd-external-target — but this branch is independent; do not assume that code exists here).

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/clients/k8s.py` | Modify | `read_deployment(namespace, name) -> dict \| None` (sanitized live spec) |
| `backend/app/services/benchmark_serving.py` | Modify | `external_bench_facts(spec)`, `build_external_clone(spec, *, name, overrides)` |
| `backend/app/api/benchmarks.py` | Modify | `external_target` branch in create + preview |
| `backend/tests/test_bench_external_clone.py` | Create | All backend tests for this feature |
| `frontend/src/app/(app)/admin/benchmarks/new/page.tsx` | Modify | Grouped picker, clone/direct radio, external wiring |
| `frontend/src/hooks/use-api.ts` | Modify | `external_target` on CreateBenchmarkRequest type (in `types/index.ts` if defined there — check) |
| `frontend/messages/en.json`, `frontend/messages/ko.json` | Modify | New strings |

---

### Task 1: `K8sClient.read_deployment` (sanitized live spec)

**Files:**
- Modify: `backend/app/clients/k8s.py` (after `read_service_cluster_ip`, before the ArgoCD/Jobs sections — anywhere among the read helpers is fine)
- Test: Create `backend/tests/test_bench_external_clone.py`

**Interfaces:**
- Produces (Tasks 2-3 consume): `async read_deployment(namespace: str, name: str) -> dict | None` — None on 404, else:
  ```python
  {
    "name": str, "namespace": str, "labels": dict,
    "replicas": int,
    "container": {  # FIRST container only (v1)
      "name": str, "image": str,
      "command": list[str], "args": list[str],
      "env": [ {"name": str, "value": str|None} ... ],      # raw list; valueFrom entries have value None
      "env_raw": list[dict],                                 # sanitized to_dict of env for faithful cloning
      "resources": dict,                                     # requests/limits as plain dict
      "ports": [ {"containerPort": int, ...} ],
      "volume_mounts": list[dict],
    },
    "volumes": list[dict],
    "node_selector": dict | None,
    "tolerations": list | None,
  }
  ```

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_bench_external_clone.py`:

```python
"""Benchmark-by-cloning-an-external-serving: live-spec reader, clone builder, facts."""

import types
from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient


def _live_deployment(args=None, ports=None, volumes=None, mounts=None,
                     env=None, node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0"):
    container = MagicMock()
    container.name = "server"
    container.image = image
    container.command = None
    container.args = args if args is not None else ["--model", "/models/llama-3-8b", "--port", "8000"]
    container.env = env
    container.resources = MagicMock()
    container.resources.to_dict.return_value = {"limits": {"nvidia.com/gpu": "1"}}
    container.ports = ports
    container.volume_mounts = mounts
    pod_spec = MagicMock()
    pod_spec.containers = [container]
    pod_spec.volumes = volumes
    pod_spec.node_selector = node_selector
    pod_spec.tolerations = tolerations
    dep = MagicMock()
    dep.metadata.name = "ext-vllm"
    dep.metadata.namespace = "team-a"
    dep.metadata.labels = {"app": "ext-vllm"}
    dep.spec.replicas = 2
    dep.spec.template.spec = pod_spec
    return dep


def _k8s_with(apps):
    fake_api = MagicMock()
    fake_api.close = AsyncMock()
    return (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)),
        patch("app.clients.k8s.client.AppsV1Api", return_value=apps),
    )


async def test_read_deployment_shapes_spec():
    apps = MagicMock()
    apps.read_namespaced_deployment = AsyncMock(return_value=_live_deployment())
    p1, p2 = _k8s_with(apps)
    with p1, p2:
        spec = await K8sClient().read_deployment("team-a", "ext-vllm")
    assert spec["name"] == "ext-vllm" and spec["namespace"] == "team-a"
    assert spec["container"]["image"] == "vllm/vllm-openai:v0.6.0"
    assert spec["container"]["args"][0] == "--model"
    assert spec["container"]["resources"] == {"limits": {"nvidia.com/gpu": "1"}}
    assert spec["replicas"] == 2


async def test_read_deployment_none_on_404():
    apps = MagicMock()
    apps.read_namespaced_deployment = AsyncMock(side_effect=ApiException(status=404))
    p1, p2 = _k8s_with(apps)
    with p1, p2:
        assert await K8sClient().read_deployment("team-a", "gone") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v`
Expected: FAIL — `'K8sClient' object has no attribute 'read_deployment'`.

- [ ] **Step 3: Implement**

Add to `backend/app/clients/k8s.py` (near the other read helpers). Use the api_client sanitizer for k8s objects (`api_client.sanitize_for_serialization`) to convert typed models to plain dicts where convenient:

```python
    async def read_deployment(self, namespace: str, name: str) -> dict | None:
        """Live Deployment spec, sanitized for the external clone-bench builder.

        First container only (vLLM/SGLang servers are single-container); None on 404.
        """
        api_client = await self._api_client()
        try:
            apps = client.AppsV1Api(api_client)
            try:
                dep = await apps.read_namespaced_deployment(name=name, namespace=namespace)
            except ApiException as e:
                if e.status == 404:
                    return None
                raise
            pod = dep.spec.template.spec
            c = pod.containers[0]
            sanitize = api_client.sanitize_for_serialization
            return {
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "labels": dict(dep.metadata.labels or {}),
                "replicas": int(dep.spec.replicas or 0),
                "container": {
                    "name": c.name,
                    "image": c.image,
                    "command": list(c.command or []),
                    "args": list(c.args or []),
                    "env": [
                        {"name": e.name, "value": e.value} for e in (c.env or [])
                    ],
                    "env_raw": sanitize(c.env) or [],
                    "resources": sanitize(c.resources) or {},
                    "ports": sanitize(c.ports) or [],
                    "volume_mounts": sanitize(c.volume_mounts) or [],
                },
                "volumes": sanitize(pod.volumes) or [],
                "node_selector": dict(pod.node_selector or {}) or None,
                "tolerations": sanitize(pod.tolerations) or None,
            }
        finally:
            await api_client.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v && .venv/bin/ruff check app/clients/k8s.py tests/test_bench_external_clone.py`
Expected: 2 passed; ruff clean on new code (pre-existing k8s.py N818/E501 lines 20/65/67/69/97/126 are NOT yours).
Note: `sanitize_for_serialization` on MagicMock returns non-dict — if the test fails on sanitize of mocks, make the mock's `resources.to_dict` path irrelevant by patching: in the test, set `container.resources = {"limits": {"nvidia.com/gpu": "1"}}` style plain values and have the implementation tolerate plain dicts (`sanitize` of a plain dict returns it unchanged — kubernetes_asyncio's sanitizer passes through dicts/lists/primitives). Prefer adjusting the TEST fixtures to plain dicts/lists over complicating the implementation.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/clients/k8s.py backend/tests/test_bench_external_clone.py
git commit -m "feat(k8s): read_deployment sanitized live spec for clone-bench"
```

---

### Task 2: Clone builder + bench facts (`benchmark_serving.py`)

**Files:**
- Modify: `backend/app/services/benchmark_serving.py`
- Test: `backend/tests/test_bench_external_clone.py` (append)

**Interfaces:**
- Consumes: Task 1's spec shape; existing `serving_resource_names(name)`, `serving_api_key` (from `model_deployment_manifests`).
- Produces (Task 3 consumes):
  - `external_bench_facts(spec: dict) -> dict` → `{"served_model": str, "tokenizer": str, "model_arg": str, "pvc_name": str|None, "pvc_mount_path": str|None}`. `model_arg` = the `--model` value; `served_model` = `--served-model-name` value if present else `model_arg`; `tokenizer = model_arg`. PVC mapping: find the volume_mount whose `mountPath` is a prefix of `model_arg`, and if the matching pod volume is a `persistentVolumeClaim`, return its claim name + that mountPath; else None/None. Raises `ValueError("no --model/--served-model-name found in serving args")` when neither flag exists.
  - `build_external_clone(spec: dict, *, name: str, overrides: dict | None = None) -> list[dict]` → `[Deployment, Service]` manifests. Deployment: name/service from `serving_resource_names(name)`; labels `{"app": "llmops-bench-serving", "bench-serving": name}` on metadata + pod template + Service selector; `replicas: 1` always; container copied from spec (name/image/command/args/env_raw/resources/ports/volume_mounts); pod volumes/node_selector/tolerations copied. `overrides`: if it contains `resources`, replace the container resources; if `image`, replace image (keep v1 minimal — ignore other keys). Service: port 80 → targetPort resolved `--port` arg value → first `containerPort` → 8000.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_bench_external_clone.py`:

```python
from app.services.benchmark_serving import build_external_clone, external_bench_facts


def _spec(args=None, ports=None, volumes=None, mounts=None, env_raw=None,
          node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0"):
    return {
        "name": "ext-vllm", "namespace": "team-a", "labels": {"app": "ext-vllm"},
        "replicas": 2,
        "container": {
            "name": "server", "image": image, "command": [],
            "args": args if args is not None else ["--model", "/models/llama-3-8b", "--port", "8000"],
            "env": [], "env_raw": env_raw or [],
            "resources": {"limits": {"nvidia.com/gpu": "1"}},
            "ports": ports or [],
            "volume_mounts": mounts or [],
        },
        "volumes": volumes or [],
        "node_selector": node_selector,
        "tolerations": tolerations,
    }


# ─── external_bench_facts ────────────────────────────────────


def test_facts_served_model_name_wins():
    facts = external_bench_facts(_spec(args=["--model", "/models/llama", "--served-model-name", "llama-3"]))
    assert facts["served_model"] == "llama-3"
    assert facts["tokenizer"] == "/models/llama"
    assert facts["model_arg"] == "/models/llama"


def test_facts_model_fallback_and_equals_form():
    facts = external_bench_facts(_spec(args=["--model=/models/qwen"]))
    assert facts["served_model"] == "/models/qwen"
    assert facts["tokenizer"] == "/models/qwen"


def test_facts_missing_model_raises():
    import pytest
    with pytest.raises(ValueError):
        external_bench_facts(_spec(args=["--port", "8000"]))


def test_facts_maps_pvc_backing_the_model_path():
    spec = _spec(
        args=["--model", "/models/llama-3-8b"],
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "model-weights"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] == "model-weights"
    assert facts["pvc_mount_path"] == "/models"


def test_facts_no_pvc_when_volume_not_pvc():
    spec = _spec(
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "nfs": {"server": "n", "path": "/x"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] is None


# ─── build_external_clone ────────────────────────────────────


def test_clone_names_labels_replicas_and_service_port():
    manifests = build_external_clone(_spec(), name="bench-abc123")
    dep, svc = manifests
    assert dep["metadata"]["name"] == "bench-abc123-deployment"
    assert svc["metadata"]["name"] == "bench-abc123-service"
    assert dep["spec"]["replicas"] == 1
    sel = svc["spec"]["selector"]
    assert sel == dep["spec"]["template"]["metadata"]["labels"]
    port = svc["spec"]["ports"][0]
    assert port["port"] == 80 and port["targetPort"] == 8000  # from --port arg


def test_clone_service_port_falls_back_to_container_port_then_8000():
    m = build_external_clone(_spec(args=["--model", "/m"], ports=[{"containerPort": 9000}]), name="b1")
    assert m[1]["spec"]["ports"][0]["targetPort"] == 9000
    m = build_external_clone(_spec(args=["--model", "/m"]), name="b2")
    assert m[1]["spec"]["ports"][0]["targetPort"] == 8000


def test_clone_preserves_volumes_selector_tolerations_and_container():
    spec = _spec(
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "w"}}],
        node_selector={"gpu": "a100"},
        tolerations=[{"key": "gpu", "operator": "Exists"}],
        env_raw=[{"name": "VLLM_API_KEY", "value": "sk-x"}],
    )
    dep = build_external_clone(spec, name="b3")[0]
    pod = dep["spec"]["template"]["spec"]
    assert pod["volumes"] == spec["volumes"]
    assert pod["nodeSelector"] == {"gpu": "a100"}
    assert pod["tolerations"] == spec["tolerations"]
    c = pod["containers"][0]
    assert c["image"] == "vllm/vllm-openai:v0.6.0"
    assert c["volumeMounts"] == spec["container"]["volume_mounts"]
    assert c["env"] == spec["container"]["env_raw"]


def test_clone_overrides_resources_and_image():
    dep = build_external_clone(
        _spec(), name="b4",
        overrides={"resources": {"limits": {"nvidia.com/gpu": "2"}}, "image": "vllm/vllm-openai:v0.7.0"},
    )[0]
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert c["resources"] == {"limits": {"nvidia.com/gpu": "2"}}
    assert c["image"] == "vllm/vllm-openai:v0.7.0"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v`
Expected: new tests FAIL — `cannot import name 'build_external_clone'`.

- [ ] **Step 3: Implement**

Append to `backend/app/services/benchmark_serving.py` (module docstring mentions clone-bench already; add after `serving_resource_names`):

```python
def _arg_value(args: list, flag: str) -> str | None:
    """Value of ``--flag value`` or ``--flag=value`` in a CLI args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return str(args[i + 1])
        if isinstance(a, str) and a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def external_bench_facts(spec: dict) -> dict:
    """What the bench job needs to know about an external serving's clone.

    served_model is what `vllm bench serve --model` must send (the name the
    server reports): --served-model-name wins, else the --model value. The
    tokenizer is always the --model value. When the model path is backed by a
    PVC-mounted volume, expose it so the bench Job mounts the same weights for
    tokenizer loading.
    """
    args = spec["container"]["args"]
    model_arg = _arg_value(args, "--model")
    served = _arg_value(args, "--served-model-name") or model_arg
    if not served:
        raise ValueError("no --model/--served-model-name found in serving args")

    pvc_name = pvc_mount = None
    if model_arg:
        for m in spec["container"].get("volume_mounts") or []:
            mount_path = m.get("mountPath") or m.get("mount_path") or ""
            if mount_path and model_arg.startswith(mount_path.rstrip("/") + "/"):
                vol = next(
                    (v for v in (spec.get("volumes") or []) if v.get("name") == m.get("name")), None
                )
                claim = ((vol or {}).get("persistentVolumeClaim") or (vol or {}).get("persistent_volume_claim") or {})
                if claim.get("claimName") or claim.get("claim_name"):
                    pvc_name = claim.get("claimName") or claim.get("claim_name")
                    pvc_mount = mount_path
                break
    return {
        "served_model": served,
        "tokenizer": model_arg or served,
        "model_arg": model_arg or "",
        "pvc_name": pvc_name,
        "pvc_mount_path": pvc_mount,
    }


def _clone_target_port(container: dict) -> int:
    port = _arg_value(container.get("args") or [], "--port")
    if port and str(port).isdigit():
        return int(port)
    for p in container.get("ports") or []:
        cp = p.get("containerPort") or p.get("container_port")
        if cp:
            return int(cp)
    return 8000


def build_external_clone(spec: dict, *, name: str, overrides: dict | None = None) -> list[dict]:
    """Deployment + Service for a throwaway clone of a live external serving.

    Faithful copy of the first container and pod-level scheduling/volume fields;
    replicas forced to 1; names/Service port chosen so the existing ephemeral
    reconciler (serving_resource_names + serving_target_url on port 80) drives
    it unchanged. ``overrides`` may replace ``resources`` and/or ``image``.
    """
    names = serving_resource_names(name)
    labels = {"app": "llmops-bench-serving", "bench-serving": name}
    src = spec["container"]
    ov = overrides or {}
    container: dict = {
        "name": src.get("name") or "server",
        "image": ov.get("image") or src["image"],
        "args": list(src.get("args") or []),
        "env": list(src.get("env_raw") or []),
        "resources": ov.get("resources") or src.get("resources") or {},
        "volumeMounts": list(src.get("volume_mounts") or []),
    }
    if src.get("command"):
        container["command"] = list(src["command"])
    target_port = _clone_target_port(src)
    container["ports"] = [{"containerPort": target_port}]

    pod_spec: dict = {"containers": [container]}
    if spec.get("volumes"):
        pod_spec["volumes"] = spec["volumes"]
    if spec.get("node_selector"):
        pod_spec["nodeSelector"] = spec["node_selector"]
    if spec.get("tolerations"):
        pod_spec["tolerations"] = spec["tolerations"]

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names["deployment"], "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names["service"], "labels": labels},
        "spec": {
            "selector": labels,
            "ports": [{"port": 80, "targetPort": target_port}],
        },
    }
    return [deployment, service]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v && .venv/bin/ruff check app/services/benchmark_serving.py tests/test_bench_external_clone.py`
Expected: all pass; ruff clean. Also run `.venv/bin/pytest tests/test_k8s_clusters.py tests/test_benchmark_manifests.py -q` — existing benchmark tests still green.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/services/benchmark_serving.py backend/tests/test_bench_external_clone.py
git commit -m "feat(bench): external clone builder + bench facts from live serving spec"
```

---

### Task 3: API — `external_target` branch (create + preview)

**Files:**
- Modify: `backend/app/api/benchmarks.py`
- Test: `backend/tests/test_bench_external_clone.py` (append)

**Interfaces:**
- Consumes: `read_deployment` (T1), `external_bench_facts`/`build_external_clone` (T2), existing `k8s_for_cluster`, `ephemeral_model_name`, `K8sNotConfigured`.
- Produces: `CreateBenchmarkRequest.external_target: ExternalTarget | None` where
  ```python
  class ExternalTarget(BaseModel):
      cluster_id: str | None = None
      namespace: str
      deployment_name: str
  ```
  Create returns 201 with the run serialized as today (`ephemeral: true`, `deployment_id: null`). Errors: 400 `lm_eval`+external / unparseable args / combined with `deployment_id`; 404 serving gone; 502 K8s errors; 503 `K8sNotConfigured`. `/preview` returns the clone manifests for the same body.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_bench_external_clone.py`:

```python
import uuid

from app.services.benchmark_serving import serving_resource_names  # noqa: F401 (used in assertions)


def _exec_result(rows):
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    r.scalar_one_or_none.return_value = rows[0] if rows else None
    return r


EXTERNAL_BODY = {
    "tool": "vllm_serving",
    "params": {"num_prompts": 10},
    "external_target": {"cluster_id": None, "namespace": "team-a", "deployment_name": "ext-vllm"},
}


def _spec_for_api():
    return _spec(args=["--model", "/models/llama-3-8b", "--served-model-name", "llama-3", "--port", "8000"])


async def test_create_external_clone_run(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    fake_k8s.create_or_patch = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ephemeral"] is True
    assert body["model_name"] == "llama-3"
    # run row persisted with the snapshot contract the reconciler needs
    run = mock_db.add.call_args.args[0]
    assert run.deployment_id is None and run.ephemeral is True
    assert run.k8s_namespace == "team-a"
    snap = run.serving_snapshot
    assert snap["model_path"] == "llama-3"                # served name for the bench job
    assert snap["vllm_extra_args"] == _spec_for_api()["container"]["args"]
    assert run.params.get("tokenizer") == "/models/llama-3-8b"  # tokenizer preset from --model
    # clone applied into the serving's namespace
    ns, manifests = fake_k8s.create_or_patch.await_args.args
    assert ns == "team-a" and manifests[0]["kind"] == "Deployment"


async def test_create_external_missing_serving_404(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=None)
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 404


async def test_create_external_unparseable_args_400(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec(args=["--port", "8000"]))
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 400


async def test_create_external_lm_eval_400(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/benchmarks", json={**EXTERNAL_BODY, "tool": "lm_eval"})
    assert resp.status_code == 400


async def test_preview_external_returns_clone_manifests(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks/preview", json=EXTERNAL_BODY)
    assert resp.status_code == 200
    kinds = [m.get("kind") for m in resp.json().get("manifests", [])]
    assert "Deployment" in kinds and "Service" in kinds
```

- [ ] **Step 2: Run to verify fail**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v -k "external"`
Expected: FAIL — 422 (unknown field `external_target`) or missing branch.

- [ ] **Step 3: Implement**

In `backend/app/api/benchmarks.py`:

Imports — add (verify existing first): `from app.services.benchmark_serving import build_external_clone, external_bench_facts` (extend the existing `benchmark_serving` import block) and ensure `ephemeral_model_name`, `k8s_for_cluster`, `K8sNotConfigured` are already imported (they are, for the ephemeral path).

Request model — add nested model + field:

```python
class ExternalTarget(BaseModel):
    cluster_id: str | None = None
    namespace: str
    deployment_name: str
```
and on `CreateBenchmarkRequest`:
```python
    external_target: ExternalTarget | None = Field(
        None,
        description="Benchmark a discovered external serving by cloning its live spec "
        "(ephemeral; performance tools only). Mutually exclusive with deployment_id/model_name.",
    )
```

Create handler — insert this branch BEFORE the existing `if body.ephemeral:` block (after the tool/kind validation and NFS checks; mirror the surrounding style):

```python
    if body.external_target:
        if kind != "performance":
            raise HTTPException(status_code=400, detail="external_target supports performance benchmarks only")
        if body.deployment_id or body.ephemeral:
            raise HTTPException(status_code=400, detail="external_target is mutually exclusive with deployment_id/ephemeral")
        ext = body.external_target
        cluster_uuid = uuid.UUID(ext.cluster_id) if ext.cluster_id else None
        k8s = await k8s_for_cluster(db, cluster_uuid)
        try:
            spec = await k8s.read_deployment(ext.namespace, ext.deployment_name)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            logger.exception("Live spec read failed for %s/%s", ext.namespace, ext.deployment_name)
            raise HTTPException(status_code=502, detail="Failed to read the external serving's spec; check logs")
        if spec is None:
            raise HTTPException(status_code=404, detail="External serving no longer exists")
        try:
            facts = external_bench_facts(spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        params = dict(body.params)
        params.setdefault("tokenizer", facts["tokenizer"])
        params["external_source"] = {
            "cluster_id": ext.cluster_id, "namespace": ext.namespace, "deployment_name": ext.deployment_name,
        }
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=facts["served_model"],
            tool=body.tool,
            kind=kind,
            params=params,
            status="provisioning",
            cluster_id=cluster_uuid,
            deployment_id=None,
            ephemeral=True,
            k8s_namespace=ext.namespace,
            bench_image=body.image or spec["container"]["image"],
            created_by=user.user_id,
            serving_snapshot={
                "source": "external",
                "image": spec["container"]["image"],
                "vllm_extra_args": spec["container"]["args"],
                "env": {e["name"]: e["value"] for e in spec["container"]["env"] if e.get("value")},
                "model_path": facts["served_model"],
                "pvc_name": facts["pvc_name"],
                "pvc_mount_path": facts["pvc_mount_path"],
            },
        )
        run.serving_k8s_name = ephemeral_model_name(run.id)
        db.add(run)
        await db.flush()
        try:
            await k8s.create_or_patch(ext.namespace, build_external_clone(spec, name=run.serving_k8s_name, overrides=body.serving_overrides))
        except Exception:
            logger.exception("External clone provisioning failed for %s", run.id)
            raise HTTPException(status_code=502, detail="Failed to provision the benchmark clone; check logs")
        await db.commit()
        await db.refresh(run)
        return _serialize(run)
```

(Adapt the exact serializer/commit pattern to match the neighboring ephemeral branch — same `_serialize`, same commit/refresh order. `ephemeral_model_name` naming keeps `serving_resource_names`/`serving_target_url`/teardown working unchanged.)

Preview handler — add the analogous branch (before the existing ephemeral preview branch):

```python
    if body.external_target:
        if kind != "performance":
            raise HTTPException(status_code=400, detail="external_target supports performance benchmarks only")
        ext = body.external_target
        k8s = await k8s_for_cluster(db, uuid.UUID(ext.cluster_id) if ext.cluster_id else None)
        try:
            spec = await k8s.read_deployment(ext.namespace, ext.deployment_name)
        except Exception:
            return {"manifests": [], "note": "external_spec_unavailable"}
        if spec is None:
            return {"manifests": [], "note": "external_serving_missing"}
        name = ephemeral_model_name(uuid.uuid4())
        return {"manifests": build_external_clone(spec, name=name, overrides=body.serving_overrides)}
```

(Use the handler's existing `kind` variable. Match the existing preview branch's return shape — check how it wraps manifests before returning.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/wongibaek/Documents/litellm-ops/backend && .venv/bin/pytest tests/test_bench_external_clone.py -v && .venv/bin/pytest -q 2>&1 | tail -1 && .venv/bin/ruff check app/api/benchmarks.py tests/test_bench_external_clone.py`
Expected: file all green; full suite = no NEW failures beyond the ~21 baseline; ruff clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add backend/app/api/benchmarks.py backend/tests/test_bench_external_clone.py
git commit -m "feat(bench): external_target creates an ephemeral clone-bench run"
```

---

### Task 4: Frontend — grouped picker + clone/direct radio

**Files:**
- Modify: `frontend/src/app/(app)/admin/benchmarks/new/page.tsx`
- Modify: `frontend/src/hooks/use-api.ts` and/or `frontend/src/types/index.ts` (wherever `CreateBenchmarkRequest` is defined — grep first)
- Modify: `frontend/messages/en.json`, `frontend/messages/ko.json`

**Interfaces:**
- Consumes: `useExternalServings()` + `ExternalServing` (exported from use-api.ts on main); Task 3's `external_target` body field.

- [ ] **Step 1: Types**

Find `CreateBenchmarkRequest` (grep `CreateBenchmarkRequest` in `frontend/src/types/index.ts` / `use-api.ts`) and add:

```ts
  external_target?: {
    cluster_id: string | null;
    namespace: string;
    deployment_name: string;
  } | null;
```

- [ ] **Step 2: i18n (both files, benchmarkForm group)**

en.json:
```json
"targetGroupPortal": "Portal deployments",
"targetGroupExternal": "External servings (discovered)",
"modeCloneLabel": "Benchmark a fresh clone (recommended)",
"modeCloneHint": "Spins up a copy of the serving, benchmarks it, then deletes it. Needs spare GPU capacity; RWO volumes may fail to attach while the live pod holds them.",
"modeDirectLabel": "Benchmark the live serving",
"modeDirectHint": "Sends load to the running serving directly.",
"modeDirectUnavailable": "Direct mode is only available for Ready portal deployments.",
"statusNotReady": "not running"
```
ko.json:
```json
"targetGroupPortal": "포털 배포",
"targetGroupExternal": "외부 서빙 (감지됨)",
"modeCloneLabel": "새로 띄워서 벤치 (복제, 권장)",
"modeCloneHint": "서빙 복제본을 띄워 벤치 후 자동 삭제합니다. 여유 GPU가 필요하며, RWO 볼륨은 라이브 파드가 점유 중이면 붙지 않을 수 있습니다.",
"modeDirectLabel": "실행 중인 서빙에 직접",
"modeDirectHint": "실행 중인 서빙으로 부하를 직접 보냅니다.",
"modeDirectUnavailable": "직접 모드는 Ready 상태의 포털 배포에서만 가능합니다.",
"statusNotReady": "미실행"
```
Update the existing `deploymentHint` (en+ko) to drop the "Ready 상태만 표시됩니다." sentence (it no longer holds).

- [ ] **Step 3: Form wiring**

In `benchmarks/new/page.tsx` (explore first; current state ~lines 63-77, target select ~440-500):

- Fetch external servings: `const { data: external } = useExternalServings(); const servings = external?.servings ?? [];` and add `useExternalServings` (+ `type ExternalServing`) to the use-api import.
- Replace `readyDeployments` with `allDeployments = deployments ?? []` in the dropdown; keep a `selectedDeployment` lookup over ALL deployments. Option label gains a status suffix when not ready: `` `${d.model_name} — …${d.ready_replicas > 0 ? "" : ` · ${t("statusNotReady")}`}` ``.
- New state: `const [externalTarget, setExternalTarget] = useState<ExternalServing | null>(null);` and a mode state replacing the boolean UI meaning: keep `ephemeral` boolean as the source of truth (`true` = clone). Encode external options as `` `ext::${s.cluster_id ?? ""}::${s.namespace}::${s.deployment_name}` `` with an `externalKey(s)` helper; the select's value is `externalTarget ? externalKey(externalTarget) : deploymentId`.
- onChange of the target select:
  - value starts with `ext::` → `setExternalTarget(serving)`, `setDeploymentId("")`, `setEphemeral(true)`.
  - portal id → `setExternalTarget(null)`, `setDeploymentId(value)`, and if that deployment has `ready_replicas === 0` force `setEphemeral(true)`.
  - empty → clear both.
- The select JSX becomes two `<optgroup>`s (portal options as today + external group listing `s.deployment_name (s.engine · s.namespace)`), hidden/disabled when `kind === "accuracy"` (external group only — portal group stays).
- Replace the ephemeral checkbox block with a radio group shown when a deployment OR external target is chosen:
  - radio "clone" (`modeCloneLabel` + `modeCloneHint`) — checked when `ephemeral`.
  - radio "direct" (`modeDirectLabel` + `modeDirectHint`) — disabled when `externalTarget` is set OR the selected portal deployment isn't Ready; show `modeDirectUnavailable` hint when disabled.
  - Keep the `serving_overrides` JsonEditor visible when `ephemeral` (both portal and external).
- Body construction (`buildBody`/`handleSubmit` AND the preview `previewBody`): when `externalTarget` is set, send `external_target: { cluster_id: externalTarget.cluster_id, namespace: externalTarget.namespace, deployment_name: externalTarget.deployment_name }` plus `serving_overrides` when non-empty, and DO NOT send `deployment_id`/`ephemeral`/`model_name`. When accuracy tool is selected while an external target is chosen, clear the external target (effect or guard) — external is perf-only.
- Cluster/namespace inputs: when external target chosen, show them read-only/disabled populated from the serving (namespace = serving.namespace; cluster label = serving.cluster_name) — the backend derives placement from external_target, so do not send overriding namespace/cluster fields for external runs (check what the create handler does with body.namespace for the external branch — it ignores them; keep the UI honest by disabling).

- [ ] **Step 4: Gates**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend
python3 -c "import json; json.load(open('messages/en.json')); json.load(open('messages/ko.json')); print('json ok')"
npx tsc --noEmit && echo TSC_OK
npm run lint 2>&1 | tail -2
```
Expected: json ok; TSC_OK; 4-error pre-existing lint baseline.

- [ ] **Step 5: Commit**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git add -A frontend/
git commit -m "feat(frontend): bench form picks external servings; clone/direct radio"
```

---

### Task 5: Verification + smoke + wrap-up

- [ ] **Step 1: Full backend suite + ruff**

```bash
cd /Users/wongibaek/Documents/litellm-ops/backend
.venv/bin/pytest -q 2>&1 | tail -1
.venv/bin/ruff check app/ tests/ 2>&1 | tail -1
```
Expected: no NEW failures (~21 baseline); ruff ~78 pre-existing, 0 new.

- [ ] **Step 2: Frontend build**

```bash
cd /Users/wongibaek/Documents/litellm-ops/frontend && npm run build 2>&1 | tail -4
```
Expected: success.

- [ ] **Step 3: Rebuild + smoke**

```bash
cd /Users/wongibaek/Documents/litellm-ops
docker compose up -d --build backend frontend
# wait healthy, then:
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8002/api/benchmarks          # 401 (auth)
```
Browser: http://localhost:3003/admin/benchmarks/new — target dropdown shows the two groups (external empty locally without kubeconfig — expected), radio replaces the checkbox, non-Ready portal deployment (opt-125m) is listed and forces clone mode.

- [ ] **Step 4: Wrap up**

Use superpowers:finishing-a-development-branch (push + PR per session convention).
