"""Admin endpoints for benchmark sweeps: one submission expands 1-2 serve-flag
variables into sequential self-serving benchmark Jobs under a fixed load
preset. Combos are ordinary CustomBenchmarkRun rows (status `queued`) whose
Job manifests are prebuilt at submit (freeze-at-submit); the reconciler
promotes the next combo when the previous one reaches a terminal state."""

import logging
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.benchmarks import (
    DEFAULT_BENCH_NAMESPACE,
    ExternalTarget,
    _serialize,
    _serving_snapshot,
)
from app.auth.deps import require_super_user
from app.clients.k8s import K8sNotConfigured
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_benchmark_sweep import CustomBenchmarkSweep
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.benchmark_manifests import build_self_serving_bench_job
from app.services.benchmark_presets import LOAD_PRESETS, preset_params
from app.services.benchmark_serving import (
    _clone_target_port,
    build_ephemeral_deployment,
    build_external_clone,
    ephemeral_model_name,
    external_bench_facts,
    serving_cli,
)
from app.services.benchmark_sweeps import expand_combos, merge_serve_argv, promote_queued_run
from app.services.clusters import k8s_for_cluster
from app.services.model_deployment_manifests import VLLM_PORT, build_deployment, serving_api_key

logger = logging.getLogger(__name__)

# Same prefix as the runs router; registered BEFORE it in main.py so /presets
# and /sweeps* match ahead of the GET /{run_id} catch-all.
router = APIRouter(prefix="/api/benchmarks", tags=["benchmark-sweeps"])

_FLAG_RE = re.compile(r"^--[a-z0-9][a-z0-9-]*$")
SWEEP_TERMINAL = ("succeeded", "failed", "cancelled")


def _now():
    return datetime.now(UTC)


class SweepVariable(BaseModel):
    flag: str
    values: list[int | float | str] = Field(..., min_length=1)


class CreateSweepRequest(BaseModel):
    name: str | None = None
    deployment_id: str | None = None
    external_target: ExternalTarget | None = None
    cluster_id: str | None = None
    namespace: str | None = None
    preset: str
    variables: list[SweepVariable]
    serving_overrides: dict | None = None
    api_key: str | None = None


def _serialize_sweep(s: CustomBenchmarkSweep, *, progress: dict | None = None, runs: list | None = None) -> dict:
    out = {
        "id": str(s.id),
        "name": s.name,
        "deployment_id": str(s.deployment_id) if s.deployment_id else None,
        "external_source": s.external_source,
        "cluster_id": str(s.cluster_id) if s.cluster_id else None,
        "k8s_namespace": s.k8s_namespace,
        "preset": s.preset,
        "variables": s.variables,
        "serving_overrides": s.serving_overrides,
        "status": s.status,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }
    if progress is not None:
        out["progress"] = progress
    if runs is not None:
        out["runs"] = runs
    return out


def _validated_combos(body: CreateSweepRequest) -> list[dict]:
    if body.preset not in LOAD_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{body.preset}'")
    if not 1 <= len(body.variables) <= 2:
        raise HTTPException(status_code=400, detail="1-2 sweep variables required")
    flags = [v.flag for v in body.variables]
    if len(set(flags)) != len(flags):
        raise HTTPException(status_code=400, detail="Sweep variable flags must be distinct")
    for v in body.variables:
        if not _FLAG_RE.match(v.flag):
            raise HTTPException(
                status_code=400, detail=f"Invalid flag '{v.flag}' (expected --lower-kebab-case)"
            )
    combos = expand_combos([v.model_dump() for v in body.variables])
    if not 2 <= len(combos) <= 12:
        raise HTTPException(status_code=400, detail=f"Sweep must expand to 2-12 combos (got {len(combos)})")
    if bool(body.deployment_id) == bool(body.external_target):
        raise HTTPException(status_code=400, detail="Exactly one of deployment_id or external_target is required")
    return combos


@router.get("/presets")
async def list_presets(user: CustomUser = Depends(require_super_user)) -> dict:
    """The fixed load presets — the portal's benchmark methodology."""
    return {"presets": LOAD_PRESETS}


