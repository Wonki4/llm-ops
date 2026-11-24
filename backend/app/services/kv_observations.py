"""Pod-scoped telemetry for both managed deployments and recipe validation attempts."""

import asyncio
import logging
import re
from datetime import UTC, datetime

from aiohttp import ClientError
from kubernetes_asyncio import client
from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import case, select

from app.db.models.custom_kv_observation import CustomKVObservation as Observation
from app.db.models.custom_recipe_validation import CustomValidationAttempt
from app.services import pd_serving
from app.services.kv_identity import pool_fingerprint
from app.services.kv_telemetry import log_sample, metric_sample, recent_log_rates
from app.services.model_deployment_manifests import k8s_resource_names, metrics_port
from app.services.validation_protocol import effective_flag

logger = logging.getLogger(__name__)


def model_revision(args):
    path = effective_flag(args, "--model") or effective_flag(args, "--model-path") or ""
    snapshot = re.search(r"/snapshots/([a-f0-9]{40}|[a-f0-9]{64})(?:/|$)", path)
    if path.startswith(("/", "./", "../")):
        # --revision does not pin a mutable directory or PVC.
        return snapshot[1] if snapshot else None
    revision = effective_flag(args, "--revision")
    if revision:
        return revision[:256]
    return None


def pinned(image, revision, gpu_type):
    return bool(
        gpu_type
        and re.search(r"@sha256:[0-9a-f]{64}$", image or "")
        and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision or "")
    )


async def matching_budget(db, fingerprint, *, image, revision, gpu_type):
    """Use profiled memory only for an immutable image, model revision and matching hardware profile."""
    if not pinned(image, revision, gpu_type):
        return None
    return await db.scalar(
        select(Observation.budget_bytes)
        .where(
            Observation.fingerprint == fingerprint,
            Observation.image == image,
            Observation.revision == revision,
            Observation.gpu_type == gpu_type,
            Observation.budget_bytes.is_not(None),
        )
        .order_by(
            Observation.active.desc(),
            case((Observation.active, Observation.budget_bytes)).asc(),
            Observation.last_seen.desc(),
            Observation.budget_bytes,
        )
        .limit(1)
    )


def matched_estimate(estimate, role, fingerprint, revision, *, image=None, node=None):
    if not estimate:
        return None
    pool = next(
        (p for p in estimate.get("pools", []) if p.get("role") == role and p.get("fingerprint") == fingerprint), None
    )
    if not pool:
        return None
    # Persist only calculation outputs. Raw manifest args and environment can include credentials.
    fields = (
        "capacity_tokens",
        "capacity_range",
        "budget_source",
        "budget_bytes_per_gpu",
        "components",
        "attention_bytes_per_token_per_gpu",
        "block_tokens",
        "tp",
        "pp",
        "dcp",
        "kv_dtype",
        "required_bytes_per_gpu",
        "metadata_digest",
        "config_digest",
        "revision",
        "missing",
        "assumptions",
    )
    revision_matches = revision is not None and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision)
    asset = estimate.get("attempt_assets", {}).get("1" if role == "decode" else "0", {})
    verified_asset = (
        bool(re.search(r"@sha256:[0-9a-f]{64}$", image or ""))
        and bool(re.fullmatch(r"[0-9a-f]{64}", asset.get("weights_sha256") or ""))
        and asset.get("image") == image
        and asset.get("node_name") == node
        and bool(node)
        and bool(pool.get("config_digest"))
        and asset.get("config_digest") == pool["config_digest"]
    )
    return {key: pool[key] for key in fields if key in pool} | {
        "validated_weights_sha256": asset["weights_sha256"] if verified_asset else None,
        "rule_version": estimate.get("rule_version") or pool.get("rule_version"),
        "same_revision": bool(
            verified_asset or revision_matches and (not pool.get("revision") or pool["revision"] == revision)
        ),
    }


async def read_metrics(core, namespace, pod_name, port, engine):
    # Use the selected cluster's apiserver. Pod IPs are often unreachable from the portal.
    response = await core.connect_get_namespaced_pod_proxy_with_path(
        f"{pod_name}:{port}", namespace, "metrics", _preload_content=False, _request_timeout=3
    )
    try:
        if response.status != 200:
            return {}
        buf = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            buf.extend(chunk)
            if len(buf) > 2 << 20:
                return {}
        return metric_sample(engine, buf.decode("utf-8", "replace"))
    finally:
        response.release()


def engine_container(pod, engine):
    containers = pod.spec.containers or []
    statuses = pod.status.container_statuses or []
    container = next((c for c in containers if c.name == engine), None)
    status = next((s for s in statuses if s.name == engine), None)
    if not container or not status or status.state.running is None:
        return None
    return container, status


