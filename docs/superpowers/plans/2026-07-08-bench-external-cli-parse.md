# External Serving CLI Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognize `vllm serve <model>` positionals, SGLang `--model-path`, command-placed flags, and `sh -c` shell strings when extracting bench facts from a discovered external serving.

**Architecture:** One normalization helper (`serving_cli`) merges `command + args` and shlex-expands shell-string tokens; `external_bench_facts`, `_clone_target_port`, and the external snapshot's `vllm_extra_args` all parse against it. Spec: `docs/superpowers/specs/2026-07-08-bench-external-cli-parse-design.md`.

**Tech Stack:** FastAPI backend, pytest.

## Global Constraints

- Existing flag-form behavior unchanged: `--served-model-name` still beats the model argument; tokenizer/PVC detection still key off the model argument; existing tests in `test_bench_external_clone.py` must pass without assertion changes (the `_spec` helper may gain a `command=` parameter).
- Model-argument priority, exactly: `--model` > `serve` positional > `--model-path`.
- `serving_cli` is PUBLIC (imported by `app/api/benchmarks.py`); `_positional_model` stays private.
- No schema/API-shape/frontend changes.
- Backend gates: `cd backend && .venv/bin/python -m pytest tests/ -q` 0 NEW failures (baseline 21); `.venv/bin/ruff check app/ tests/` 0 NEW (baseline 78). Imports top-of-file (ruff E402/I001).
- Branch `fix/bench-external-cli-parse` (already checked out).
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: CLI normalization + parsing fix

**Files:**
- Modify: `backend/app/services/benchmark_serving.py` (imports; `_arg_value` region lines 115–170)
- Modify: `backend/app/api/benchmarks.py` (import block ~line 36; snapshot line 374)
- Test: `backend/tests/test_bench_external_clone.py`

**Interfaces:**
- Produces: `serving_cli(container: dict) -> list[str]` in `app.services.benchmark_serving`, imported by `app.api.benchmarks`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_bench_external_clone.py`:

1. Extend the `_spec` helper with a `command=None` keyword: signature becomes
   `def _spec(args=None, ports=None, volumes=None, mounts=None, env_raw=None, node_selector=None, tolerations=None, image="vllm/vllm-openai:v0.6.0", command=None):`
   and the container's `"command": [],` line becomes `"command": command or [],`.

2. Append after `test_facts_no_pvc_when_volume_not_pvc`:

```python
def test_facts_positional_model_vllm_serve():
    facts = external_bench_facts(
        _spec(command=["vllm", "serve", "/models/qwen-7b"], args=["--port", "8000"])
    )
    assert facts["served_model"] == "/models/qwen-7b"
    assert facts["tokenizer"] == "/models/qwen-7b"
    assert facts["model_arg"] == "/models/qwen-7b"


def test_facts_sglang_model_path():
    facts = external_bench_facts(
        _spec(args=["--model-path", "/models/qwen", "--served-model-name", "qwen-2"])
    )
    assert facts["served_model"] == "qwen-2"
    assert facts["tokenizer"] == "/models/qwen"


def test_facts_flags_in_command_only():
    facts = external_bench_facts(
        _spec(command=["python", "-m", "vllm.entrypoints.openai.api_server", "--model", "/m/x"], args=[])
    )
    assert facts["served_model"] == "/m/x"


def test_facts_sh_c_shell_string():
    spec = _spec(
        command=["sh", "-c", "vllm serve /models/y --served-model-name y-8b --port 9000"],
        args=[],
    )
    facts = external_bench_facts(spec)
    assert facts["served_model"] == "y-8b"
    assert facts["tokenizer"] == "/models/y"


def test_facts_positional_model_pvc_detection():
    spec = _spec(
        command=["vllm", "serve", "/models/llama-3-8b"],
        args=[],
        mounts=[{"name": "weights", "mountPath": "/models"}],
        volumes=[{"name": "weights", "persistentVolumeClaim": {"claimName": "model-weights"}}],
    )
    facts = external_bench_facts(spec)
    assert facts["pvc_name"] == "model-weights"
    assert facts["pvc_mount_path"] == "/models"


def test_facts_missing_command_and_args_raises_cleanly():
    spec = _spec(args=[])
    spec["container"].pop("args")
    spec["container"].pop("command")
    with pytest.raises(ValueError):
        external_bench_facts(spec)


def test_clone_target_port_from_sh_c_command():
    spec = _spec(command=["sh", "-c", "vllm serve /m --port 9000"], args=[])
    manifests = build_external_clone(spec, name="bench-x")
    svc = next(m for m in manifests if m["kind"] == "Service")
    assert svc["spec"]["ports"][0]["targetPort"] == 9000
