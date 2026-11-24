"""Engine fixtures cover missing vs zero, repeated ranks, strict scope and Pod churn."""

import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.db.models.custom_kv_observation import CustomKVObservation
from app.jobs import observe_kv_capacity
from app.services import kv_observations as kv
from app.services.kv_identity import pool_fingerprint
from app.services.kv_telemetry import log_sample, metric_sample, recent_log_rates

REV = "a" * 40


def test_engine_log_capacity_does_not_sum_ranks_or_report_utilization_as_capacity():
    result = log_sample(
        "vllm",
        (
            "[rank0] Available KV cache memory: 49.07 GiB\n[rank1] Available KV cache memory: 48.50 GiB\n"
            "[rank0] GPU KV cache size: 3,040,000 tokens\n[rank1] GPU KV cache size: 3,040,000 tokens"
        ),
    )
    assert result["capacity_tokens"] == 3040000
    assert result["budget_bytes"] == int(48.5 * 2**30)
    assert result["budget_bytes_max"] == int(49.07 * 2**30)
    assert log_sample("vllm", "GPU KV cache usage: 60%") == {}
    assert recent_log_rates("vllm", "GPU KV cache usage: 12.0%, Prefix cache hit rate: 75.5%") == {
        "utilization": 0.12,
        "prefix_hit_rate": 0.755,
        "prefix_rate_scope": "engine_window",
    }
    assert log_sample("vllm", "GPU KV cache size: 0 tokens")["capacity_tokens"] == 0
    sg = log_sample(
        "sglang",
        "max_total_num_tokens=8765, chunked_prefill_size=8192, max_prefill_tokens=8192\n"
        "KV Cache is allocated. #tokens: 8765, K size: 1.25 GB, V size: 1.25 GB",
    )
    assert sg["capacity_tokens"] == 8765 and sg["budget_bytes"] == int(2.5 * 2**30)


def test_metric_scope_and_zero():
    assert metric_sample("vllm", 'vllm:cache_config_info{block_size="16",num_gpu_blocks="32768"} 1') == {}
    result = metric_sample(
        "vllm",
        'vllm:cache_config_info{kv_cache_size_tokens="32768"} 1\n'
        'vllm:kv_cache_usage_perc{engine="0"} 0\n'
        'vllm:prefix_cache_queries_total{engine="0"} 20\n'
        'vllm:prefix_cache_hits_total{engine="0"} 0',
    )
    assert result["capacity_tokens"] == 32768
    assert result["utilization"] == result["prefix_hit_rate"] == 0
    assert "prefix_hit_rate" not in metric_sample(
        "vllm", "vllm:prefix_cache_queries_total 0\nvllm:prefix_cache_hits_total 0"
    )
    assert "prefix_hit_rate" not in metric_sample(
        "vllm", 'vllm:prefix_cache_queries_total{engine="a"} 8\nvllm:prefix_cache_hits_total{engine="b"} 2'
    )
    sg = metric_sample(
        "sglang",
        "sglang:max_total_num_tokens 1234\nsglang:max_total_num_tokens_swa 123456\n"
        "sglang:kv_cache_memory_usage_gb 1\nsglang:cache_hit_rate 0.75",
    )
    assert (sg["capacity_tokens"], sg["budget_bytes"], sg["prefix_hit_rate"]) == (1234, 2**30, 0.75)


