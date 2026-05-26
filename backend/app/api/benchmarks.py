"""Admin endpoints for model benchmark runs (vllm/sglang/lm-eval).

PR-1 scope: create, list, get, cancel. The runner is a K8s Job; the worker
loop (`reconcile_benchmarks`) polls Job status and persists the result blob.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured, get_k8s_client
from app.config import settings
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.benchmark_manifests import build_job_manifest, job_name_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


ALLOWED_TOOLS = {"vllm_serving", "sglang_serving", "lm_eval"}
ALLOWED_KINDS = {"performance", "accuracy"}
TOOL_TO_KIND = {
    "vllm_serving": "performance",
    "sglang_serving": "performance",
    "lm_eval": "accuracy",
}


DEFAULT_BENCH_IMAGE = "llmops-benchmark:latest"
DEFAULT_BENCH_NAMESPACE = "default"


class CreateBenchmarkRequest(BaseModel):
    model_name: str = Field(..., description="LiteLLM-registered model alias")
    tool: str = Field(..., description="vllm_serving | sglang_serving | lm_eval")
    params: dict = Field(default_factory=dict, description="Tool-specific args, stored verbatim")
    namespace: str | None = None
    image: str | None = None


def _serialize(r: CustomBenchmarkRun) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "tool": r.tool,
        "kind": r.kind,
        "params": r.params,
        "status": r.status,
        "k8s_job_name": r.k8s_job_name,
        "k8s_namespace": r.k8s_namespace,
        "result": r.result,
        "error_message": r.error_message,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


@router.get("")
async def list_benchmarks(
    model_name: str | None = None,
    tool: str | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(CustomBenchmarkRun)
    if model_name:
        query = query.where(CustomBenchmarkRun.model_name == model_name)
    if tool:
        query = query.where(CustomBenchmarkRun.tool == tool)
    if status_filter:
        query = query.where(CustomBenchmarkRun.status == status_filter)
    query = query.order_by(CustomBenchmarkRun.created_at.desc()).limit(min(limit, 500))
    result = await db.execute(query)
    return {"runs": [_serialize(r) for r in result.scalars().all()]}


@router.get("/{run_id}")
async def get_benchmark(
    run_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    result = await db.execute(select(CustomBenchmarkRun).where(CustomBenchmarkRun.id == rid))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return _serialize(run)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_benchmark(
    body: CreateBenchmarkRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    k8s: K8sClient = Depends(get_k8s_client),
) -> dict:
    if body.tool not in ALLOWED_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unknown tool '{body.tool}'")
    kind = TOOL_TO_KIND[body.tool]
    namespace = body.namespace or DEFAULT_BENCH_NAMESPACE
    image = body.image or DEFAULT_BENCH_IMAGE

    run = CustomBenchmarkRun(
        id=uuid.uuid4(),
        model_name=body.model_name,
        tool=body.tool,
        kind=kind,
        params=body.params,
        status="pending",
        k8s_namespace=namespace,
        created_by=user.user_id,
    )
    run.k8s_job_name = job_name_for(run.id)
    db.add(run)
    await db.flush()
    await db.refresh(run)

    target_base = settings.litellm_base_url.rstrip("/")
    manifest = build_job_manifest(
        run,
        image=image,
        target_base_url=target_base,
        api_key=settings.litellm_admin_api_key,
    )
    try:
        await k8s.create_job(namespace, manifest)
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("K8s Job create failed for benchmark %s", run.id)
        # Mark the row failed so it doesn't sit pending forever.
        run.status = "failed"
        run.error_message = f"K8s Job create failed: {e}"
        await db.flush()
        raise HTTPException(status_code=502, detail="Failed to schedule benchmark; check logs")

    return _serialize(run)


@router.post("/{run_id}/cancel")
async def cancel_benchmark(
    run_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    k8s: K8sClient = Depends(get_k8s_client),
) -> dict:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    result = await db.execute(select(CustomBenchmarkRun).where(CustomBenchmarkRun.id == rid))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    if run.status in ("succeeded", "failed", "cancelled"):
        return _serialize(run)

    if run.k8s_job_name and run.k8s_namespace:
        try:
            await k8s.delete_job(run.k8s_namespace, run.k8s_job_name)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            logger.exception("K8s Job delete failed for benchmark %s", run.id)

    run.status = "cancelled"
    await db.flush()
    return _serialize(run)
