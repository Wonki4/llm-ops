# Self-Serving Benchmark Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the two performance "spin up a fresh serving and benchmark it" flows (portal-deployment clone `ephemeral`, external-serving clone `external_target`) from Deployment+Service+separate-Job into a single self-serving Job that runs `vllm serve` and `vllm bench serve` in one pod.

**Architecture:** A new `build_self_serving_bench_job` transforms a serving Deployment manifest + an explicit serve argv into one `batch/v1` Job whose container backgrounds the serve command, polls `/health` on localhost, runs the bench against localhost, and emits the RESULT marker. The two create/preview branches build+submit this Job directly (status `pending`) instead of provisioning a serving. Spec: `docs/superpowers/specs/2026-07-09-self-serving-bench-job-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async; Kubernetes batch/v1 Jobs; pytest.

## Global Constraints

- Scope is EXACTLY the two performance flows: `body.external_target` (always performance) and `body.ephemeral` with `kind == "performance"`. The `ephemeral` + accuracy (lm_eval) path and the direct / LiteLLM-model paths are UNCHANGED and still route through the current code.
- Converted runs: `status="pending"` (never `provisioning`), `k8s_job_name = job_name_for(run.id)`, `serving_k8s_name = None`, `serving_torn_down = True`, `ephemeral = True` (bookkeeping). The Job is created at submit time via `k8s.create_job`; NO Deployment/Service is created for these flows.
- Bench targets `http://localhost:<port>` inside the pod; `<port>` is the port the serve command binds (ephemeral: `VLLM_PORT`=8000; external: `_clone_target_port(spec["container"])`).
- The `vllm bench serve` argv is identical to today's `build_vllm_bench_job` output (same flags, same params passthrough, same `extra_args` shlex handling) — extracted into a shared helper so both builders stay DRY (no behavior change to the existing direct/model bench Job).
- Readiness poll window ≈ 30 min (matches today's `PROVISION_TIMEOUT_S`): `HEALTH_TRIES=900`, `HEALTH_INTERVAL=2` seconds.
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Use `datetime.UTC` not `timezone.utc` (repo ruff UP017 on py3.14). Imports top-of-file.
- Branch `feat/self-serving-bench-job` (already checked out). Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Shared bench argv helper + `build_self_serving_bench_job`

**Files:**
- Modify: `backend/app/services/benchmark_manifests.py` (extract `_vllm_bench_argv`; add `build_self_serving_bench_job`)
- Test: `backend/tests/test_self_serving_bench.py`

**Interfaces:**
- Consumes: `job_name_for(run.id)`, existing param handling in `build_vllm_bench_job`.
- Produces:
  - `_vllm_bench_argv(run, *, target_base_url, served_model, tokenizer) -> list[str]` — the `["vllm","bench","serve", ...]` argv (no shell).
  - `build_self_serving_bench_job(run, *, serving_deployment: dict, serve_argv: list[str], port: int, api_key: str, served_model: str, tokenizer: str | None = None, backoff_limit: int = 0, ttl_seconds_after_finished: int = 7*24*3600) -> dict` — one Job dict.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_self_serving_bench.py`:

```python
"""Self-serving benchmark Job builder (serve + bench in one pod)."""

import types
import uuid

from app.services.benchmark_manifests import (
    build_self_serving_bench_job,
    job_name_for,
)


def _run(kind="performance", params=None):
    return types.SimpleNamespace(
        id=uuid.UUID(int=7),
        tool="vllm_serving",
        kind=kind,
        params=params or {"num_prompts": 50, "random_input_len": 128, "random_output_len": 32},
        k8s_namespace="bench",
    )


def _serving_deployment():
    # Shaped like build_deployment / build_external_clone[0] output.
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "x-deployment"},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "vllm",
                            "image": "vllm/vllm-openai:v0.6.0",
                            "args": ["--model", "/models/m", "--port", "8000"],
                            "ports": [{"containerPort": 8000}],
                            "resources": {"limits": {"nvidia.com/gpu": "1"}},
                            "volumeMounts": [{"name": "model-weights", "mountPath": "/models"}],
                            "env": [{"name": "HF_HOME", "value": "/models/.hf"}],
                        }
                    ],
                    "volumes": [{"name": "model-weights", "persistentVolumeClaim": {"claimName": "w"}}],
                    "nodeSelector": {"gpu-type": "H100"},
                    "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists"}],
                }
            },
        },
    }