def test_fingerprint_matches_manifest_and_pod_defaults_and_separates_pool_overrides():
    c = {
        "image": "vllm:1",
        "args": ["--model", "/mnt/snapshot", "--max-model-len", "32000"],
        "env": [{"name": "POD_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}}],
        "volumeMounts": [{"name": "weights", "mountPath": "/mnt", "readOnly": False}],
    }
    pod = {"volumes": [{"name": "weights", "persistentVolumeClaim": {"claimName": "local-models"}}]}

    def target(container, spec, role="prefill", cluster="cluster-b"):
        return pool_fingerprint(cluster, "airgap", role, "H200", container, spec)

    admitted = copy.deepcopy(c)
    admitted["env"][0]["valueFrom"]["fieldRef"]["apiVersion"] = "v1"
    admitted["volumeMounts"].append({"name": "kube-api-access-17", "mountPath": "/var/run/secrets/kubernetes.io"})
    assert target(admitted, pod) == target(c, pod)
    assert target(c, pod, role="decode") != target(c, pod)
    assert target(c, pod, cluster="cluster-a") != target(c, pod)
    assert target({**c, "args": [*c["args"], "--tensor-parallel-size", "8"]}, pod) != target(c, pod)
    assert target(c, {"volumes": []}) != target(c, pod)


def sample_row(**kwargs):
    fields = dict(
        deployment_id=uuid.uuid4(),
        role="prefill",
        pod_uid="uid",
        pod_name="prefill-a",
        restart=0,
        gpu_type="A",
        engine="vllm",
        image="vllm:test",
        last_seen=datetime.now(UTC),
        active=True,
        evidence={},
        capacity_tokens=120,
    )
    return CustomKVObservation(**{**fields, **kwargs})


def test_comparison_only_for_matching_pool_and_revision_with_retained_rule():
    pool = {"role": "prefill", "fingerprint": "a", "capacity_tokens": 100, "revision": REV, "args": ["secret"]}
    blob = {"rule_version": "rule1", "pools": [pool]}
    assert kv.matched_estimate(blob, "decode", "a", REV) is None
    assert kv.matched_estimate(blob, "prefill", "b", REV) is None
    expected = kv.matched_estimate(blob, "prefill", "a", REV)
    result = kv.summary(sample_row(evidence={"estimate": expected}))
    assert result["delta_tokens"] == result["delta_percent"] == 20
    assert expected["rule_version"] == "rule1" and "args" not in expected
    for revision in [None, "b" * 40]:
        assert (
            kv.summary(sample_row(evidence={"estimate": kv.matched_estimate(blob, "prefill", "a", revision)}))[
                "delta_tokens"
            ]
            is None
        )
    assert kv.summary(sample_row(evidence={"estimate": expected}, capacity_tokens=None))["delta_percent"] is None


@pytest.mark.asyncio
async def test_matching_budget_requires_immutable_identity_and_preserves_real_zero():
    db = AsyncMock()
    db.scalar.return_value = 0
    req = {"image": "vllm@sha256:" + "b" * 64, "revision": REV, "gpu_type": "h100"}
    for overrides in ({"image": "vllm:v2"}, {"revision": "main"}, {"gpu_type": None}):
        assert await kv.matching_budget(db, "fingerprint", **{**req, **overrides}) is None
    db.scalar.assert_not_awaited()
    assert await kv.matching_budget(db, "fingerprint", **req) == 0
    assert kv.model_revision(["--model", "/weights", "--revision", REV]) is None
    assert kv.model_revision(["--model-path", "/weights/snapshots/" + REV]) == REV


@pytest.mark.asyncio
async def test_retry_uses_current_pod_epoch_and_preserves_capacity_if_scrape_unavailable(monkeypatch):
    args = ["--model", "acme/model", "--revision", REV]
    container = SimpleNamespace(name="vllm")
    podspec = SimpleNamespace(containers=[container])
    status = SimpleNamespace(name="vllm", image_id="digest", restart_count=0, state=SimpleNamespace(running=True))
    pod = SimpleNamespace(
        metadata=SimpleNamespace(uid="uid", name="pod-a"),
        spec=podspec,
        status=SimpleNamespace(container_statuses=[status]),
    )
    c = {"name": "vllm", "image": "vllm:test", "args": args, "ports": [{"name": "http", "containerPort": 8000}]}
    api = Mock()
    api.sanitize_for_serialization.side_effect = lambda obj: {"containers": [c]} if obj is podspec else c
    dep = SimpleNamespace(
        id=uuid.uuid4(), cluster_id=uuid.uuid4(), namespace="remote", validation_attempt_id=uuid.uuid4(), engine="vllm"
    )
    core = AsyncMock()
    core.read_namespaced_pod.return_value = pod
    core.read_namespaced_pod_log.side_effect = ApiException(status=403)
    metrics = AsyncMock(side_effect=ApiException(status=403))
    monkeypatch.setattr(kv, "read_metrics", metrics)

    async def poll(old=None):
        return await kv.read_pod(api, core, dep, "decode", "GPU", pod, old, None)

    row = await poll()
    assert row.capacity_tokens is None and row.observed_at is None
    core.read_namespaced_pod_log.side_effect = None
    core.read_namespaced_pod_log.return_value = "GPU KV cache size: 0 tokens\nAvailable KV cache memory: 0.00 GiB"
    row = await poll(row)
    assert row.capacity_tokens == row.budget_bytes == 0 and row.observed_at is not None
    before = row.observed_at
    await poll(row)
    assert row.observed_at == before and row.attempt_id == dep.validation_attempt_id
    replacement = copy.deepcopy(pod)
    replacement.status.container_statuses[0].restart_count = 1
    core.read_namespaced_pod.return_value = replacement
    assert await poll(row) is None
    before_restart = kv.summary(row)
    status.restart_count = 1
    new_row = await poll()
    assert (before_restart["restart"], new_row.restart) == (0, 1)
    assert before_restart["capacity_tokens"] == new_row.capacity_tokens == 0
    metrics.assert_awaited_with(core, "remote", "pod-a", 8000, "vllm")


@pytest.mark.asyncio
async def test_candidate_final_capture_can_fail_without_blocking_cleanup(monkeypatch):
    monkeypatch.setattr(observe_kv_capacity, "collect_one", AsyncMock(side_effect=TimeoutError()))
    session = AsyncMock()
    monkeypatch.setattr(observe_kv_capacity, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        observe_kv_capacity, "deployment_observations", AsyncMock(return_value=[{"capacity_tokens": 7}])
    )
    attempt = SimpleNamespace(state={"outcome": "failed"})
    await observe_kv_capacity.retain_attempt(attempt, SimpleNamespace(id=uuid.uuid4()))
    assert attempt.state == {"outcome": "failed", "kv_observations": [{"capacity_tokens": 7}]}


def test_verified_airgap_candidate_uses_pool_image_node_and_config_digest():
    from app.services.validation_runtime import restored_assets_match

    image = "registry/vllm@sha256:" + "a" * 64
    asset = {"weights_sha256": "b" * 64, "config_digest": "c" * 64, "image": image, "node_name": "gpu-node-b"}
    pool = {"role": "decode", "fingerprint": "fp", "config_digest": "c" * 64, "capacity_tokens": 100}
    estimate = {"pools": [pool], "attempt_assets": {"1": asset}}
    match = kv.matched_estimate(estimate, "decode", "fp", None, image=image, node="gpu-node-b")
    assert match["same_revision"] and match["validated_weights_sha256"] == asset["weights_sha256"]
    assert not kv.matched_estimate(estimate, "decode", "fp", None, image=image, node="gpu-node-a")["same_revision"]
    assert not kv.matched_estimate(estimate, "decode", "fp", None, image="vllm:latest", node="gpu-node-b")[
        "same_revision"
    ]
    current = {"1": asset}
    assert restored_assets_match(current, {"1": {k: v for k, v in asset.items() if k != "config_digest"}})
    assert not restored_assets_match(current, {"1": {**asset, "config_digest": "different"}})
    assert not restored_assets_match(current, {"1": {**asset, "weights_sha256": "different"}})
