"""Label + note are persisted on create and returned by the serializer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_persists_label_and_note(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = {
        "tool": "vllm_serving",
        "model_name": "m",
        "params": {"num_prompts": 10},
        "label": "baseline-h100",
        "note": "first pass before tuning",
    }
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.label == "baseline-h100"
    assert run.note == "first pass before tuning"
    assert resp.json()["label"] == "baseline-h100"
    assert resp.json()["note"] == "first pass before tuning"


@pytest.mark.asyncio
async def test_list_presets(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/benchmarks/presets")
    assert resp.status_code == 200, resp.text
    presets = resp.json()["presets"]
    assert set(presets.keys()) >= {"chat", "long_input", "long_output"}