async def read_pod(api, core, dep, role, gpu_type, pod, old, estimate):
    engine = dep.engine
    pair = engine_container(pod, engine)
    if not pair:
        return None
    container, status = pair
    spec = api.sanitize_for_serialization(pod.spec)
    c = api.sanitize_for_serialization(container)
    name, uid, restart = pod.metadata.name, str(pod.metadata.uid), status.restart_count
    fingerprint = pool_fingerprint(dep.cluster_id, dep.namespace, role, gpu_type, c, spec)
    try:
        metrics = await read_metrics(core, dep.namespace, name, metrics_port(c.get("ports") or []), engine)
    except (ApiException, ClientError, OSError, TimeoutError, ValueError, KeyError):
        metrics = {}
    need_logs = any(
        metrics.get(k) is None and (old is None or getattr(old, k) is None) for k in ("capacity_tokens", "budget_bytes")
    )
    logs = {}
    if need_logs:
        try:
            body = await core.read_namespaced_pod_log(
                name, dep.namespace, container=engine, limit_bytes=524288, _request_timeout=4
            )
            logs = log_sample(engine, body)
        except (ApiException, ClientError, OSError, TimeoutError):
            pass
    if engine == "vllm" and not metrics:
        # Existing credentials may permit pods/log but not pods/proxy. Limit dynamic
        # samples to the last polling window so an idle Pod doesn't show an old rate.
        try:
            recent = await core.read_namespaced_pod_log(
                name,
                dep.namespace,
                container=engine,
                since_seconds=60,
                tail_lines=80,
                limit_bytes=32768,
                _request_timeout=3,
            )
            logs.update(recent_log_rates(engine, recent))
        except (ApiException, ClientError, OSError, TimeoutError):
            pass
    # Both logs and metrics endpoints use the mutable pod name. Discard crossed restarts.
    current = await core.read_namespaced_pod(name, dep.namespace, _request_timeout=3)
    pair = engine_container(current, engine)
    if str(current.metadata.uid) != uid or not pair or pair[1].restart_count != restart:
        return None
    values = {**logs, **metrics}
    stamp = datetime.now(UTC)
    row = old or Observation(deployment_id=dep.id, role=role, pod_uid=uid, restart=restart)
    row.attempt_id = dep.validation_attempt_id
    row.pod_name, row.engine, row.gpu_type = name, engine, gpu_type
    row.fingerprint, row.image, row.image_id = fingerprint, c["image"], status.image_id
    launched_revision = model_revision(c.get("args") or [])
    row.revision = launched_revision or row.revision
    row.active, row.last_seen = True, stamp
    evidence = dict(row.evidence or {})
    for key in ("capacity_tokens", "budget_bytes"):
        if key in values:
            setattr(row, key, values[key])
            evidence[key + "_source"] = values[key + "_source"]
            evidence[key + "_max"] = values[key + "_max"]
            if key == "capacity_tokens":
                row.observed_at = stamp
    # Utilization and cache hits are point-in-time samples. Missing scrapes must not retain a stale gauge.
    evidence["utilization"] = values.get("utilization")
    evidence["prefix_hit_rate"] = values.get("prefix_hit_rate")
    evidence["prefix_rate_scope"] = values.get("prefix_rate_scope")
    estimate_for_pool = matched_estimate(
        estimate, role, fingerprint, launched_revision, image=c["image"], node=spec.get("nodeName")
    )
    if estimate_for_pool and estimate_for_pool.get("validated_weights_sha256") and not row.revision:
        row.revision = estimate_for_pool["validated_weights_sha256"]
    frozen = evidence.get("estimate") or {}
    if estimate_for_pool and frozen.get("capacity_tokens") is None and not frozen.get("capacity_range"):
        evidence["estimate"] = estimate_for_pool
    row.evidence = evidence
    return row


def controller_uid(resource):
    return next((str(o.uid) for o in resource.metadata.owner_references or [] if o.controller), None)


