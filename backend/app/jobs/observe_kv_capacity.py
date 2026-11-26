"""Collect engine telemetry without changing deployment or validation status."""

import asyncio
import logging

from sqlalchemy import func, or_, select, update

from app.db.models.custom_kv_observation import CustomKVObservation as Observation
from app.db.models.custom_model_deployment import CustomModelDeployment as Deployment
from app.db.session import async_session_factory
from app.services.clusters import k8s_for_cluster
from app.services.kv_observations import collect, deployment_observations

logger = logging.getLogger(__name__)


async def collect_one(deployment_id):
    async with async_session_factory() as db:
        async with db.begin():
            # Separate worker pods and the final candidate snapshot may race.
            lock = int.from_bytes(deployment_id.bytes[:8], "big", signed=True)
            if not await db.scalar(select(func.pg_try_advisory_xact_lock(lock))):
                return
            dep = await db.get(Deployment, deployment_id)
            if dep is None or dep.validation_state == "retired":
                return
            async with asyncio.timeout(30):
                await collect(db, dep, await k8s_for_cluster(db, dep.cluster_id))


async def retain_attempt(attempt, deployment):
    """The final capture is best-effort. Prior startup evidence survives even if the last scrape fails."""
    try:
        await asyncio.wait_for(collect_one(deployment.id), timeout=10)
    except Exception as exc:
        logger.debug("Final KV capture unavailable for %s (%s)", deployment.id, type(exc).__name__)
    try:
        async with async_session_factory() as db:
            rows = await deployment_observations(db, deployment.id)
    except Exception as exc:
        logger.warning("KV snapshot unavailable for %s (%s)", deployment.id, type(exc).__name__)
        rows = []
    attempt.state = {**attempt.state, "kv_observations": rows}


async def collect_once():
    async with async_session_factory() as db:
        await db.execute(
            update(Observation)
            .where(
                Observation.active,
                Observation.deployment_id.in_(select(Deployment.id).where(Deployment.validation_state == "retired")),
            )
            .values(active=False)
        )
        await db.commit()
        ids = (
            await db.scalars(
                select(Deployment.id).where(
                    or_(Deployment.validation_state.is_(None), Deployment.validation_state != "retired")
                )
            )
        ).all()
    gate = asyncio.Semaphore(4)

    async def one(deployment_id):
        try:
            async with gate:
                await collect_one(deployment_id)
        except Exception as exc:
            logger.warning("KV observation unavailable for %s (%s)", deployment_id, type(exc).__name__)

    await asyncio.gather(*(one(i) for i in ids))


async def observe_loop(interval_seconds=45):
    while True:
        try:
            await collect_once()
        except Exception as exc:
            logger.warning("KV polling unavailable (%s)", type(exc).__name__)
        await asyncio.sleep(interval_seconds)
