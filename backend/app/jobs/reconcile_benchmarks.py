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
from app.config import settings
from app.services.benchmark_manifests import build_job_manifest, build_vllm_bench_job
from app.services.benchmark_serving import serving_resource_names, serving_target_url
from app.services.clusters import k8s_for_cluster

logger = logging.getLogger(__name__)

RESULT_MARKER = "<<<RESULT>>>"
TERMINAL = ("succeeded", "failed", "cancelled")
# A serving loading weights from a PVC can take a while to report ready.
PROVISION_TIMEOUT_S = 1800
DEFAULT_BENCH_IMAGE = "llmops-benchmark:latest"


def _now():
    return datetime.now(UTC)


def _age_s(run: CustomBenchmarkRun) -> float:
    if not run.created_at:
        return 0.0
    return (_now() - run.created_at).total_seconds()


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


async def _teardown_serving(k8s: K8sClient, run: CustomBenchmarkRun) -> None:
    """Delete a run's throwaway serving. Leaves serving_torn_down False on
    failure so a later pass retries."""
    if not run.serving_k8s_name or not run.k8s_namespace:
        run.serving_torn_down = True
        return
    try:
        await k8s.delete(run.k8s_namespace, serving_resource_names(run.serving_k8s_name))
    except Exception:  # noqa: BLE001 — incl. K8sNotConfigured; retry next pass
        logger.exception("Ephemeral serving teardown failed for benchmark %s", run.id)
        return
    run.serving_torn_down = True
    logger.info("Tore down ephemeral serving for benchmark %s", run.id)


async def _drive_provisioning(k8s: K8sClient, run: CustomBenchmarkRun) -> int:
    """Gate a provisioning run: once its serving is ready, create the bench Job."""
    if not run.serving_k8s_name or not run.k8s_namespace:
        run.status = "failed"
        run.error_message = "ephemeral run missing serving info"
        run.finished_at = _now()
        return 1

    names = serving_resource_names(run.serving_k8s_name)
    try:
        ds = await k8s.read_deployment_status(run.k8s_namespace, names["deployment"])
        ready = ds["ready"] >= 1
    except ApiException as e:
        if e.status == 404:
            ready = False
        else:
            raise

    if ready:
        snap = run.serving_snapshot or {}
        target = serving_target_url(run.serving_k8s_name, run.k8s_namespace)
        if run.kind == "performance":
            params = run.params or {}
            manifest = build_vllm_bench_job(
                run,
                image=run.bench_image or settings.vllm_bench_image,
                target_base_url=target,
                api_key="EMPTY",
                served_model=snap.get("model_path"),
                tokenizer=params.get("tokenizer") or snap.get("model_path"),
                pvc_name=snap.get("pvc_name"),
                pvc_mount_path=snap.get("pvc_mount_path"),
            )
        else:
            manifest = build_job_manifest(
                run,
                image=run.bench_image or DEFAULT_BENCH_IMAGE,
                target_base_url=target,
                api_key="EMPTY",
                bench_model=snap.get("model_path"),
            )
        await k8s.create_job(run.k8s_namespace, manifest)
        run.status = "pending"
        run.started_at = _now()
        return 1

    if _age_s(run) > PROVISION_TIMEOUT_S:
        run.status = "failed"
        run.error_message = "Serving did not become ready within the provisioning timeout"
        run.finished_at = _now()
        return 1
    return 0


async def _drive_job(k8s: K8sClient, run: CustomBenchmarkRun) -> int:
    """Poll a pending/running run's K8s Job and map it to a terminal status."""
    if not run.k8s_job_name or not run.k8s_namespace:
        return 0
    try:
        observed = await k8s.read_job_status(run.k8s_namespace, run.k8s_job_name)
    except ApiException as e:
        if e.status == 404:
            run.status = "failed"
            run.error_message = "K8s Job missing from cluster"
            run.finished_at = _now()
            return 1
        logger.exception("K8s Job status read failed for benchmark %s", run.id)
        return 0

    active, succeeded, failed = observed["active"], observed["succeeded"], observed["failed"]
    transitions = 0
    if run.status == "pending" and (active or succeeded or failed):
        run.status = "running"
        run.started_at = run.started_at or _now()
        transitions += 1

    if succeeded > 0:
        parsed = _parse_result(await k8s.read_job_pod_logs(run.k8s_namespace, run.k8s_job_name))
        run.status = "succeeded"
        run.result = parsed
        if parsed is None:
            run.error_message = "Runner finished without emitting RESULT marker"
        run.finished_at = _now()
        transitions += 1
    elif failed > 0:
        parsed = _parse_result(await k8s.read_job_pod_logs(run.k8s_namespace, run.k8s_job_name))
        run.status = "failed"
        run.error_message = _failure_message(observed["conditions"]) or "Job failed"
        if parsed is not None:
            run.result = parsed
        run.finished_at = _now()
        transitions += 1
    return transitions


async def reconcile_once() -> dict:
    polled = 0
    transitions = 0
    try:
        async with async_session_factory() as db:
            # ❶ Drive provisioning → running → terminal.
            result = await db.execute(
                select(CustomBenchmarkRun).where(
                    CustomBenchmarkRun.status.in_(("provisioning", "pending", "running"))
                )
            )
            for run in result.scalars().all():
                polled += 1
                k8s = await k8s_for_cluster(db, run.cluster_id)
                try:
                    if run.status == "provisioning":
                        transitions += await _drive_provisioning(k8s, run)
                    else:
                        transitions += await _drive_job(k8s, run)
                except K8sNotConfigured:
                    logger.warning("K8s not configured; skipping benchmark reconciler pass")
                    return {"polled": 0, "transitions": 0, "skipped": True}
                # Tear the temp serving down as soon as the run is terminal.
                if run.ephemeral and not run.serving_torn_down and run.status in TERMINAL:
                    await _teardown_serving(k8s, run)

            # ❷ Safety sweep — ephemeral runs that finished but still hold a
            #    serving (e.g. teardown failed earlier or worker restarted).
            sweep = await db.execute(
                select(CustomBenchmarkRun).where(
                    CustomBenchmarkRun.ephemeral.is_(True),
                    CustomBenchmarkRun.serving_torn_down.is_(False),
                    CustomBenchmarkRun.status.in_(TERMINAL),
                )
            )
            for run in sweep.scalars().all():
                k8s = await k8s_for_cluster(db, run.cluster_id)
                await _teardown_serving(k8s, run)

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