async def collect_pool(api, dep, name, role, gpu_type, previous, estimate, *, present=None, fetch_estimate=None):
    core, apps = client.CoreV1Api(api), client.AppsV1Api(api)
    try:
        workload = await apps.read_namespaced_deployment(name, dep.namespace, _request_timeout=4)
    except ApiException as exc:
        if exc.status == 404:
            return []
        raise
    labels = workload.spec.selector.match_labels
    if not labels:
        return []
    selector = ",".join(f"{k}={v}" for k, v in labels.items())
    replicas, pods = await asyncio.gather(
        apps.list_namespaced_replica_set(dep.namespace, label_selector=selector, _request_timeout=5),
        core.list_namespaced_pod(dep.namespace, label_selector=selector, _request_timeout=5),
    )
    owners = {str(rs.metadata.uid) for rs in replicas.items if controller_uid(rs) == str(workload.metadata.uid)}
    live = [p for p in pods.items if controller_uid(p) in owners and not p.metadata.deletion_timestamp]
    if fetch_estimate:
        for pod in live:
            pair = engine_container(pod, dep.engine)
            if not pair:
                continue
            key = (role, str(pod.metadata.uid), pair[1].restart_count)
            saved = (previous[key].evidence or {}).get("estimate", {}) if key in previous else {}
            if saved.get("capacity_tokens") is None and not saved.get("capacity_range"):
                estimate = await fetch_estimate()
                break
    gate = asyncio.Semaphore(4)

    async def one(pod):
        pair = engine_container(pod, dep.engine)
        if not pair:
            return None
        key = (role, str(pod.metadata.uid), pair[1].restart_count)
        if present is not None:
            present.add(key)
        async with gate:
            try:
                return await read_pod(api, core, dep, role, gpu_type, pod, previous.get(key), estimate)
            except (ApiException, ClientError, OSError, TimeoutError) as exc:
                logger.debug("KV sample unavailable for %s (%s)", pod.metadata.name, type(exc).__name__)
                return None

    return [item for item in await asyncio.gather(*(one(p) for p in live)) if item is not None]


async def collect(db, dep, k8s):
    previous = {
        (r.role, r.pod_uid, r.restart): r
        for r in (
            await db.scalars(select(Observation).where(Observation.deployment_id == dep.id, Observation.active))
        ).all()
    }
    # Resolve metadata only after a running engine Pod has been found. Stopped deployments
    # and old candidates do not create metadata Jobs. Share one calculation across both pools.
    requested = False
    estimate = None

    async def fetch_estimate():
        nonlocal requested, estimate
        if not requested:
            requested = True
            try:
                from app.services.recipe_capacity import deployment_estimate
            except ModuleNotFoundError as exc:
                if exc.name != "app.services.recipe_capacity":
                    raise
            else:
                try:
                    estimate = await deployment_estimate(db, dep, start_metadata=True)
                    if dep.validation_attempt_id:
                        assets = await db.scalar(
                            select(CustomValidationAttempt.state["assets"]).where(
                                CustomValidationAttempt.id == dep.validation_attempt_id,
                                CustomValidationAttempt.deployment_id == dep.id,
                            )
                        )
                        estimate = {**estimate, "attempt_assets": assets or {}}
                except Exception as exc:
                    logger.warning("KV estimate unavailable for %s (%s)", dep.id, type(exc).__name__)
        return estimate

    names = k8s_resource_names(dep)
    pools = (
        [(role, names[f"{role}_deployment"], pd_serving.role_view(dep, role).gpu_type) for role in pd_serving.ROLES]
        if pd_serving.is_pd(dep)
        else [("aggregated", names["deployment"], dep.gpu_type)]
    )
    async with await k8s._api_client() as api:
        for role, name, gpu_type in pools:
            present = set()
            try:
                observed = await collect_pool(
                    api, dep, name, role, gpu_type, previous, estimate, present=present, fetch_estimate=fetch_estimate
                )
            except (ApiException, ClientError, OSError, TimeoutError) as exc:
                logger.debug("KV pool unavailable for %s/%s (%s)", dep.id, role, type(exc).__name__)
                continue
            for row in observed:
                if (row.role, row.pod_uid, row.restart) in previous:
                    db.add(row)
                else:
                    # A Pod incarnation may reappear following a transient list/read gap.
                    await db.merge(row)
            for key, row in previous.items():
                if key[0] == role and key not in present:
                    row.active = False
    await db.flush()


def summary(row):
    evidence = row.evidence or {}
    estimate = evidence.get("estimate") or {}
    predicted = estimate.get("capacity_tokens")
    delta = None
    if estimate.get("same_revision") and isinstance(predicted, int) and row.capacity_tokens is not None:
        delta = row.capacity_tokens - predicted
    return {
        **{
            k: getattr(row, k)
            for k in (
                "role",
                "gpu_type",
                "pod_name",
                "pod_uid",
                "restart",
                "engine",
                "image",
                "image_id",
                "revision",
                "fingerprint",
                "capacity_tokens",
                "budget_bytes",
                "active",
            )
        },
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        "last_seen": row.last_seen.isoformat(),
        **evidence,
        "delta_tokens": delta,
        "delta_percent": 100 * delta / predicted if delta is not None and predicted else None,
    }


async def deployment_observations(db, deployment_id):
    rows = (
        await db.scalars(
            select(Observation)
            .where(Observation.deployment_id == deployment_id)
            .order_by(Observation.active.desc(), Observation.last_seen.desc(), Observation.pod_name)
        )
    ).all()
    return [summary(r) for r in rows]
