"""DELETE /api/benchmarks/{id} — remove a run with best-effort K8s cleanup."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch


def _run(**kw):
    r = MagicMock()
    r.id = uuid.uuid4()
    r.cluster_id = None
    r.k8s_job_name = "bench-abc"
    r.k8s_namespace = "team-a"
    r.ephemeral = False
    r.serving_k8s_name = None
    r.serving_torn_down = True
    for k, v in kw.items():
        setattr(r, k, v)
    return r


async def test_delete_removes_run_and_deletes_job(client_for_user, super_user, mock_db):
    run = _run()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.delete = AsyncMock()
    fake_k8s = MagicMock()
    fake_k8s.delete_job = AsyncMock()
    fake_k8s.delete = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/benchmarks/{run.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    fake_k8s.delete_job.assert_awaited_once_with("team-a", "bench-abc")
    mock_db.delete.assert_awaited_once_with(run)


async def test_delete_tears_down_ephemeral_serving(client_for_user, super_user, mock_db):
    run = _run(ephemeral=True, serving_k8s_name="eph-xyz", serving_torn_down=False, k8s_job_name=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.delete = AsyncMock()
    fake_k8s = MagicMock()
    fake_k8s.delete_job = AsyncMock()
    fake_k8s.delete = AsyncMock()
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/benchmarks/{run.id}")
    assert resp.status_code == 200
    fake_k8s.delete.assert_awaited_once()
    ns, _names = fake_k8s.delete.await_args.args
    assert ns == "team-a"
    mock_db.delete.assert_awaited_once_with(run)


async def test_delete_still_removes_row_when_cleanup_fails(client_for_user, super_user, mock_db):
    run = _run()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.delete = AsyncMock()
    # k8s_for_cluster raising must be swallowed; the row is still deleted.
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(side_effect=RuntimeError("no cluster"))):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/benchmarks/{run.id}")
    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once_with(run)


async def test_delete_missing_run_404(client_for_user, super_user, mock_db):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.delete = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/benchmarks/{uuid.uuid4()}")
    assert resp.status_code == 404
    mock_db.delete.assert_not_awaited()
