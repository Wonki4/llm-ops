"""Sweep create: validation, grid expansion into queued runs, combo #0 promotion."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.benchmark_manifests import job_name_for


def _template_deployment():
    base = MagicMock()
    base.id = uuid.uuid4()
    base.model_name = "m"
    base.image = "vllm/vllm-openai:v0.6.0"
    base.model_path = "/models/m"
    base.replicas = 1
    base.gpu_count = 1
    base.gpu_resource_key = "nvidia.com/gpu"
    base.cpu_request = None
    base.cpu_limit = None
    base.memory_request = None
    base.memory_limit = None
    base.node_selector = {}
    base.tolerations = None
    base.pvc_name = "w"
    base.pvc_mount_path = "/models"
    base.vllm_extra_args = ["--max-num-seqs", "64"]
    base.env = {}
    return base


def _sweep_body(**kw):
    body = {
        "deployment_id": str(uuid.uuid4()),
        "preset": "chat",
        "variables": [{"flag": "--max-num-seqs", "values": [128, 256]}],
    }
    body.update(kw)
    return body


async def _post(client_for_user, super_user, body):
    async with client_for_user(super_user) as client:
        return await client.post("/api/benchmarks/sweeps", json=body)


async def test_presets_endpoint_lists_the_methodology(client_for_user, super_user):
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/benchmarks/presets")
    assert resp.status_code == 200
    assert set(resp.json()["presets"]) == {"chat", "long_input", "long_output"}


async def test_create_rejects_bad_input(client_for_user, super_user, mock_db):
    checks = [
        (_sweep_body(preset="nope"), "preset"),
        (_sweep_body(variables=[]), "variable"),
        (_sweep_body(variables=[{"flag": "--a", "values": [1]}] * 3), "variable"),
        (_sweep_body(variables=[{"flag": "MaxSeqs", "values": [1, 2]}]), "flag"),
        (_sweep_body(variables=[{"flag": "--a", "values": [1]}]), "combos"),  # 1 combo < 2
        (_sweep_body(variables=[{"flag": "--a", "values": [1, 2, 3, 4]},
                                {"flag": "--b", "values": [1, 2, 3, 4]}]), "combos"),  # 16 > 12
        (_sweep_body(deployment_id=None), "deployment_id or external_target"),
    ]
    for body, needle in checks:
        resp = await _post(client_for_user, super_user, body)
        assert resp.status_code == 400, (body, resp.text)
        assert needle in resp.json()["detail"]


async def test_create_expands_grid_queues_all_and_promotes_first(
    client_for_user, super_user, mock_db
):
    base = _template_deployment()
    result = MagicMock()
    result.scalar_one_or_none.return_value = base
    mock_db.execute = AsyncMock(return_value=result)
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = _sweep_body(deployment_id=str(base.id))
    with patch("app.api.benchmark_sweeps.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        resp = await _post(client_for_user, super_user, body)
    assert resp.status_code == 201, resp.text

    added = [c.args[0] for c in mock_db.add.call_args_list]
    sweeps = [o for o in added if getattr(o, "preset", None)]
    runs = sorted(
        (o for o in added if getattr(o, "sweep_index", None) is not None or getattr(o, "sweep_combo", None)),
        key=lambda r: r.sweep_index,
    )
    assert len(sweeps) == 1 and len(runs) == 2
    sweep = sweeps[0]
    assert sweep.status == "running" and sweep.preset == "chat"

    # combo #0 promoted (single Job created), combo #1 still queued with manifest
    assert fake_k8s.create_job.await_count == 1
    first, second = runs
    assert first.status == "pending" and first.k8s_job_name == job_name_for(first.id)
    assert first.queued_job_manifest is None
    assert second.status == "queued" and second.k8s_job_name is None
    assert second.queued_job_manifest["kind"] == "Job"

    # per-combo snapshot carries the MERGED args; params carry the preset
    assert second.serving_snapshot["vllm_extra_args"][-2:] == ["--max-num-seqs", "256"]
    assert first.serving_snapshot["vllm_extra_args"][-2:] == ["--max-num-seqs", "128"]
    for r in runs:
        assert r.params["preset"] == "chat" and r.params["num_prompts"] == 300
        assert r.tool == "vllm_serving" and r.kind == "performance"
        assert r.ephemeral is True and r.serving_torn_down is True

    # the API response never leaks the stored manifest
    payload = resp.json()
    assert "queued_job_manifest" not in str(payload)
    assert len(payload["runs"]) == 2
