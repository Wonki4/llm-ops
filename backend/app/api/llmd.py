"""Admin endpoints for llm-d serving stacks (ArgoCD-managed).

The portal renders an argoproj.io Application per stack and sends it to ArgoCD's
REST API through a registered ArgoCD connection. Sync/health status is read live
from the Application — never persisted. Applications are scoped to a dedicated
AppProject + namespace so they cannot affect other projects' apps.
"""

import logging
import types
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.argocd import ArgoCDClient
from app.config import settings
from app.db.models.custom_argocd_connection import CustomArgocdConnection
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import crypto
from app.services.llmd_manifests import argo_app_name_for, build_argo_application, build_llmd_values
from app.services.yaml_block import dump_block_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llmd-stacks", tags=["llmd-stacks"])


class CreateLlmdStackRequest(BaseModel):
    name: str
    target_model_name: str  # an existing model deployment the router targets
    argocd_connection_id: str
    namespace: str = "default"
    replicas: int = 1
    model_server_type: str = "vllm"
    target_port: int = 8000
    endpoint_selector: str | None = None  # null = derive from target_model_name
    values_override: dict = {}


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    replicas: int | None = None
    model_server_type: str | None = None
    target_port: int | None = None
    endpoint_selector: str | None = None
    values_override: dict | None = None


class PreviewLlmdStackRequest(BaseModel):
    name: str = ""
    target_model_name: str = ""
    namespace: str = "default"
    replicas: int = 1
    model_server_type: str = "vllm"
    target_port: int = 8000
    endpoint_selector: str | None = None
    values_override: dict = {}


def _argo_status(obj: dict | None) -> dict:
    """Extract sync/health from an Application (Unknown when absent)."""
    if not obj:
        return {"sync_status": "Unknown", "health_status": "Unknown", "status_message": None}
    st = obj.get("status", {}) or {}
    return {
        "sync_status": (st.get("sync") or {}).get("status", "Unknown"),
        "health_status": (st.get("health") or {}).get("status", "Unknown"),
        "status_message": (st.get("health") or {}).get("message"),
    }


def _values_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_values(stack, image_registry=settings.llmd_image_registry)


def _application_for(stack: CustomLlmdStack) -> dict:
    return build_argo_application(
        stack,
        chart_repo=settings.llmd_chart_repo,
        chart_name=settings.llmd_chart_name,
        chart_version=settings.llmd_chart_version,
        values=stack.values_snapshot,
        project=settings.argo_project,
    )


async def _connection(db: AsyncSession, connection_id: uuid.UUID) -> CustomArgocdConnection:
    conn = (
        await db.execute(
            select(CustomArgocdConnection).where(CustomArgocdConnection.id == connection_id)
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="ArgoCD connection not found")
    return conn


def _client(conn: CustomArgocdConnection) -> ArgoCDClient:
    return ArgoCDClient(
        conn.server_url,
        crypto.decrypt(conn.token_encrypted),
        insecure_skip_verify=conn.insecure_skip_verify,
    )


async def _live_status(db: AsyncSession, stack: CustomLlmdStack) -> dict:
    if stack.argocd_connection_id is None:
        return _argo_status(None)
    try:
        conn = await _connection(db, stack.argocd_connection_id)
        obj = await _client(conn).get_application(stack.argo_app_name)
        return _argo_status(obj)
    except Exception as e:  # noqa: BLE001 — status is best-effort
        logger.info("llm-d status read failed for %s: %s", stack.name, e)
        return _argo_status(None)


def _serialize(stack: CustomLlmdStack, status_fields: dict) -> dict:
    return {
        "id": str(stack.id),
        "name": stack.name,
        "target_model_name": stack.target_model_name,
        "argocd_connection_id": str(stack.argocd_connection_id) if stack.argocd_connection_id else None,
        "cluster_id": str(stack.cluster_id) if stack.cluster_id else None,
        "namespace": stack.namespace,
        "argo_app_name": stack.argo_app_name,
        "replicas": stack.replicas,
        "model_server_type": stack.model_server_type,
        "target_port": stack.target_port,
        "endpoint_selector": stack.endpoint_selector,
        "values_override": stack.values_override,
        "created_by": stack.created_by,
        "created_at": stack.created_at.isoformat() if stack.created_at else None,
        "updated_at": stack.updated_at.isoformat() if stack.updated_at else None,
        **status_fields,
    }


@router.get("")
async def list_stacks(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.execute(select(CustomLlmdStack).order_by(CustomLlmdStack.created_at.desc()))).scalars().all()
    return {"stacks": [_serialize(s, await _live_status(db, s)) for s in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == body.name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{body.name}' already exists")
    conn = await _connection(db, uuid.UUID(body.argocd_connection_id))

    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=body.name,
        target_model_name=body.target_model_name,
        argocd_connection_id=conn.id,
        namespace=body.namespace,
        argo_app_name=argo_app_name_for(body.name),
        replicas=body.replicas,
        model_server_type=body.model_server_type,
        target_port=body.target_port,
        endpoint_selector=body.endpoint_selector,
        values_override=body.values_override or {},
        values_snapshot={},
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    stack.values_snapshot = _values_for(stack)
    db.add(stack)
    await db.flush()

    try:
        await _client(conn).create_application(_application_for(stack))
    except Exception as e:
        logger.exception("ArgoCD Application create failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD create failed: {e}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


@router.post("/preview")
async def preview_stack(
    body: PreviewLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    """Render the ArgoCD Application this stack would create — no DB/ArgoCD writes."""
    stack = types.SimpleNamespace(
        target_model_name=body.target_model_name or "<existing-model>",
        namespace=body.namespace or "default",
        replicas=body.replicas,
        model_server_type=body.model_server_type,
        target_port=body.target_port,
        endpoint_selector=body.endpoint_selector,
        values_override=body.values_override or {},
        argo_app_name=argo_app_name_for(body.name or "stack"),
    )
    values = build_llmd_values(stack, image_registry=settings.llmd_image_registry)
    app_body = build_argo_application(
        stack,
        chart_repo=settings.llmd_chart_repo,
        chart_name=settings.llmd_chart_name,
        chart_version=settings.llmd_chart_version,
        values=values,
        project=settings.argo_project,
    )
    return {
        "manifests": [
            {"kind": "Application", "name": stack.argo_app_name, "yaml": dump_block_yaml(app_body)},
        ],
        "note": None,
    }


@router.put("/{stack_id}")
async def update_stack(
    stack_id: str,
    body: UpdateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")

    if body.namespace is not None:
        stack.namespace = body.namespace
    if body.replicas is not None:
        stack.replicas = body.replicas
    if body.model_server_type is not None:
        stack.model_server_type = body.model_server_type
    if body.target_port is not None:
        stack.target_port = body.target_port
    if body.endpoint_selector is not None:
        stack.endpoint_selector = body.endpoint_selector or None
    if body.values_override is not None:
        stack.values_override = body.values_override
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    conn = await _connection(db, stack.argocd_connection_id)
    try:
        await _client(conn).create_application(_application_for(stack))  # upsert
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {e}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


@router.delete("/{stack_id}")
async def delete_stack(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")
    if stack.argocd_connection_id is not None:
        conn = await _connection(db, stack.argocd_connection_id)
        try:
            await _client(conn).delete_application(stack.argo_app_name)
        except Exception as e:
            logger.exception("ArgoCD Application delete failed for stack %s", stack.name)
            raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {e}")
    await db.delete(stack)
    await db.commit()
    return {"ok": True}