def test_job_is_single_batch_job_named_by_run():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m",
    )
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == job_name_for(uuid.UUID(int=7))
    assert job["spec"]["backoffLimit"] == 0
    assert job["metadata"]["labels"]["app"] == "llmops-benchmark"


def test_reuses_serving_image_gpu_mount_and_scheduling():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m",
    )
    pod = job["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert c["image"] == "vllm/vllm-openai:v0.6.0"
    assert c["resources"] == {"limits": {"nvidia.com/gpu": "1"}}
    assert {"name": "model-weights", "mountPath": "/models"} in c["volumeMounts"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "w"
    assert pod["nodeSelector"] == {"gpu-type": "H100"}
    assert pod["tolerations"][0]["key"] == "nvidia.com/gpu"
    assert pod["restartPolicy"] == "Never"


def test_script_backgrounds_serve_polls_health_and_benches_localhost():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="EMPTY", served_model="/models/m", tokenizer="/models/m",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    # serve is backgrounded
    assert "vllm serve /models/m --port 8000 &" in script
    # readiness poll targets localhost:8000/health
    assert "http://localhost:8000/health" in script
    # bench targets the same localhost base-url and the served model
    assert "vllm bench serve" in script
    assert "http://localhost:8000" in script
    assert "--model /models/m" in script
    # RESULT marker emitted, server killed
    assert "<<<RESULT>>>" in script
    assert "kill" in script


def test_env_carries_serving_env_plus_openai_key():
    job = build_self_serving_bench_job(
        _run(), serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m", "--port", "8000"],
        port=8000, api_key="sk-secret", served_model="/models/m",
    )
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["OPENAI_API_KEY"] == "sk-secret"
    assert env["HF_HOME"] == "/models/.hf"           # serving env preserved
    assert env["BENCH_RUN_ID"] == str(uuid.UUID(int=7))


def test_bench_argv_respects_params():
    job = build_self_serving_bench_job(
        _run(params={"num_prompts": 300, "random_input_len": 512, "request_rate": 8, "ignore_eos": True}),
        serving_deployment=_serving_deployment(),
        serve_argv=["vllm", "serve", "/models/m"],
        port=8000, api_key="EMPTY", served_model="srv",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "--num-prompts 300" in script
    assert "--random-input-len 512" in script
    assert "--request-rate 8" in script
    assert "--ignore-eos" in script
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_self_serving_bench.py -q
```

Expected: ImportError (`build_self_serving_bench_job` missing).

- [ ] **Step 3: Extract the shared bench-argv helper**

In `backend/app/services/benchmark_manifests.py`, refactor `build_vllm_bench_job` so the argv construction (everything from `args = ["vllm","bench","serve", ...]` through the `extra_args` shlex passthrough, i.e. the current body that builds `args`) moves into a new module-level function, and `build_vllm_bench_job` calls it. Extract verbatim — do not change any flag:

```python
def _vllm_bench_argv(
    run: CustomBenchmarkRun,
    *,
    target_base_url: str,
    served_model: str,
    tokenizer: str | None,
) -> list[str]:
    """The `vllm bench serve` argv (no shell). Shared by the URL-target bench
    Job and the self-serving bench Job."""
    p = run.params or {}
    args = [
        "vllm", "bench", "serve",
        "--backend", "openai-chat",
        "--base-url", target_base_url,
        "--endpoint", "/v1/chat/completions",
        "--model", served_model,
        "--tokenizer", tokenizer or served_model,
        "--dataset-name", "random",
        "--random-input-len", str(int(p.get("random_input_len", 1024))),
        "--random-output-len", str(int(p.get("random_output_len", 128))),
        "--num-prompts", str(int(p.get("num_prompts", 200))),
        "--percentile-metrics", "ttft,tpot,itl,e2el",
        "--metric-percentiles", "90,99",
        "--seed", str(int(p.get("seed", 0))),
        "--save-result", "--result-dir", "/tmp", "--result-filename", "r.json",
    ]
    if p.get("request_rate") not in (None, ""):
        args += ["--request-rate", str(float(p["request_rate"]))]
    if p.get("max_concurrency") not in (None, ""):
        args += ["--max-concurrency", str(int(p["max_concurrency"]))]
    if p.get("goodput") not in (None, ""):
        args += ["--goodput", *str(p["goodput"]).split()]
    if p.get("ignore_eos"):
        args += ["--ignore-eos"]
    used_flags = {a for a in args if a.startswith("--")}
    for key, val in p.items():
        if key in _NON_CLI_PARAMS or val in (None, ""):
            continue
        flag = "--" + key.replace("_", "-")
        if flag in used_flags:
            continue
        if isinstance(val, bool):
            if val:
                args.append(flag)
        else:
            args += [flag, str(val)]
    extra_args = p.get("extra_args")
    if isinstance(extra_args, str) and extra_args.strip():
        args += shlex.split(extra_args)
    return args
```

Then in `build_vllm_bench_job`, replace the inline `args = [...]` construction (through the `extra_args` block) with:

```python
    args = _vllm_bench_argv(run, target_base_url=target_base_url, served_model=served_model, tokenizer=tokenizer)
```

Leave the rest of `build_vllm_bench_job` (the `bench_cmd`, `emit`, `script`, volumes, Job dict) exactly as-is. This is a pure refactor — existing `test_benchmark_manifests.py` must still pass unchanged.

- [ ] **Step 4: Add `build_self_serving_bench_job`**

Add after `build_vllm_bench_job` (uses `shlex`, already imported):

```python
_HEALTH_TRIES = 900
_HEALTH_INTERVAL = 2


def build_self_serving_bench_job(
    run: CustomBenchmarkRun,
    *,
    serving_deployment: dict,
    serve_argv: list[str],
    port: int,
    api_key: str,
    served_model: str,
    tokenizer: str | None = None,
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 7 * 24 * 3600,
) -> dict:
    """One Job that serves the model and benchmarks it in the same pod.

    Reuses the serving container's image/GPU/mounts/env and pod-level
    volumes/scheduling from ``serving_deployment`` (a Deployment manifest as
    built by build_deployment / build_external_clone), but replaces the run
    command with: background ``serve_argv``, poll ``/health`` on localhost until
    ready, run ``vllm bench serve`` against ``http://localhost:<port>``, emit the
    RESULT marker, stop the server.
    """
    name = job_name_for(run.id)
    src_pod = serving_deployment["spec"]["template"]["spec"]
    src_c = src_pod["containers"][0]

    base_url = f"http://localhost:{port}"
    serve_cmd = " ".join(shlex.quote(a) for a in serve_argv)
    bench_cmd = " ".join(
        shlex.quote(a)
        for a in _vllm_bench_argv(run, target_base_url=base_url, served_model=served_model, tokenizer=tokenizer)
    )
    emit = 'echo "<<<RESULT>>>{\\"metrics\\": $(tr -d \'\\n\' < /tmp/r.json)}"'
    wait = (
        "python - <<'PY'\n"
        "import urllib.request, time, sys\n"
        f"for _ in range({_HEALTH_TRIES}):\n"
        "    try:\n"
        f"        urllib.request.urlopen('{base_url}/health', timeout=3); sys.exit(0)\n"
        "    except Exception:\n"
        f"        time.sleep({_HEALTH_INTERVAL})\n"
        "sys.exit(1)\n"
        "PY"
    )
    script = (
        "set -m\n"
        f"{serve_cmd} &\n"
        "SRV=$!\n"
        f"{wait}\n"
        "set -e\n"
        f"{bench_cmd}\n"
        f"{emit}\n"
        "kill $SRV 2>/dev/null || true\n"
    )

    env = list(src_c.get("env") or [])
    env = [e for e in env if e.get("name") not in ("OPENAI_API_KEY", "BENCH_RUN_ID")]
    env += [
        {"name": "OPENAI_API_KEY", "value": api_key},
        {"name": "BENCH_RUN_ID", "value": str(run.id)},
    ]

    container = {
        "name": "serve-bench",
        "image": src_c["image"],
        "imagePullPolicy": "IfNotPresent",
        "command": ["sh", "-c", script],
        "env": env,
        "resources": src_c.get("resources") or {},
        "volumeMounts": list(src_c.get("volumeMounts") or []),
    }
    pod_spec: dict = {"restartPolicy": "Never", "containers": [container]}
    if src_pod.get("volumes"):
        pod_spec["volumes"] = src_pod["volumes"]
    if src_pod.get("nodeSelector"):
        pod_spec["nodeSelector"] = src_pod["nodeSelector"]
    if src_pod.get("tolerations"):
        pod_spec["tolerations"] = src_pod["tolerations"]

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": run.k8s_namespace,
            "labels": {"app": "llmops-benchmark", "bench-tool": run.tool, "bench-kind": run.kind},
        },
        "spec": {
            "backoffLimit": backoff_limit,
            "ttlSecondsAfterFinished": ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": {"app": "llmops-benchmark", "job-name": name}},
                "spec": pod_spec,
            },
        },
    }