@router.post("/sweeps", status_code=status.HTTP_201_CREATED)
async def create_sweep(
    body: CreateSweepRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    combos = _validated_combos(body)
    params = preset_params(body.preset)

    if body.external_target:
        ext = body.external_target
        cluster_uuid = uuid.UUID(ext.cluster_id) if ext.cluster_id else None
        namespace = ext.namespace
        k8s = await k8s_for_cluster(db, cluster_uuid)
        try:
            spec = await k8s.read_deployment(ext.namespace, ext.deployment_name)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            logger.exception("Live spec read failed for %s/%s", ext.namespace, ext.deployment_name)
            raise HTTPException(status_code=502, detail="Failed to read the external serving's spec; check logs")
        if spec is None:
            raise HTTPException(status_code=404, detail="External serving no longer exists")
        try:
            facts = external_bench_facts(spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        base_argv = serving_cli(spec["container"])
        port = _clone_target_port(spec["container"])
        env_map = {e["name"]: e["value"] for e in spec["container"]["env"] if e.get("value")}
        model_name = facts["served_model"]
        params = {**params, "tokenizer": facts["tokenizer"]}
        external_source = {
            "cluster_id": ext.cluster_id,
            "namespace": ext.namespace,
            "deployment_name": ext.deployment_name,
        }
        deployment_uuid = None
        base = None
    else:
        try:
            tmpl_id = uuid.UUID(body.deployment_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid deployment_id")
        base = (
            await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == tmpl_id))
        ).scalar_one_or_none()
        if not base:
            raise HTTPException(status_code=404, detail="Template deployment not found")
        cluster_uuid = uuid.UUID(body.cluster_id) if body.cluster_id else None
        namespace = body.namespace or DEFAULT_BENCH_NAMESPACE
        model_name = base.model_name
        external_source = None
        deployment_uuid = base.id

    sweep = CustomBenchmarkSweep(
        id=uuid.uuid4(),
        name=(body.name or "").strip() or None,
        deployment_id=deployment_uuid,
        external_source=external_source,
        cluster_id=cluster_uuid,
        k8s_namespace=namespace,
        preset=body.preset,
        variables=[v.model_dump() for v in body.variables],
        serving_overrides=body.serving_overrides,
        status="running",
        created_by=user.user_id,
    )
    db.add(sweep)

    runs: list[CustomBenchmarkRun] = []
    for idx, combo in enumerate(combos):
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=model_name,
            tool="vllm_serving",
            kind="performance",
            params=params,
            status="queued",
            cluster_id=cluster_uuid,
            k8s_namespace=namespace,
            deployment_id=deployment_uuid,
            ephemeral=True,
            serving_torn_down=True,  # single self-serving Job; nothing separate to tear down
            created_by=user.user_id,
            sweep_id=sweep.id,
            sweep_index=idx,
            sweep_combo=combo,
        )
        if body.external_target:
            serve_argv = merge_serve_argv(base_argv, combo)
            clone = build_external_clone(
                spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides
            )[0]
            run.bench_image = spec["container"]["image"]
            run.serving_snapshot = {
                "source": "external",
                "image": spec["container"]["image"],
                "vllm_extra_args": serve_argv,
                "env": env_map,
                "model_path": facts["served_model"],
                "pvc_name": facts["pvc_name"],
                "pvc_mount_path": facts["pvc_mount_path"],
            }
            api_key = body.api_key or serving_api_key(serve_argv, env_map)
            run.queued_job_manifest = build_self_serving_bench_job(
                run,
                serving_deployment=clone,
                serve_argv=serve_argv,
                port=port,
                api_key=api_key,
                served_model=facts["served_model"],
                tokenizer=facts["tokenizer"],
            )
        else:
            eph = build_ephemeral_deployment(
                base, name=ephemeral_model_name(run.id), namespace=namespace, overrides=body.serving_overrides
            )
            eph.vllm_extra_args = merge_serve_argv(list(eph.vllm_extra_args or []), combo)
            run.bench_image = eph.image
            run.serving_snapshot = _serving_snapshot(eph)
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            api_key = body.api_key or serving_api_key(eph.vllm_extra_args, eph.env)
            run.queued_job_manifest = build_self_serving_bench_job(
                run,
                serving_deployment=build_deployment(eph),
                serve_argv=serve_argv,
                port=VLLM_PORT,
                api_key=api_key,
                served_model=eph.model_path,
                tokenizer=params.get("tokenizer"),
            )
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        db.add(run)
        runs.append(run)
    await db.flush()

    # Promote combo #0 now; a create failure marks it failed and leaves the
    # sweep running — the reconciler promotes the next combo on its next tick.
    k8s = await k8s_for_cluster(db, cluster_uuid)
    try:
        await promote_queued_run(k8s, runs[0])
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Sweep combo #0 Job create failed for sweep %s", sweep.id)
        runs[0].status = "failed"
        runs[0].error_message = f"Benchmark Job create failed: {e}"
        runs[0].queued_job_manifest = None
    await db.flush()
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])


