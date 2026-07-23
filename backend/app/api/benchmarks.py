"""Admin endpoints for model benchmark runs (vllm/sglang/lm-eval).

PR-1 scope: create, list, get, cancel. The runner is a K8s Job; the worker
loop (`reconcile_benchmarks`) polls Job status and persists the result blob.
"""

import logging
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured, get_k8s_client  # noqa: F401
from app.config import settings
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.benchmark_manifests import (
    build_job_manifest,
    build_self_serving_bench_job,
    build_vllm_bench_job,
    job_name_for,
    nfs_fields_incomplete,
    resolve_bench_nfs,
)
from app.services.benchmark_presets import LOAD_PRESETS
from app.services.benchmark_serving import (
    _clone_target_port,
    build_ephemeral_deployment,
    build_external_clone,
    ephemeral_manifests,
    ephemeral_model_name,
    external_bench_facts,
    serving_cli,
    serving_resource_names,
    serving_target_url,
)
from app.services.clusters import k8s_for_cluster
from app.services.model_deployment_manifests import (
    VLLM_PORT,
    build_deployment,
    k8s_resource_names,
    serving_api_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


@router.get("/presets")
async def list_presets(user: CustomUser = Depends(require_super_user)) -> dict:
    """The fixed load presets — the benchmark measurement methodology."""
    return {"presets": LOAD_PRESETS}


ALLOWED_TOOLS = {"vllm_serving", "sglang_serving", "lm_eval"}
ALLOWED_KINDS = {"performance", "accuracy"}
TOOL_TO_KIND = {
    "vllm_serving": "performance",
    "sglang_serving": "performance",
    "lm_eval": "accuracy",
}


DEFAULT_BENCH_IMAGE = "llmops-benchmark:latest"
DEFAULT_BENCH_NAMESPACE = "default"


class _BlockStringDumper(yaml.SafeDumper):
    """YAML dumper that renders multi-line strings as literal blocks (``|``).

    Keeps the benchmark Job's shell script readable in the preview (one command
    per line) instead of PyYAML's default folded single-quoted scalar that wraps
    mid-argument.
    """


def _represent_str_block(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStringDumper.add_representer(str, _represent_str_block)


def _dump_manifest_yaml(manifest: dict) -> str:
    return yaml.dump(
        manifest,
        Dumper=_BlockStringDumper,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )


class ExternalTarget(BaseModel):
    cluster_id: str | None = None
    namespace: str
    deployment_name: str


class CreateBenchmarkRequest(BaseModel):
    model_name: str | None = Field(
        None, description="LiteLLM-registered alias (legacy mode; ignored when deployment_id is set)"
    )
    deployment_id: str | None = Field(
        None,
        description="Portal-managed serving deployment to benchmark directly (preferred). "
        "When set, the runner hits the deployment's Service URL, not the LiteLLM proxy.",
    )
    ephemeral: bool = Field(
        False,
        description="Clone deployment_id into a throwaway serving, benchmark it, then "
        "tear it down. Requires deployment_id as the template.",
    )
    serving_overrides: dict | None = Field(
        None,
        description="Overrides applied to the cloned serving (ephemeral only): "
        "image, model_path, replicas, gpu_count, gpu_type, gpu_resource_key, "
        "cpu_request/limit, memory_request/limit, vllm_extra_args, env, node_selector.",
    )
    external_target: ExternalTarget | None = Field(
        None,
        description="Benchmark a discovered external serving by cloning its live spec "
        "(ephemeral; performance tools only). Mutually exclusive with deployment_id/ephemeral.",
    )
    tool: str = Field(..., description="vllm_serving | sglang_serving | lm_eval")
    params: dict = Field(default_factory=dict, description="Tool-specific args, stored verbatim")
    cluster_id: str | None = Field(
        None, description="Registered K8s cluster to run on; None = portal default"
    )
    namespace: str | None = None
    image: str | None = None
    api_key: str | None = Field(
        None,
        description="Explicit API key the runner presents to the target. Overrides the "
        "auto-derived serving key / LiteLLM admin key when set. Use for auth-gated "
        "targets whose key the portal can't infer.",
    )


def _serving_snapshot(dep: CustomModelDeployment) -> dict:
    """Freeze a serving deployment's config so benchmark runs stay comparable
    even if the deployment is later edited or deleted."""
    return {
        "engine": "vllm",
        "image": dep.image,
        "model_path": dep.model_path,
        "vllm_extra_args": list(dep.vllm_extra_args or []),
        "env": dict(dep.env or {}),
        "replicas": dep.replicas,
        "resources": {
            "gpu_count": dep.gpu_count,
            "gpu_resource_key": dep.gpu_resource_key,
            "cpu_request": dep.cpu_request,
            "cpu_limit": dep.cpu_limit,
            "memory_request": dep.memory_request,
            "memory_limit": dep.memory_limit,
        },
        "node_selector": dict(dep.node_selector or {}),
        "namespace": dep.namespace,
        "pvc_name": dep.pvc_name,
        "pvc_mount_path": dep.pvc_mount_path,
    }


def _build_bench_job(
    run: CustomBenchmarkRun,
    *,
    deployment: "CustomModelDeployment | None",
    target_base: str,
    api_key: str,
    image_override: str | None,
    cluster_default_nfs_server: str | None = None,
    cluster_default_nfs_path: str | None = None,
    cluster_default_nfs_mount_path: str | None = None,
) -> dict:
    """Pick the manifest for a run: performance → official `vllm bench serve`
    (on a vLLM image), accuracy → the lm-eval runner image. Shared by create
    and preview so the YAML preview is exactly what runs.
    """
    params = run.params or {}
    # Model-weights mount: a deployment target reuses its own PVC; a raw
    # model_name target attaches an NFS export (run override → cluster default).
    pvc_name = pvc_mount = None
    nfs_server = nfs_path = nfs_mount = None
    if deployment is not None:
        served_model = deployment.model_path
        perf_default_image = deployment.image
        pvc_name, pvc_mount = deployment.pvc_name, deployment.pvc_mount_path
    else:
        served_model = run.model_name
        perf_default_image = settings.vllm_bench_image
        nfs_server, nfs_path, nfs_mount = resolve_bench_nfs(
            params,
            default_server=cluster_default_nfs_server,
            default_path=cluster_default_nfs_path,
            default_mount_path=cluster_default_nfs_mount_path,
        )

    if run.kind == "performance":
        tokenizer = params.get("tokenizer") or served_model
        return build_vllm_bench_job(
            run,
            image=image_override or perf_default_image,
            target_base_url=target_base,
            api_key=api_key,
            served_model=served_model,
            tokenizer=tokenizer,
            pvc_name=pvc_name,
            pvc_mount_path=pvc_mount,
            nfs_server=nfs_server,
            nfs_path=nfs_path,
            nfs_mount_path=nfs_mount,
        )
    # accuracy (lm-eval): bench_model None for a LiteLLM alias target.
    return build_job_manifest(
        run,
        image=image_override or DEFAULT_BENCH_IMAGE,
        target_base_url=target_base,
        api_key=api_key,
        bench_model=deployment.model_path if deployment is not None else None,
    )


async def _cluster_nfs_defaults(
    db: AsyncSession, cluster_uuid: uuid.UUID | None
) -> tuple[str | None, str | None, str | None]:
    """Default NFS (server, export path, mount path) for the run's cluster.

    Returns (None, None, None) for the portal default cluster or an unknown id.
    """
    if cluster_uuid is None:
        return None, None, None
    row = (
        await db.execute(
            select(CustomK8sCluster).where(CustomK8sCluster.id == cluster_uuid)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, None, None
    return row.default_nfs_server, row.default_nfs_path, row.default_nfs_mount_path


def _serialize(r: CustomBenchmarkRun) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "tool": r.tool,
        "kind": r.kind,
        "params": r.params,
        "bench_image": r.bench_image,
        "cluster_id": str(r.cluster_id) if r.cluster_id else None,
        "deployment_id": str(r.deployment_id) if r.deployment_id else None,
        "serving_snapshot": r.serving_snapshot,
        "ephemeral": r.ephemeral,
        "serving_torn_down": r.serving_torn_down,
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
) -> dict:
    if body.tool not in ALLOWED_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unknown tool '{body.tool}'")
    kind = TOOL_TO_KIND[body.tool]
    namespace = body.namespace or DEFAULT_BENCH_NAMESPACE
    image = body.image or DEFAULT_BENCH_IMAGE
    cluster_uuid = uuid.UUID(body.cluster_id) if body.cluster_id else None
    if nfs_fields_incomplete(
        body.params.get("nfs_server"),
        body.params.get("nfs_path"),
        body.params.get("nfs_mount_path"),
    ):
        raise HTTPException(
            status_code=400,
            detail="nfs_server, nfs_path and nfs_mount_path must be set together",
        )
    k8s = await k8s_for_cluster(db, cluster_uuid)

    # External-target mode: clone a serving discovered outside the portal (not
    # a portal-managed deployment) from its live K8s spec into a throwaway
    # serving, benchmark it, then tear it down. Performance tools only — the
    # target's own cluster_id (not body.cluster_id) resolves the K8s client.
    if body.external_target:
        if kind != "performance":
            raise HTTPException(status_code=400, detail="external_target supports performance benchmarks only")
        if body.deployment_id or body.ephemeral:
            raise HTTPException(
                status_code=400, detail="external_target is mutually exclusive with deployment_id/ephemeral"
            )
        ext = body.external_target
        ext_cluster_uuid = uuid.UUID(ext.cluster_id) if ext.cluster_id else None
        ext_k8s = await k8s_for_cluster(db, ext_cluster_uuid)
        try:
            spec = await ext_k8s.read_deployment(ext.namespace, ext.deployment_name)
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

        params = dict(body.params)
        params.setdefault("tokenizer", facts["tokenizer"])
        params["external_source"] = {
            "cluster_id": ext.cluster_id,
            "namespace": ext.namespace,
            "deployment_name": ext.deployment_name,
        }
        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=facts["served_model"],
            tool=body.tool,
            kind=kind,
            params=params,
            status="pending",
            cluster_id=ext_cluster_uuid,
            deployment_id=None,
            ephemeral=True,
            k8s_namespace=ext.namespace,
            bench_image=body.image or spec["container"]["image"],
            created_by=user.user_id,
            serving_snapshot={
                "source": "external",
                "image": spec["container"]["image"],
                "vllm_extra_args": serving_cli(spec["container"]),
                "env": {e["name"]: e["value"] for e in spec["container"]["env"] if e.get("value")},
                "model_path": facts["served_model"],
                "pvc_name": facts["pvc_name"],
                "pvc_mount_path": facts["pvc_mount_path"],
            },
        )
        run.k8s_job_name = job_name_for(run.id)
        run.serving_torn_down = True  # single Job — no separate serving to tear down
        clone = build_external_clone(spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides)[0]
        api_key = body.api_key or serving_api_key(serving_cli(spec["container"]), run.serving_snapshot["env"])
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        job = build_self_serving_bench_job(
            run,
            serving_deployment=clone,
            serve_argv=serving_cli(spec["container"]),
            port=_clone_target_port(spec["container"]),
            api_key=api_key,
            served_model=facts["served_model"],
            tokenizer=params.get("tokenizer"),
        )
        db.add(run)
        await db.flush()
        await db.refresh(run)
        try:
            await ext_k8s.create_job(ext.namespace, job)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.exception("Self-serving bench Job create failed for benchmark %s", run.id)
            run.status = "failed"
            run.error_message = f"Benchmark Job create failed: {e}"
            await db.flush()
            raise HTTPException(status_code=502, detail="Failed to create the benchmark Job; check logs")
        return _serialize(run)

    # Ephemeral mode: clone a template deployment into a throwaway serving. The
    # reconciler waits for it to become ready, creates the bench Job, then tears
    # the serving down. The Job is NOT created here.
    if body.ephemeral:
        if not body.deployment_id:
            raise HTTPException(status_code=400, detail="ephemeral requires deployment_id (template)")
        try:
            tmpl_id = uuid.UUID(body.deployment_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid deployment_id")
        base = (
            await db.execute(
                select(CustomModelDeployment).where(CustomModelDeployment.id == tmpl_id)
            )
        ).scalar_one_or_none()
        if not base:
            raise HTTPException(status_code=404, detail="Template deployment not found")

        run = CustomBenchmarkRun(
            id=uuid.uuid4(),
            model_name=base.model_name,
            tool=body.tool,
            kind=kind,
            params=body.params,
            status="provisioning" if kind != "performance" else "pending",
            cluster_id=cluster_uuid,
            k8s_namespace=namespace,
            deployment_id=base.id,
            ephemeral=True,
            # Perf runs `vllm bench serve` (needs a vLLM image) → reuse the
            # serving's own image; accuracy keeps the lm-eval runner image.
            bench_image=(body.image or base.image) if kind == "performance" else image,
            created_by=user.user_id,
        )
        name = ephemeral_model_name(run.id)
        eph = build_ephemeral_deployment(
            base, name=name, namespace=namespace, overrides=body.serving_overrides
        )
        run.serving_snapshot = _serving_snapshot(eph)
        if body.api_key:
            run.serving_snapshot["api_key_override"] = body.api_key
        run.k8s_job_name = job_name_for(run.id)

        if kind == "performance":
            # Single self-serving Job — no separate serving Deployment.
            run.serving_torn_down = True
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            api_key = body.api_key or serving_api_key(eph.vllm_extra_args, eph.env)
            job = build_self_serving_bench_job(
                run,
                serving_deployment=build_deployment(eph),
                serve_argv=serve_argv,
                port=VLLM_PORT,
                api_key=api_key,
                served_model=eph.model_path,
                tokenizer=(run.params or {}).get("tokenizer"),
            )
            db.add(run)
            await db.flush()
            await db.refresh(run)
            try:
                await k8s.create_job(namespace, job)
            except K8sNotConfigured as e:
                raise HTTPException(status_code=503, detail=str(e))
            except Exception as e:
                logger.exception("Self-serving bench Job create failed for benchmark %s", run.id)
                run.status = "failed"
                run.error_message = f"Benchmark Job create failed: {e}"
                await db.flush()
                raise HTTPException(status_code=502, detail="Failed to create the benchmark Job; check logs")
            return _serialize(run)

        # Accuracy (lm_eval): keep the provisioning path — serving + later Job.
        run.serving_k8s_name = name
        db.add(run)
        await db.flush()
        await db.refresh(run)
        try:
            await k8s.create_or_patch(namespace, ephemeral_manifests(eph))
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.exception("Ephemeral serving create failed for benchmark %s", run.id)
            run.status = "failed"
            run.error_message = f"Ephemeral serving create failed: {e}"
            run.serving_torn_down = True
            await db.flush()
            raise HTTPException(status_code=502, detail="Failed to provision serving; check logs")
        return _serialize(run)

    # Resolve the target: a portal-managed serving deployment (preferred — the
    # runner hits its Service directly) or a LiteLLM alias (legacy).
    deployment: CustomModelDeployment | None = None
    serving_snapshot: dict | None = None
    if body.deployment_id:
        try:
            dep_id = uuid.UUID(body.deployment_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid deployment_id")
        dep_res = await db.execute(
            select(CustomModelDeployment).where(CustomModelDeployment.id == dep_id)
        )
        deployment = dep_res.scalar_one_or_none()
        if not deployment:
            raise HTTPException(status_code=404, detail="Serving deployment not found")
        if deployment.ready_replicas < 1:
            raise HTTPException(status_code=409, detail="Serving deployment is not ready yet")
        model_name = deployment.model_name
        namespace = body.namespace or deployment.namespace
        serving_snapshot = _serving_snapshot(deployment)
    else:
        if not body.model_name:
            raise HTTPException(status_code=400, detail="model_name or deployment_id is required")
        model_name = body.model_name

    run = CustomBenchmarkRun(
        id=uuid.uuid4(),
        model_name=model_name,
        tool=body.tool,
        kind=kind,
        params=body.params,
        status="pending",
        cluster_id=cluster_uuid,
        k8s_namespace=namespace,
        deployment_id=deployment.id if deployment else None,
        serving_snapshot=serving_snapshot,
        created_by=user.user_id,
    )
    run.k8s_job_name = job_name_for(run.id)
    db.add(run)
    await db.flush()
    await db.refresh(run)

    # Where the runner sends requests. An explicit body.api_key always wins.
    if deployment is not None:
        svc = k8s_resource_names(deployment)["service"]
        target_base = f"http://{svc}.{deployment.namespace}.svc.cluster.local"
        api_key = body.api_key or serving_api_key(deployment.vllm_extra_args, deployment.env)
    else:
        target_base = settings.litellm_base_url.rstrip("/")
        api_key = body.api_key or settings.litellm_admin_api_key

    cl_nfs_server, cl_nfs_path, cl_nfs_mount = await _cluster_nfs_defaults(db, cluster_uuid)
    manifest = _build_bench_job(
        run,
        deployment=deployment,
        target_base=target_base,
        api_key=api_key,
        image_override=body.image or None,
        cluster_default_nfs_server=cl_nfs_server,
        cluster_default_nfs_path=cl_nfs_path,
        cluster_default_nfs_mount_path=cl_nfs_mount,
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


_SENSITIVE_ENV = ("api_key", "apikey", "token", "secret", "password")


def _redact(obj):
    """Mask env values whose name looks sensitive, for safe YAML previews."""
    if isinstance(obj, dict):
        name = obj.get("name")
        if "value" in obj and isinstance(name, str) and any(s in name.lower() for s in _SENSITIVE_ENV):
            return {**obj, "value": "***"}
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


@router.post("/preview")
async def preview_benchmark(
    body: CreateBenchmarkRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Render the exact K8s manifests this run would apply — no DB/K8s writes.

    Powers the live YAML preview on the run form. Sensitive env values
    (API keys / tokens) are masked.
    """
    if body.tool not in ALLOWED_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unknown tool '{body.tool}'")
    kind = TOOL_TO_KIND[body.tool]
    namespace = body.namespace or DEFAULT_BENCH_NAMESPACE

    run = CustomBenchmarkRun(
        id=uuid.uuid4(),
        model_name=body.model_name or "<model>",
        tool=body.tool,
        kind=kind,
        params=body.params,
        status="pending",
        k8s_namespace=namespace,
        created_by=user.user_id,
    )
    run.k8s_job_name = job_name_for(run.id)
    manifests: list[dict] = []

    if body.external_target:
        if kind != "performance":
            raise HTTPException(status_code=400, detail="external_target supports performance benchmarks only")
        ext = body.external_target
        ext_k8s = await k8s_for_cluster(db, uuid.UUID(ext.cluster_id) if ext.cluster_id else None)
        try:
            spec = await ext_k8s.read_deployment(ext.namespace, ext.deployment_name)
        except Exception:
            return {"manifests": [], "note": "external_spec_unavailable"}
        if spec is None:
            return {"manifests": [], "note": "external_serving_missing"}
        run.model_name = ext.deployment_name
        clone = build_external_clone(spec, name=ephemeral_model_name(run.id), overrides=body.serving_overrides)[0]
        try:
            facts = external_bench_facts(spec)
        except ValueError:
            return {"manifests": [], "note": "external_spec_unparseable"}
        manifests.append(
            build_self_serving_bench_job(
                run,
                serving_deployment=clone,
                serve_argv=serving_cli(spec["container"]),
                port=_clone_target_port(spec["container"]),
                api_key="<redacted>",
                served_model=facts["served_model"],
                tokenizer=(body.params or {}).get("tokenizer") or facts["tokenizer"],
            )
        )
    elif body.ephemeral:
        if not body.deployment_id:
            return {"manifests": [], "note": "ephemeral_needs_deployment"}
        try:
            tmpl_id = uuid.UUID(body.deployment_id)
        except ValueError:
            return {"manifests": [], "note": "invalid_deployment"}
        base = (
            await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == tmpl_id))
        ).scalar_one_or_none()
        if not base:
            return {"manifests": [], "note": "deployment_not_found"}
        name = ephemeral_model_name(run.id)
        eph = build_ephemeral_deployment(base, name=name, namespace=namespace, overrides=body.serving_overrides)
        run.model_name = base.model_name
        if kind == "performance":
            serve_argv = ["vllm", "serve", eph.model_path, "--port", str(VLLM_PORT), *(eph.vllm_extra_args or [])]
            manifests.append(
                build_self_serving_bench_job(
                    run,
                    serving_deployment=build_deployment(eph),
                    serve_argv=serve_argv,
                    port=VLLM_PORT,
                    api_key="<redacted>",
                    served_model=eph.model_path,
                    tokenizer=(body.params or {}).get("tokenizer"),
                )
            )
        else:
            manifests.extend(ephemeral_manifests(eph))
            manifests.append(
                _build_bench_job(
                    run, deployment=eph, target_base=serving_target_url(name, namespace),
                    api_key=body.api_key or serving_api_key(eph.vllm_extra_args, eph.env),
                    image_override=body.image or None,
                )
            )
    else:
        deployment: CustomModelDeployment | None = None
        if body.deployment_id:
            try:
                dep_id = uuid.UUID(body.deployment_id)
            except ValueError:
                return {"manifests": [], "note": "invalid_deployment"}
            deployment = (
                await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == dep_id))
            ).scalar_one_or_none()
            if not deployment:
                return {"manifests": [], "note": "deployment_not_found"}
        if deployment is None and not body.model_name:
            return {"manifests": [], "note": "target_required"}

        if deployment is not None:
            run.model_name = deployment.model_name
            run.k8s_namespace = body.namespace or deployment.namespace
            svc = k8s_resource_names(deployment)["service"]
            target_base = f"http://{svc}.{deployment.namespace}.svc.cluster.local"
            api_key = body.api_key or serving_api_key(deployment.vllm_extra_args, deployment.env)
        else:
            target_base = settings.litellm_base_url.rstrip("/")
            api_key = body.api_key or settings.litellm_admin_api_key
        cl_nfs_server, cl_nfs_path, cl_nfs_mount = await _cluster_nfs_defaults(
            db, uuid.UUID(body.cluster_id) if body.cluster_id else None
        )
        manifests.append(
            _build_bench_job(
                run,
                deployment=deployment,
                target_base=target_base,
                api_key=api_key,
                image_override=body.image or None,
                cluster_default_nfs_server=cl_nfs_server,
                cluster_default_nfs_path=cl_nfs_path,
                cluster_default_nfs_mount_path=cl_nfs_mount,
            )
        )

    out = [
        {
            "kind": m.get("kind", "?"),
            "name": m.get("metadata", {}).get("name", "?"),
            "yaml": _dump_manifest_yaml(_redact(m)),
        }
        for m in manifests
    ]
    return {"manifests": out, "note": None}


@router.post("/{run_id}/cancel")
async def cancel_benchmark(
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
    if run.status in ("succeeded", "failed", "cancelled"):
        return _serialize(run)

    k8s = await k8s_for_cluster(db, run.cluster_id)

    if run.k8s_job_name and run.k8s_namespace:
        try:
            await k8s.delete_job(run.k8s_namespace, run.k8s_job_name)
        except K8sNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception:
            logger.exception("K8s Job delete failed for benchmark %s", run.id)

    # Tear down the throwaway serving on cancel too.
    if run.ephemeral and run.serving_k8s_name and not run.serving_torn_down and run.k8s_namespace:
        try:
            await k8s.delete(run.k8s_namespace, serving_resource_names(run.serving_k8s_name))
            run.serving_torn_down = True
        except K8sNotConfigured:
            pass
        except Exception:
            logger.exception("Ephemeral serving delete failed for benchmark %s", run.id)

    run.status = "cancelled"
    await db.flush()
    return _serialize(run)
