import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.jobs.reconcile_recipe_validations import tick
from app.services import validation_runtime as runtime
from app.services.validation_asset_runner import inspect_assets
from tests.test_recipe_validations import db as db
from tests.test_recipe_validations import prepared_attempt
from tests.test_recipe_validations import protocol as protocol
from tests.test_recipe_validations import user as user


@pytest.mark.parametrize("ready", [False, True])
async def test_startup_probe_warning_without_restart_respects_startup_budget(db, user, protocol, monkeypatch, ready):
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    current = datetime.now(UTC)
    attempt.status = "starting"
    attempt.stage_started_at = current - timedelta(seconds=30)
    attempt.state = {"applied_at": (current - timedelta(seconds=30)).isoformat()}
    evidence = {
        "aggregated": {
            "pods": [
                {
                    "scheduled": True,
                    "events": [{"reason": "Unhealthy", "message": "Startup probe failed: connection refused"}],
                    "containers": [
                        {"restarts": 0, "reason": "Running", "ready": ready, "current_log": "Loading model weights"}
                    ],
                }
            ]
        }
    }
    monkeypatch.setattr(runtime, "observe", AsyncMock(return_value=({"ready": ready, "pools": {}}, evidence)))
    await tick(db, job, current=current)
    assert attempt.status == ("smoke" if ready else "starting"), (attempt.status, attempt.error_code)


async def test_benchmark_can_schedule_on_same_tainted_gpu_node(db, user, protocol, monkeypatch):
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    dep.tolerations = [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]
    run = await runtime.prepare_benchmark(db, job, candidate, attempt, dep, "performance-1", protocol["performance"])
    k8s = SimpleNamespace(read_job_status=AsyncMock(side_effect=ApiException(status=404)), create_job=AsyncMock())
    monkeypatch.setattr(runtime, "k8s_for_cluster", AsyncMock(return_value=k8s))
    await runtime.benchmark(db, job, attempt, dep, run)
    pod = k8s.create_job.call_args.args[1]["spec"]["template"]["spec"]
    assert pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert pod.get("tolerations") == dep.tolerations, pod.get("tolerations")


async def test_hf_tokenizer_reference_survives_preflight_pod_removal(db, user, protocol, monkeypatch, tmp_path):
    model_ref = "example/test-model"
    cache = tmp_path / "preflight-pod-only" / "snapshots" / ("a" * 40)
    cache.mkdir(parents=True)
    (cache / "config.json").write_text("{}")
    (cache / "model.safetensors").write_bytes(b"fake-weights")
    tokenizer = SimpleNamespace(get_vocab=lambda: {"a": 1, "b": 2}, encode=lambda *a, **kw: [1, 2])
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **kw: tokenizer)),
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=lambda *a, **kw: str(cache)))
    assets = inspect_assets({"model_path": model_ref})
    assets["image"] = "registry/engine@sha256:" + "a" * 64
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    dep.model_path = model_ref
    attempt.state = {"assets": {"0": assets}}
    run = await runtime.prepare_benchmark(db, job, candidate, attempt, dep, "performance-1", protocol["performance"])
    k8s = SimpleNamespace(read_job_status=AsyncMock(side_effect=ApiException(status=404)), create_job=AsyncMock())
    monkeypatch.setattr(runtime, "k8s_for_cluster", AsyncMock(return_value=k8s))
    await runtime.benchmark(db, job, attempt, dep, run)
    pod = k8s.create_job.call_args.args[1]["spec"]["template"]["spec"]
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    # No shared volumes exist; a path created only inside the asset Job cannot exist in this runner Pod.
    assert not pod["volumes"]
    assert env["BENCH_TOKENIZER"] == model_ref, env["BENCH_TOKENIZER"]
    assert env["BENCH_TOKENIZER_REVISION"] == "a" * 40


async def test_probe_failure_with_real_restart_is_preserved(db, user, protocol, monkeypatch):
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    current = datetime.now(UTC)
    attempt.status = "starting"
    attempt.stage_started_at = current
    attempt.state = {"applied_at": current.isoformat()}
    evidence = {
        "aggregated": {"pods": [{"containers": [{"restarts": 1}], "events": [{"message": "Startup probe failed"}]}]}
    }
    monkeypatch.setattr(runtime, "observe", AsyncMock(return_value=({"ready": False, "pools": {}}, evidence)))
    await tick(db, job, current=current)
    assert attempt.error_code == "probe_restart"


def test_common_and_server_tokenizers_keep_separate_revisions(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"weights")
    calls = []

    def download(ref, **kwargs):
        calls.append((ref, kwargs))
        return str(tmp_path / "snapshots" / ("server-pin" if ref == "org/server" else "workload-pin"))

    tokenizer = SimpleNamespace(get_vocab=lambda: {"a": 1}, encode=lambda *a, **kw: [1])
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **kw: tokenizer)),
    )
    asset = inspect_assets(
        {"model_path": str(model), "server_tokenizer": "org/server", "tokenizer": "org/common", "airgap": True}
    )
    assert asset["tokenizer"] == "org/common"
    assert asset["server_tokenizer"] == "org/server"
    assert asset["tokenizer_revision"] == "server-pin"
    assert asset["workload_tokenizer_revision"] == "workload-pin"
    assert all(kw["local_files_only"] for _, kw in calls)


def test_measurement_loads_pinned_tokenizer_offline(monkeypatch):
    from app.services.portal_bench_runner import chat_inputs

    load = __import__("unittest.mock", fromlist=["Mock"]).Mock(
        return_value=SimpleNamespace(encode=lambda *a, **kw: [1, 2], decode=lambda *a, **kw: "text")
    )
    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=load))
    )
    monkeypatch.setenv("BENCH_TOKENIZER_REVISION", "immutable-sha")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("BENCH_VALIDATION_TOKENIZER_SHA256", raising=False)
    chat_inputs("org/common", {"seed": 1, "random_input_len": 4, "num_prompts": 2})
    assert load.call_args.kwargs["revision"] == "immutable-sha"
    assert load.call_args.kwargs["local_files_only"] is True