```

- [ ] **Step 5: Run tests + gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_self_serving_bench.py tests/test_benchmark_manifests.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: new file green (6 tests); `test_benchmark_manifests.py` still green (pure refactor); suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/benchmark_manifests.py backend/tests/test_self_serving_bench.py
git commit -m "feat(bench): self-serving bench Job builder + shared vllm bench argv helper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Rewire external + ephemeral create/preview to the single Job

**Files:**
- Modify: `backend/app/api/benchmarks.py` (external create branch ~328–406; ephemeral create branch ~409–457; preview branches ~589–627; imports)
- Test: `backend/tests/test_bench_external_clone.py`, `backend/tests/test_benchmarks_ephemeral.py` (create/inspect as needed)

**Interfaces:**
- Consumes: `build_self_serving_bench_job` (Task 1); existing `serving_cli`, `external_bench_facts`, `_clone_target_port`, `build_external_clone`, `build_ephemeral_deployment`, `build_deployment` (import as needed), `k8s.create_job`, `job_name_for`, `VLLM_PORT`.
- Produces: converted runs with `status="pending"`, a created Job, `serving_k8s_name=None`, `serving_torn_down=True`.

- [ ] **Step 1: Write/adjust the failing tests**

In `backend/tests/test_bench_external_clone.py`, the existing `test_create_external_clone_run` asserts the OLD behavior (`create_or_patch` clone applied, snapshot vllm_extra_args, status provisioning). Replace its body with the new contract (keep the same test name so coverage is continuous):

```python
async def test_create_external_clone_run(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    fake_k8s.create_job = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"          # no provisioning phase
    assert body["ephemeral"] is True
    assert body["model_name"] == "llama-3"
    run = mock_db.add.call_args.args[0]
    assert run.status == "pending"
    assert run.serving_k8s_name is None          # no separate serving object
    assert run.serving_torn_down is True
    assert run.k8s_job_name == job_name_for(run.id)
    # A single self-serving Job was created into the serving's namespace
    assert fake_k8s.create_job.await_count == 1   # exactly one Job, no serving clone
    ns, manifest = fake_k8s.create_job.await_args.args
    assert ns == "team-a" and manifest["kind"] == "Job"
    script = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "vllm bench serve" in script and "http://localhost:" in script
```

Import `job_name_for` at the top of the test file (`from app.services.benchmark_manifests import job_name_for`). Update `test_create_external_api_key_override` similarly: assert `run.serving_snapshot.get("api_key_override") == "sk-gate"` still holds (snapshot kept for display) OR that the Job env `OPENAI_API_KEY == "sk-gate"` — pick the Job-env assertion since that is the new source of truth:

```python
async def test_create_external_api_key_override(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=_spec_for_api())
    fake_k8s.create_job = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json={**EXTERNAL_BODY, "api_key": "sk-gate"})
    assert resp.status_code == 201
    _ns, manifest = fake_k8s.create_job.await_args.args
    env = {e["name"]: e["value"] for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["OPENAI_API_KEY"] == "sk-gate"
```

For the ephemeral path, add a test (new file `backend/tests/test_benchmarks_ephemeral.py` if none exists; otherwise append to the existing ephemeral test module — grep `body.ephemeral`/`ephemeral requires deployment_id` in tests first):

```python
"""Ephemeral (portal-deployment clone) perf bench now runs as one self-serving Job."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.benchmark_manifests import job_name_for


async def test_ephemeral_perf_creates_single_self_serving_job(client_for_user, super_user, mock_db):
    base = MagicMock()
    base.id = uuid.uuid4(); base.model_name = "m"; base.image = "vllm/vllm-openai:v0.6.0"
    base.model_path = "/models/m"; base.replicas = 1; base.gpu_count = 1
    base.gpu_resource_key = "nvidia.com/gpu"; base.cpu_request = None; base.cpu_limit = None
    base.memory_request = None; base.memory_limit = None; base.node_selector = {}
    base.tolerations = None; base.pvc_name = "w"; base.pvc_mount_path = "/models"
    base.vllm_extra_args = []; base.env = {}
    result = MagicMock(); result.scalar_one_or_none.return_value = base
    mock_db.execute = AsyncMock(return_value=result)
    fake_k8s = MagicMock(); fake_k8s.create_job = AsyncMock()
    body = {"tool": "vllm_serving", "params": {"num_prompts": 10}, "ephemeral": True, "deployment_id": str(base.id)}
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.status == "pending" and run.serving_k8s_name is None and run.serving_torn_down is True
    assert run.k8s_job_name == job_name_for(run.id)
    ns, manifest = fake_k8s.create_job.await_args.args
    assert manifest["kind"] == "Job"
    script = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "vllm serve" in script and "vllm bench serve" in script
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_bench_external_clone.py tests/test_benchmarks_ephemeral.py -q
```

Expected: the rewritten/added tests fail against the current provisioning code (create_or_patch used, status provisioning).

- [ ] **Step 3: Rewire the EXTERNAL create branch**

In `backend/app/api/benchmarks.py`, replace the external branch body (from building `run` through the `create_or_patch` block, roughly lines 361–437 in the current file — i.e. everything after the `facts = external_bench_facts(spec)` / 400 guard up to `return _serialize(run)`) with:

```python
        params = dict(body.params)
        params.setdefault("tokenizer", facts["tokenizer"])
        params["external_source"] = {
            "cluster_id": ext.cluster_id,
            "namespace": ext.namespace,
            "deployment_name": ext.deployment_name,
        }
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=facts["served_model"],
            tool=body.tool,
            kind=kind,
            params=params,
            status="pending",
            cluster_id=ext_cluster_uuid,
            deployment_id=None,
            ephemeral=True,
            k8s_namespace=ext.namespace,
            bench_image=body.image or spec["container"]["image"],
            created_by=user.user_id,
            serving_snapshot={
                "source": "external",
                "image": spec["container"]["image"],
                "vllm_extra_args": serving_cli(spec["container"]),
                "env": {e["name"]: e["value"] for e in spec["container"]["env"] if e.get("value")},
                "model_path": facts["served_model"],
                "pvc_name": facts["pvc_name"],
                "pvc_mount_path": facts["pvc_mount_path"],
            },
        )
        run.k8s_job_name = job_name_for(run.id)
        run.serving_torn_down = True  # single Job — no separate serving to tear down
        clone = build_external_clone(spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides)[0]
        api_key = body.api_key or serving_api_key(serving_cli(spec["container"]), run.serving_snapshot["env"])
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        job = build_self_serving_bench_job(
            run,
            serving_deployment=clone,
            serve_argv=serving_cli(spec["container"]),
            port=_clone_target_port(spec["container"]),
            api_key=api_key,
            served_model=facts["served_model"],
            tokenizer=params.get("tokenizer"),
        )
        db.add(run)
        await db.flush()
        await db.refresh(run)
        try:
            await ext_k8s.create_job(ext.namespace, job)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.exception("Self-serving bench Job create failed for benchmark %s", run.id)
            run.status = "failed"
            run.error_message = f"Benchmark Job create failed: {e}"
            await db.flush()
            raise HTTPException(status_code=502, detail="Failed to create the benchmark Job; check logs")
        return _serialize(run)
```

Add imports at top-of-file: `build_self_serving_bench_job` (from `app.services.benchmark_manifests`), and `_clone_target_port` + `serving_cli` (from `app.services.benchmark_serving`) if not already imported. Remove now-unused imports only if ruff flags them (`ephemeral_manifests` may still be used by the accuracy path — check before removing).

- [ ] **Step 4: Rewire the EPHEMERAL create branch**

For `body.ephemeral`, the perf path builds the single Job; the accuracy path keeps today's provisioning. Replace the ephemeral branch body (lines ~457–498) with:

```python
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=base.model_name,
            tool=body.tool,
            kind=kind,
            params=body.params,
            status="provisioning" if kind != "performance" else "pending",
            cluster_id=cluster_uuid,
            k8s_namespace=namespace,
            deployment_id=base.id,
            ephemeral=True,
            bench_image=(body.image or base.image) if kind == "performance" else image,
            created_by=user.user_id,
        )
        name = ephemeral_model_name(run.id)
        eph = build_ephemeral_deployment(
            base, name=name, namespace=namespace, overrides=body.serving_overrides
        )
        run.serving_snapshot = _serving_snapshot(eph)
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        run.k8s_job_name = job_name_for(run.id)

        if kind == "performance":
            # Single self-serving Job — no separate serving Deployment.
            run.serving_torn_down = True
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            api_key = body.api_key or serving_api_key(eph.vllm_extra_args, eph.env)
            job = build_self_serving_bench_job(
                run,
                serving_deployment=build_deployment(eph),
                serve_argv=serve_argv,
                port=VLLM_PORT,
                api_key=api_key,
                served_model=eph.model_path,
                tokenizer=(run.params or {}).get("tokenizer"),
            )
            db.add(run)
            await db.flush()
            await db.refresh(run)
            try:
                await k8s.create_job(namespace, job)
            except K8sNotConfigured as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception as e:
                logger.exception("Self-serving bench Job create failed for benchmark %s", run.id)
                run.status = "failed"
                run.error_message = f"Benchmark Job create failed: {e}"
                await db.flush()
                raise HTTPException(status_code=502, detail="Failed to create the benchmark Job; check logs")
            return _serialize(run)

        # Accuracy (lm_eval): keep the provisioning path — serving + later Job.
        run.serving_k8s_name = name
        db.add(run)
        await db.flush()
        await db.refresh(run)
        try:
            await k8s.create_or_patch(namespace, ephemeral_manifests(eph))
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.exception("Ephemeral serving create failed for benchmark %s", run.id)
            run.status = "failed"
            run.error_message = f"Ephemeral serving create failed: {e}"
            run.serving_torn_down = True
            await db.flush()
            raise HTTPException(status_code=502, detail="Failed to provision serving; check logs")
        return _serialize(run)
```

Add `build_deployment` and `VLLM_PORT` to the imports from `app.services.model_deployment_manifests` (they live there). `serving_api_key` is already imported.

- [ ] **Step 5: Rewire the PREVIEW branches**

For the external preview branch, replace `manifests.extend(build_external_clone(...))` with the single Job:

```python
        run.model_name = ext.deployment_name
        clone = build_external_clone(spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides)[0]
        facts = external_bench_facts(spec)
        manifests.append(
            build_self_serving_bench_job(
                run,
                serving_deployment=clone,
                serve_argv=serving_cli(spec["container"]),
                port=_clone_target_port(spec["container"]),
                api_key="<redacted>",
                served_model=facts["served_model"],
                tokenizer=(body.params or {}).get("tokenizer") or facts["tokenizer"],
            )
        )
```

(Wrap the `external_bench_facts(spec)` call to return `{"manifests": [], "note": "external_spec_unparseable"}` on `ValueError`, mirroring the create guard.)

For the ephemeral preview branch, when `kind == "performance"` render the single Job; keep the current Deployment+Service+Job rendering for accuracy:

```python
        name = ephemeral_model_name(run.id)
        eph = build_ephemeral_deployment(base, name=name, namespace=namespace, overrides=body.serving_overrides)
        run.model_name = base.model_name
        if kind == "performance":
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            manifests.append(
                build_self_serving_bench_job(
                    run,
                    serving_deployment=build_deployment(eph),
                    serve_argv=serve_argv,
                    port=VLLM_PORT,
                    api_key="<redacted>",
                    served_model=eph.model_path,
                    tokenizer=(body.params or {}).get("tokenizer"),
                )
            )
        else:
            manifests.extend(ephemeral_manifests(eph))
            manifests.append(
                _build_bench_job(
                    run, deployment=eph, target_base=serving_target_url(name, namespace),
                    api_key=body.api_key or serving_api_key(eph.vllm_extra_args, eph.env),
                    image_override=body.image or None,
                )
            )
```

- [ ] **Step 6: Run tests + gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_bench_external_clone.py tests/test_benchmarks_ephemeral.py tests/test_benchmark_manifests.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: targeted green; suite baseline unchanged; ruff 78. If a pre-existing external/ephemeral test asserted `status == "provisioning"` or `create_or_patch`, update it to the new contract (note each such change in the report).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/benchmarks.py backend/tests/test_bench_external_clone.py backend/tests/test_benchmarks_ephemeral.py
git commit -m "feat(bench): external + ephemeral perf run as one self-serving Job (no provisioning)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Reconciler / cancel / teardown parity for single-Job runs

**Files:**
- Modify: `backend/app/jobs/reconcile_benchmarks.py` (only if a guard is needed for null `serving_k8s_name`)
- Test: `backend/tests/test_reconcile_benchmarks.py` (create/append)

**Interfaces:**
- Consumes: converted runs (`status="pending"`, `serving_k8s_name=None`, `serving_torn_down=True`, `k8s_job_name` set) from Task 2.
- Produces: verified reconciler behavior — a converted run is polled by `_drive_job` straight to terminal, and neither `_teardown_serving` nor the safety sweep touches it.

- [ ] **Step 1: Write the failing/guard tests**

Create or append to `backend/tests/test_reconcile_benchmarks.py`:

```python
"""Reconciler handles single-Job (self-serving) runs without a serving object."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.reconcile_benchmarks import _drive_job, _teardown_serving


def _run(**kw):
    base = dict(
        id=uuid.uuid4(), status="pending", k8s_namespace="bench",
        k8s_job_name="llmops-bench-x", serving_k8s_name=None,
        serving_torn_down=True, ephemeral=True, kind="performance",
        started_at=None, params={}, serving_snapshot={},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


async def test_drive_job_polls_single_job_run_to_running():
    run = _run()
    k8s = MagicMock()
    k8s.read_job_status = AsyncMock(return_value={"active": 1, "succeeded": 0, "failed": 0, "conditions": []})
    n = await _drive_job(k8s, run)
    assert n == 1 and run.status == "running"


async def test_teardown_noop_when_no_serving_object():
    run = _run(status="succeeded")
    k8s = MagicMock()
    k8s.delete = AsyncMock()
    await _teardown_serving(k8s, run)
    k8s.delete.assert_not_called()          # nothing to delete
    assert run.serving_torn_down is True
```

- [ ] **Step 2: Run to verify current behavior**

```bash
cd backend && .venv/bin/python -m pytest tests/test_reconcile_benchmarks.py -q
```

`test_teardown_noop_when_no_serving_object` should already pass (the current `_teardown_serving` early-returns True when `serving_k8s_name` is falsy). `test_drive_job_polls_single_job_run_to_running` should already pass (converted runs have `k8s_job_name`). If BOTH pass, no reconciler code change is needed — record that in the report and skip Step 3. If either fails, fix minimally in Step 3.

- [ ] **Step 3: Fix only if a test failed**

If `_teardown_serving` or the safety sweep mishandles null `serving_k8s_name`, adjust the guard so a converted run is never treated as holding a serving. Example (only if needed): in the terminal-teardown call site and the sweep query, the runs are already excluded by `serving_torn_down=True`; no change should be required. Do NOT change behavior for accuracy-ephemeral runs (which still set `serving_k8s_name`).

- [ ] **Step 4: Confirm `_drive_provisioning` is still reachable (accuracy)**

```bash
grep -n 'status="provisioning"\|status = "provisioning"' backend/app/api/benchmarks.py
```

Expected: the accuracy-ephemeral branch still writes `provisioning` (Task 2 kept it), so `_drive_provisioning` and the `"provisioning"` status handling in the reconciler remain live. Record this — do NOT delete `_drive_provisioning`.

- [ ] **Step 5: Gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_reconcile_benchmarks.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: reconciler tests green; suite baseline unchanged; ruff 78.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_reconcile_benchmarks.py backend/app/jobs/reconcile_benchmarks.py
git commit -m "test(bench): reconciler drives single-Job runs; teardown no-op without serving

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
(If Step 3 changed no source, commit only the test file.)
