"""Sweep driver: sequential promotion, failure-continues, completion."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.reconcile_benchmarks import _drive_sweeps
from app.services.benchmark_manifests import job_name_for


def _sweep(**kw):
    base = dict(id=uuid.uuid4(), status="running", finished_at=None, cluster_id=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run(**kw):
    base = dict(
        id=uuid.uuid4(), status="queued", sweep_index=0, cluster_id=None,
        k8s_namespace="bench", k8s_job_name=None, queued_job_manifest={"kind": "Job"},
        error_message=None, finished_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _res(items):
    m = MagicMock()
    m.scalars.return_value.all.return_value = items
    return m


def _db(sweeps, runs):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_res(sweeps)] + [_res(r) for r in runs])
    return db


async def test_promotes_next_queued_when_previous_terminal():
    done = _run(status="succeeded", sweep_index=0, queued_job_manifest=None)
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[done, queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1
    assert queued.status == "pending"
    assert queued.k8s_job_name == job_name_for(queued.id)
    assert queued.queued_job_manifest is None


async def test_waits_while_a_combo_is_active():
    active = _run(status="running", sweep_index=0, queued_job_manifest=None, k8s_job_name="bench-x")
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[active, queued]])
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock()) as kfc:
        n = await _drive_sweeps(db)
    assert n == 0 and queued.status == "queued"
    kfc.assert_not_awaited()


async def test_failed_combo_does_not_block_next():
    failed = _run(status="failed", sweep_index=0, queued_job_manifest=None)
    queued = _run(status="queued", sweep_index=1)
    db = _db([_sweep()], [[failed, queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1 and queued.status == "pending"


async def test_promotion_failure_marks_run_failed_and_continues_next_tick():
    queued = _run(status="queued", sweep_index=0)
    db = _db([_sweep()], [[queued]])
    k8s = MagicMock()
    k8s.create_job = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.jobs.reconcile_benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        n = await _drive_sweeps(db)
    assert n == 1
    assert queued.status == "failed" and "boom" in queued.error_message
    assert queued.queued_job_manifest is None and queued.finished_at is not None


async def test_all_terminal_completes_sweep():
    sweep = _sweep()
    runs = [_run(status="succeeded", sweep_index=0, queued_job_manifest=None),
            _run(status="failed", sweep_index=1, queued_job_manifest=None)]
    db = _db([sweep], [runs])
    n = await _drive_sweeps(db)
    assert n == 1
    assert sweep.status == "completed" and sweep.finished_at is not None
