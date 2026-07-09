"""Reconciler handles single-Job (self-serving) runs without a serving object."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock

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