@router.get("/sweeps")
async def list_sweeps(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (
        await db.execute(
            select(CustomBenchmarkSweep).order_by(CustomBenchmarkSweep.created_at.desc()).limit(200)
        )
    ).scalars().all()
    ids = [s.id for s in rows]
    progress: dict = {sid: {"total": 0, "by_status": {}} for sid in ids}
    if ids:
        counts = await db.execute(
            select(CustomBenchmarkRun.sweep_id, CustomBenchmarkRun.status, func.count())
            .where(CustomBenchmarkRun.sweep_id.in_(ids))
            .group_by(CustomBenchmarkRun.sweep_id, CustomBenchmarkRun.status)
        )
        for sweep_id, run_status, n in counts.all():
            progress[sweep_id]["total"] += n
            progress[sweep_id]["by_status"][run_status] = n
    return {"sweeps": [_serialize_sweep(s, progress=progress[s.id]) for s in rows]}


async def _get_sweep(db: AsyncSession, sweep_id: str) -> CustomBenchmarkSweep:
    try:
        sid = uuid.UUID(sweep_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sweep not found")
    sweep = (
        await db.execute(select(CustomBenchmarkSweep).where(CustomBenchmarkSweep.id == sid))
    ).scalar_one_or_none()
    if not sweep:
        raise HTTPException(status_code=404, detail="Sweep not found")
    return sweep


async def _sweep_runs(db: AsyncSession, sweep_id: uuid.UUID) -> list[CustomBenchmarkRun]:
    return (
        await db.execute(
            select(CustomBenchmarkRun)
            .where(CustomBenchmarkRun.sweep_id == sweep_id)
            .order_by(CustomBenchmarkRun.sweep_index)
        )
    ).scalars().all()


@router.get("/sweeps/{sweep_id}")
async def get_sweep(
    sweep_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sweep = await _get_sweep(db, sweep_id)
    runs = await _sweep_runs(db, sweep.id)
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])


@router.post("/sweeps/{sweep_id}/cancel")
async def cancel_sweep(
    sweep_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sweep = await _get_sweep(db, sweep_id)
    if sweep.status in ("completed", "cancelled"):
        return _serialize_sweep(sweep)
    runs = await _sweep_runs(db, sweep.id)
    k8s = await k8s_for_cluster(db, sweep.cluster_id)
    for run in runs:
        if run.status in SWEEP_TERMINAL:
            continue
        if run.k8s_job_name and run.k8s_namespace:
            try:
                await k8s.delete_job(run.k8s_namespace, run.k8s_job_name)
            except K8sNotConfigured as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception:
                logger.exception("Sweep run Job delete failed for %s", run.id)
        run.status = "cancelled"
        run.queued_job_manifest = None
        run.finished_at = _now()
    sweep.status = "cancelled"
    sweep.finished_at = _now()
    await db.flush()
    return _serialize_sweep(sweep, runs=[_serialize(r) for r in runs])