```

(Mirror the exact Service-manifest shape used by the file's existing clone/port tests if it differs — the assertion target is the Service's `targetPort`.)

3. Append after `test_create_external_api_key_override`:

```python
async def test_create_external_snapshot_merges_command_cli(client_for_user, super_user, mock_db):
    spec = _spec(
        command=["sh", "-c", "vllm serve /models/y --served-model-name y-8b --api-key sk-live --port 9000"],
        args=[],
    )
    fake_k8s = MagicMock()
    fake_k8s.read_deployment = AsyncMock(return_value=spec)
    fake_k8s.create_or_patch = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=EXTERNAL_BODY)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.model_name == "y-8b"
    snap = run.serving_snapshot
    # Merged+expanded CLI in the snapshot so serving_api_key can derive
    # --api-key from command-form launches too.
    assert "--api-key" in snap["vllm_extra_args"]
    assert "sk-live" in snap["vllm_extra_args"]
    assert run.params.get("tokenizer") == "/models/y"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && .venv/bin/python -m pytest tests/test_bench_external_clone.py -q
```

Expected: the new facts tests fail with `ValueError` (or wrong served_model), the port test with `8000 != 9000`, the API test with a 400/assertion failure. Existing tests still pass.

- [ ] **Step 3: Implement in `backend/app/services/benchmark_serving.py`**

1. Add `import shlex` to the stdlib import block (next to `import uuid`).

2. Insert directly after `_arg_value` (line 122):

```python
def serving_cli(container: dict) -> list[str]:
    """The serving's full launch line: command + args, shell strings expanded.

    K8s manifests spread the CLI across ``command``/``args`` and sometimes
    wrap it in ``sh -c "vllm serve …"``; flag parsing works against one
    normalized token list instead of assuming everything lives in args.
    """
    raw = list(container.get("command") or []) + list(container.get("args") or [])
    cli: list[str] = []
    for tok in raw:
        s = str(tok)
        if any(ch.isspace() for ch in s):
            try:
                cli.extend(shlex.split(s))
                continue
            except ValueError:
                pass
        cli.append(s)
    return cli


def _positional_model(cli: list) -> str | None:
    """The positional MODEL of a ``vllm serve <model>`` launch."""
    for i, tok in enumerate(cli):
        if tok == "serve" and i + 1 < len(cli):
            nxt = str(cli[i + 1])
            if nxt and not nxt.startswith("-"):
                return nxt
    return None
```

3. In `external_bench_facts`, replace

```python
    args = spec["container"]["args"]
    model_arg = _arg_value(args, "--model")
    served = _arg_value(args, "--served-model-name") or model_arg
    if not served:
        raise ValueError("no --model/--served-model-name found in serving args")
```

with

```python
    cli = serving_cli(spec["container"])
    model_arg = (
        _arg_value(cli, "--model")
        or _positional_model(cli)
        or _arg_value(cli, "--model-path")
    )
    served = _arg_value(cli, "--served-model-name") or model_arg
    if not served:
        raise ValueError(
            "no model found in serving command — looked for --model, "
            "--served-model-name, --model-path and a `serve <model>` positional"
        )
```

and update the docstring sentence "The tokenizer is always the --model value." to "The tokenizer is always the model argument (--model, the `serve` positional, or --model-path)."

4. In `_clone_target_port`, replace

```python
    port = _arg_value(container.get("args") or [], "--port")
```

with

```python
    port = _arg_value(serving_cli(container), "--port")
```

- [ ] **Step 4: Implement in `backend/app/api/benchmarks.py`**

1. Add `serving_cli` to the existing `from app.services.benchmark_serving import (...)` block (keep alphabetical order within the block if it is sorted).
2. Line 374: replace

```python
                "vllm_extra_args": spec["container"]["args"],
```

with

```python
                "vllm_extra_args": serving_cli(spec["container"]),
```

- [ ] **Step 5: Run tests and gates**

```bash
cd backend && .venv/bin/python -m pytest tests/test_bench_external_clone.py -q
cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
cd backend && .venv/bin/ruff check app/ tests/ 2>&1 | tail -2
```

Expected: test file fully green (17 existing + 8 new); suite baseline unchanged (21 pre-existing failures, 0 new); ruff 78 (0 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/benchmark_serving.py backend/app/api/benchmarks.py backend/tests/test_bench_external_clone.py
git commit -m "fix(bench): parse external serving CLI from command+args incl. positional/sh -c/sglang forms

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
