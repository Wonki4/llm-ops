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

from app.clients.k8s import K8sClient, K8sNotConfigured
from app.clients.litellm import LiteLLMClient
from app.clients.slack import send_deployment_event_notification
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent
from app.db.session import async_session_factory
from app.services.model_deployment_manifests import k8s_resource_names

logger = logging.getLogger(__name__)


def _classify(observed: dict, desired_replicas: int) -> tuple[str, str]:
    """Return (status, message) from K8s deployment status payload."""
    ready = observed.get("ready", 0)
    available = observed.get("available", 0)
    conditions = observed.get("conditions", [])

    progressing_failed = any(
        c.get("type") == "Progressing"
        and c.get("status") == "False"
        and c.get("reason") in ("ProgressDeadlineExceeded",)
        for c in conditions
    )
    if progressing_failed:
        return "Failed", "Deployment progress deadline exceeded"

    replica_failure = any(c.get("type") == "ReplicaFailure" and c.get("status") == "True" for c in conditions)
    if replica_failure:
        msg = next((c.get("message") for c in conditions if c.get("type") == "ReplicaFailure"), None)
        return "Unhealthy", msg or "ReplicaFailure condition true"

    if desired_replicas == 0:
        return "Stopped", "replicas set to 0"

    if ready >= desired_replicas and available >= desired_replicas:
        return "Ready", None
    if ready == 0:
        return "Pending", "No ready pods yet"
    return "Updating", f"{ready}/{desired_replicas} pods ready"


def _severity_for(transition: tuple[str, str]) -> str:
    _, new = transition
    if new in ("Failed", "Unhealthy"):
        return "error" if new == "Failed" else "warning"
    if new == "Ready":
        return "info"
    return "info"


async def _ensure_catalog(
    db: AsyncSession,
    model_name: str,
    user_id: str,
    deployment_id: uuid.UUID | None = None,
) -> None:
    """Make sure a catalog row exists for this name, optionally linked to a deployment.

    If the row exists and `deployment_id` is given, attach the deployment when
    the row has no current attachment (don't steal from another deployment).
    """
    existing = await db.execute(
        select(CustomModelCatalog).where(CustomModelCatalog.model_name == model_name)
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        if deployment_id is not None and row.deployment_id is None:
            row.deployment_id = deployment_id
        return
    db.add(
        CustomModelCatalog(
            id=uuid.uuid4(),
            model_name=model_name,
            display_name=model_name,
            status=ModelStatus.TESTING,
            visible=True,
            deployment_id=deployment_id,
            created_by=user_id,
            updated_by=user_id,
        )
    )


async def _register_catalog_with_litellm(
    litellm: LiteLLMClient,
    dep: CustomModelDeployment,
    catalog_model_name: str,
) -> str | None:
    """Register one LiteLLM model alias backed by `dep`. Returns LiteLLM id."""
    api_base = f"https://{dep.ingress_host}".rstrip("/")
    if dep.ingress_path and dep.ingress_path != "/":
        api_base = f"{api_base}{dep.ingress_path.rstrip('/')}"
    # vLLM exposes the OpenAI-compatible API; route through openai/ in LiteLLM.
    served_name = dep.model_path.split("/")[-1] or dep.model_name
    try:
        result = await litellm.create_model(
            model_name=catalog_model_name,
            litellm_model=f"openai/{served_name}",
            api_base=api_base,
            api_key="EMPTY",
        )
        info = result.get("model_info") or {}
        return info.get("id") or result.get("id")
    except Exception:
        logger.exception("LiteLLM /model/new failed for %s", catalog_model_name)
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
    cluster_clients: dict[uuid.UUID, K8sClient] = {}

    async def _client_for(db: AsyncSession, cluster_id: uuid.UUID | None) -> K8sClient | None:
        """Cache K8sClient per cluster within one reconcile pass."""
        if cluster_id is None:
            return None
        if cluster_id in cluster_clients:
            return cluster_clients[cluster_id]
        cresult = await db.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_id))
        cluster_row = cresult.scalar_one_or_none()
        if not cluster_row or not cluster_row.enabled or not cluster_row.kubeconfig_content:
            cluster_clients[cluster_id] = None  # type: ignore[assignment]
            return None
        c = K8sClient(cluster_row.kubeconfig_content)
        cluster_clients[cluster_id] = c
        return c

    try:
        async with async_session_factory() as db:
            result = await db.execute(select(CustomModelDeployment))
            deployments = result.scalars().all()
            for dep in deployments:
                polled += 1
                names = k8s_resource_names(dep)
                k8s = await _client_for(db, dep.cluster_id)
                if k8s is None:
                    logger.warning("Skipping %s: no usable cluster", dep.model_name)
                    continue
                try:
                    observed = await k8s.read_deployment_status(dep.namespace, names["deployment"])
                except K8sNotConfigured:
                    logger.warning("Cluster kubeconfig unusable for %s; skipping", dep.model_name)
                    continue
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

                new_status, message = _classify(observed, dep.replicas)
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

                    # First-time Ready: register every catalog row attached to
                    # this deployment that hasn't been registered yet. Auto-create
                    # a catalog row matching the deployment's model_name when
                    # none are attached, so the simple 1-deployment-1-model
                    # case still works out of the box.
                    if new_status == "Ready":
                        attached = (
                            await db.execute(
                                select(CustomModelCatalog).where(
                                    CustomModelCatalog.deployment_id == dep.id
                                )
                            )
                        ).scalars().all()
                        if not attached:
                            await _ensure_catalog(db, dep.model_name, dep.updated_by or "system", deployment_id=dep.id)
                            attached = (
                                await db.execute(
                                    select(CustomModelCatalog).where(
                                        CustomModelCatalog.deployment_id == dep.id
                                    )
                                )
                            ).scalars().all()
                        for catalog_row in attached:
                            if catalog_row.litellm_model_id:
                                continue
                            model_id = await _register_catalog_with_litellm(
                                litellm, dep, catalog_row.model_name
                            )
                            if model_id:
                                catalog_row.litellm_model_id = model_id
                                await _record_event(
                                    db, dep, "LitellmRegistered", "info", None, None,
                                    f"Registered {catalog_row.model_name} as LiteLLM id={model_id}",
                                )
                                registered += 1
                            else:
                                ev2 = await _record_event(
                                    db, dep, "LitellmRegisterFailed", "error", None, None,
                                    f"LiteLLM /model/new failed for {catalog_row.model_name}",
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
