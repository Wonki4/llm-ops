"""Reconciler for benchmark runs spawned as K8s Jobs.

Every interval:
1. Pull every CustomBenchmarkRun in status `pending` or `running`.
2. Read the matching K8s Job status via BatchV1Api.
3. Map status (active / succeeded / failed) → row status.
4. On terminal status, harvest pod logs and parse the trailing
   `<<<RESULT>>>{...}` JSON line that the runner image emits.
5. Persist result / error_message / finished_at.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import select

from app.clients.k8s import K8sClient, K8sNotConfigured
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)

RESULT_MARKER = "<<<RESULT>>>"


def _parse_result(logs: str) -> dict | None:
    """Find the last `<<<RESULT>>>{json}` line in the log buffer."""
    if not logs:
        return None
    for line in reversed(logs.splitlines()):
        line = line.strip()
        if not line.startswith(RESULT_MARKER):
            continue
        payload = line[len(RESULT_MARKER):].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Benchmark result marker found but JSON invalid: %.200s", payload)
            return None
    return None


def _failure_message(conditions: list[dict]) -> str | None:
    """Pick the most informative condition message for a Failed Job."""
    for c in conditions:
        if c.get("type") == "Failed" and c.get("status") == "True":
            return c.get("message") or c.get("reason") or "Job marked Failed"
    return None


async def reconcile_once() -> dict:
    polled = 0
    transitions = 0
    k8s = K8sClient()
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CustomBenchmarkRun).where(
                    CustomBenchmarkRun.status.in_(("pending", "running"))
                )
            )
            runs = result.scalars().all()
            for run in runs:
                polled += 1
                if not run.k8s_job_name or not run.k8s_namespace:
                    continue
                try:
                    observed = await k8s.read_job_status(run.k8s_namespace, run.k8s_job_name)
                except K8sNotConfigured:
                    logger.warning("K8s not configured; skipping benchmark reconciler pass")
                    return {"polled": 0, "transitions": 0, "skipped": True}
                except ApiException as e:
                    if e.status == 404:
                        # Job was GCed or never landed; mark as failed.
                        run.status = "failed"
                        run.error_message = "K8s Job missing from cluster"
                        run.finished_at = datetime.now(UTC)
                        transitions += 1
                        continue
                    logger.exception("K8s Job status read failed for benchmark %s", run.id)
                    continue

                active = observed["active"]
                succeeded = observed["succeeded"]
                failed = observed["failed"]
                conditions = observed["conditions"]

                if run.status == "pending" and (active or succeeded or failed):
                    run.status = "running"
                    run.started_at = datetime.now(UTC)
                    transitions += 1

                if succeeded > 0:
                    logs = await k8s.read_job_pod_logs(run.k8s_namespace, run.k8s_job_name)
                    parsed = _parse_result(logs)
                    run.status = "succeeded"
                    run.result = parsed
                    if parsed is None:
                        run.error_message = "Runner finished without emitting RESULT marker"
                    run.finished_at = datetime.now(UTC)
                    transitions += 1
                elif failed > 0:
                    logs = await k8s.read_job_pod_logs(run.k8s_namespace, run.k8s_job_name)
                    run.status = "failed"
                    run.error_message = _failure_message(conditions) or "Job failed"
                    # Even on failure we keep a partial result if the runner managed to emit one.
                    parsed = _parse_result(logs)
                    if parsed is not None:
                        run.result = parsed
                    run.finished_at = datetime.now(UTC)
                    transitions += 1

            await db.commit()
    except Exception:
        logger.exception("Benchmark reconciler pass failed")

    return {"polled": polled, "transitions": transitions}


async def reconcile_loop(interval_seconds: int = 30) -> None:
    logger.info("Starting benchmark reconciler (interval=%ds)", interval_seconds)
    while True:
        try:
            r = await reconcile_once()
            if r.get("transitions", 0):
                logger.info(
                    "Benchmark reconciler: polled=%d transitions=%d",
                    r.get("polled", 0), r["transitions"],
                )
        except Exception:
            logger.exception("Benchmark reconciler loop error")
        await asyncio.sleep(interval_seconds)
