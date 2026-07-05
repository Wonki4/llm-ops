"""Reconciler for K8s-backed model deployments.

Every interval:
1. Pull every custom_model_deployment row.
2. Read the matching K8s Deployment + Service status via the K8s API.
3. Compute a coarse-grained status (Pending / Updating / Ready / Unhealthy /
   Failed) from ready_replicas + spec.replicas + pod conditions.
4. Write the new status back to the row and, on transitions, append a row to
   custom_model_deployment_event.
5. When a deployment first reaches Ready, register it with LiteLLM via
   /model/new (using ingress_host as api_base since LiteLLM proxy may live
   in a different cluster) and auto-create a CustomModelCatalog row keyed by
   model_name. default_*_cost_per_token are left null — admins fill those in
   later through the existing catalog UI.
6. Severity warning/error events fan out to Slack once.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.k8s import K8sNotConfigured
from app.clients.litellm import LiteLLMClient
from app.clients.slack import send_deployment_event_notification
from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent
from app.db.session import async_session_factory
from app.services.clusters import k8s_for_cluster
from app.services.deployment_status import classify
from app.services.model_deployment_manifests import k8s_resource_names, serving_api_key

logger = logging.getLogger(__name__)


def _severity_for(transition: tuple[str, str]) -> str:
    _, new = transition
    if new in ("Failed", "Unhealthy"):
        return "error" if new == "Failed" else "warning"
    if new == "Ready":
        return "info"
    return "info"


async def _ensure_catalog(db: AsyncSession, model_name: str, user_id: str) -> None:
    """Create a catalog row if none exists for this model_name."""
    existing = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.model_name == model_name))
    if existing.scalar_one_or_none():
        return
    db.add(
        CustomModelCatalog(
            id=uuid.uuid4(),
            model_name=model_name,
            display_name=model_name,
            status=ModelStatus.TESTING,
            visible=True,
            created_by=user_id,
            updated_by=user_id,
        )
    )


async def _register_with_litellm(
    litellm: LiteLLMClient, dep: CustomModelDeployment
) -> str | None:
    """Call LiteLLM /model/new using the deployment's ingress URL as api_base.

    Returns the LiteLLM-assigned deployment id, or None if the call failed.
    """
    api_base = f"https://{dep.ingress_host}".rstrip("/")
    if dep.ingress_path and dep.ingress_path != "/":
        api_base = f"{api_base}{dep.ingress_path.rstrip('/')}"
    # vLLM exposes the OpenAI-compatible API; route through openai/ in LiteLLM.
    served_name = dep.model_path.split("/")[-1] or dep.model_name
    try:
        result = await litellm.create_model(
            model_name=dep.model_name,
            litellm_model=f"openai/{served_name}",
            api_base=api_base,
            # Register with the serving's own key so LiteLLM can reach it when
            # the vLLM server has auth enabled (--api-key); "EMPTY" when it's open.
            api_key=serving_api_key(dep.vllm_extra_args, dep.env),
        )
        info = result.get("model_info") or {}
        return info.get("id") or result.get("id")
    except Exception:
        logger.exception("LiteLLM /model/new failed for %s", dep.model_name)
        return None


async def _record_event(
    db: AsyncSession,
    deployment: CustomModelDeployment,
    event_type: str,
    severity: str,
    from_status: str | None,
    to_status: str | None,
    message: str | None,
) -> CustomModelDeploymentEvent:
    event = CustomModelDeploymentEvent(
        id=uuid.uuid4(),
        deployment_id=deployment.id,
        event_type=event_type,
        severity=severity,
        from_status=from_status,
        to_status=to_status,
        message=message,
    )
    db.add(event)
    await db.flush()
    return event


async def reconcile_once() -> dict:
    """Single pass: poll K8s, sync status, emit events, register on Ready."""
    polled = 0
    transitions = 0
    registered = 0

    litellm = LiteLLMClient()
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(CustomModelDeployment))
            deployments = result.scalars().all()
            for dep in deployments:
                polled += 1
                names = k8s_resource_names(dep)
                k8s = await k8s_for_cluster(db, dep.cluster_id)
                try:
                    observed = await k8s.read_deployment_status(dep.namespace, names["deployment"])
                except K8sNotConfigured:
                    logger.warning("K8s not configured; skipping reconciler pass")
                    return {"polled": 0, "transitions": 0, "registered": 0, "skipped": True}
                except ApiException as e:
                    if e.status == 404:
                        # Deployment vanished from cluster; mark as missing but keep DB row
                        if dep.status != "Missing":
                            ev = await _record_event(
                                db, dep, "MissingFromCluster", "warning", dep.status, "Missing",
                                "K8s Deployment not found"
                            )
                            transitions += 1
                            dep.status = "Missing"
                            dep.status_message = "K8s Deployment not found"
                            dep.last_synced_at = datetime.now(UTC)
                            await db.flush()
                            await _maybe_alert(db, ev, dep)
                        continue
                    logger.exception("K8s status read failed for %s", dep.model_name)
                    continue

                new_status, message = classify(observed, dep.replicas)
                cluster_ip = await k8s.read_service_cluster_ip(dep.namespace, names["service"])

                # Build a small set of updates so we only flush when something changed
                changed = False
                if cluster_ip and cluster_ip != dep.service_cluster_ip:
                    dep.service_cluster_ip = cluster_ip
                    changed = True
                if observed["ready"] != dep.ready_replicas:
                    dep.ready_replicas = observed["ready"]
                    changed = True
                if message != dep.status_message:
                    dep.status_message = message
                    changed = True

                if new_status != dep.status:
                    transition = (dep.status, new_status)
                    severity = _severity_for(transition)
                    ev = await _record_event(
                        db, dep, "StatusChanged", severity, dep.status, new_status, message
                    )
                    transitions += 1
                    dep.status = new_status
                    dep.last_synced_at = datetime.now(UTC)
                    changed = True
                    # Slack fan-out for warning/error
                    await _maybe_alert(db, ev, dep)

                    # First-time Ready: register with LiteLLM + auto-create catalog
                    if new_status == "Ready" and not dep.litellm_model_id:
                        model_id = await _register_with_litellm(litellm, dep)
                        if model_id:
                            dep.litellm_model_id = model_id
                            await _ensure_catalog(db, dep.model_name, dep.updated_by or "system")
                            await _record_event(
                                db, dep, "LitellmRegistered", "info", None, None,
                                f"Registered as LiteLLM model id={model_id}"
                            )
                            registered += 1
                        else:
                            ev2 = await _record_event(
                                db, dep, "LitellmRegisterFailed", "error", None, None,
                                "LiteLLM /model/new failed; see logs"
                            )
                            await _maybe_alert(db, ev2, dep)

                if changed:
                    dep.last_synced_at = datetime.now(UTC)

            await db.commit()
    except Exception:
        logger.exception("Reconciler pass failed")

    return {"polled": polled, "transitions": transitions, "registered": registered}


async def _maybe_alert(
    db: AsyncSession,
    event: CustomModelDeploymentEvent,
    dep: CustomModelDeployment,
) -> None:
    """Send Slack notification for warning/error events. Marks alert_sent=True after."""
    if event.severity not in ("warning", "error"):
        return
    sent = await send_deployment_event_notification(
        model_name=dep.model_name,
        namespace=dep.namespace,
        event_type=event.event_type,
        severity=event.severity,
        message=event.message,
    )
    if sent:
        event.alert_sent = True
        await db.flush()


async def reconcile_loop(interval_seconds: int = 60) -> None:
    logger.info("Starting model deployment reconciler (interval=%ds)", interval_seconds)
    while True:
        try:
            r = await reconcile_once()
            if r.get("transitions", 0) or r.get("registered", 0):
                logger.info(
                    "Reconciler: polled=%d transitions=%d registered=%d",
                    r.get("polled", 0), r["transitions"], r["registered"],
                )
        except Exception:
            logger.exception("Reconciler loop error")
        await asyncio.sleep(interval_seconds)
