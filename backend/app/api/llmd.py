"""Admin endpoints for llm-d serving stacks (ArgoCD-managed).

The portal renders an argoproj.io Application per stack and sends it to ArgoCD's
REST API through a registered ArgoCD connection. Sync/health status is read live
from the Application — never persisted. Applications are scoped to a dedicated
AppProject + namespace so they cannot affect other projects' apps.
"""

import json
import logging
import uuid

import httpx
import yaml
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
from app.services.llmd_manifests import (
    argo_app_name_for,
    build_argo_application,
    build_llmd_values,
    default_llmd_values,
)
from app.services.yaml_block import dump_block_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llmd-stacks", tags=["llmd-stacks"])


class CreateLlmdStackRequest(BaseModel):
    name: str
    target_model_name: str  # an existing model deployment the router targets
    argocd_connection_id: str
    namespace: str = "default"
    values_yaml: str = ""  # full Helm values.yaml the user authored


class UpdateLlmdStackRequest(BaseModel):
    namespace: str | None = None
    values_yaml: str | None = None


class DefaultValuesRequest(BaseModel):
    target_model_name: str = ""


def _parse_values_yaml(text: str) -> dict:
    """Parse the user's values.yaml into a dict. Empty -> {}. Raises 400 on
    invalid YAML or a non-mapping top level."""
    if not text or not text.strip():
        return {}
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid values YAML: {e}")
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="values.yaml must be a mapping (key: value)")
    return parsed


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


def _require_valid_name(name: str) -> str:
    """Validate the stack name and return its ArgoCD Application/Helm release name.

    The release name (``llmd-<name>``) must survive sanitising to something
    non-empty and stay within Helm's 53-char limit — otherwise ArgoCD fails late
    with a cryptic ``helm template`` error, so we reject early with a clear 400."""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Stack name is required.")
    app_name = argo_app_name_for(name)
    if app_name in ("llmd-", "llmd"):
        raise HTTPException(
            status_code=400,
            detail="Stack name must contain letters or digits (a–z, 0–9, hyphen).",
        )
    if len(app_name) > 53:
        raise HTTPException(
            status_code=400,
            detail=f"Stack name is too long — the resulting app name '{app_name}' exceeds 53 characters.",
        )
    return app_name


def _argo_response_error(resp: httpx.Response) -> str | None:
    """Pull ArgoCD's own error text out of a response body. ArgoCD returns
    ``{"error": "...", "message": "..."}``; for a 400 this carries the real
    reason (e.g. a failed `helm template`), which httpx's status message omits."""
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text[:600] or None
    if isinstance(body, dict):
        msg = body.get("error") or body.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:600]
    return None


def _argo_error_message(e: Exception) -> str:
    """Human-readable reason an ArgoCD call failed, for surfacing in the UI.

    For HTTP errors the server's own response body (the real cause — e.g. a chart
    that failed to render) is preferred over httpx's generic status string."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return "ArgoCD authentication failed — the connection token may be expired or invalid."
        detail = _argo_response_error(e.response)
        return detail or f"ArgoCD returned HTTP {code}."
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "Could not reach the ArgoCD server — check the connection URL and that ArgoCD is running."
    if isinstance(e, httpx.TimeoutException):
        return "Timed out talking to ArgoCD."
    return str(e) or "ArgoCD request failed."


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
        "chart_repo": settings.llmd_chart_repo,
        "chart_name": settings.llmd_chart_name,
        "chart_version": settings.llmd_chart_version,
        "helm_values": stack.helm_values,
        "values_yaml": yaml.safe_dump(stack.helm_values, sort_keys=False, default_flow_style=False) if stack.helm_values else "",
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


@router.get("/{stack_id}/applied")
async def applied_values(
    stack_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """How the stack's values were applied. Super user only.

    Returns the effective Helm values the portal rendered and sent
    (``values_snapshot``), plus the live ArgoCD state — the applied
    ``helm.valuesObject`` and the deployed resources (kind/name/sync/health) —
    so you can see what actually got created in the cluster. The live fields are
    best-effort: null/empty when ArgoCD is unreachable or the app isn't synced.
    """
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")

    live_values: dict | None = None
    resources: list[dict] = []
    revision: str | None = None
    live_error: str | None = None
    if stack.argocd_connection_id is None:
        live_error = "No ArgoCD connection is set for this stack."
    else:
        try:
            conn = await _connection(db, stack.argocd_connection_id)
            obj = await _client(conn).get_application(stack.argo_app_name)
            if obj:
                src = (obj.get("spec") or {}).get("source") or {}
                live_values = (src.get("helm") or {}).get("valuesObject")
                st = obj.get("status") or {}
                revision = (st.get("sync") or {}).get("revision")
                resources = [
                    {
                        "group": r.get("group") or "",
                        "version": r.get("version") or "v1",
                        "kind": r.get("kind"),
                        "name": r.get("name"),
                        "namespace": r.get("namespace"),
                        "status": r.get("status"),
                        "health": (r.get("health") or {}).get("status"),
                    }
                    for r in (st.get("resources") or [])
                ]
            else:
                live_error = "The ArgoCD Application was not found — it may have been deleted."
        except Exception as e:  # noqa: BLE001 — live state is best-effort
            logger.info("llm-d applied read failed for %s: %s", stack.name, e)
            live_error = _argo_error_message(e)

    return {
        "effective_values": stack.values_snapshot,
        "live_values": live_values,
        "resources": resources,
        "revision": revision,
        "live_error": live_error,
    }


@router.get("/{stack_id}/resource")
async def resource_manifest(
    stack_id: str,
    kind: str,
    name: str,
    namespace: str,
    version: str = "v1",
    group: str = "",
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live manifest of one deployed resource of the stack, fetched from ArgoCD.
    Super user only."""
    stack = (
        await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.id == uuid.UUID(stack_id)))
    ).scalar_one_or_none()
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")
    if stack.argocd_connection_id is None:
        raise HTTPException(status_code=400, detail="Stack has no ArgoCD connection")

    conn = await _connection(db, stack.argocd_connection_id)
    try:
        raw = await _client(conn).get_resource(
            stack.argo_app_name, name=name, namespace=namespace, kind=kind, version=version, group=group
        )
    except Exception as e:  # noqa: BLE001
        logger.info("llm-d resource read failed for %s/%s: %s", kind, name, e)
        raise HTTPException(status_code=502, detail=f"ArgoCD resource read failed: {_argo_error_message(e)}")
    if raw is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError):
        manifest = {}
    return {
        "manifest": manifest,
        # Block scalars (|) so embedded multi-line values like data."envoy.yaml"
        # render line-by-line instead of PyYAML's \n-escaped folded scalar.
        "manifest_yaml": dump_block_yaml(manifest) if manifest else (raw or ""),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_stack(
    body: CreateLlmdStackRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    app_name = _require_valid_name(body.name)
    if not body.namespace or not body.namespace.strip():
        raise HTTPException(status_code=400, detail="Namespace is required.")
    if (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == body.name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Stack '{body.name}' already exists")
    conn = await _connection(db, uuid.UUID(body.argocd_connection_id))

    # Empty values.yaml -> fall back to the default template for the target model.
    helm_values = _parse_values_yaml(body.values_yaml) or default_llmd_values(
        body.target_model_name, image_registry=settings.llmd_image_registry
    )
    stack = CustomLlmdStack(
        id=uuid.uuid4(),
        name=body.name,
        target_model_name=body.target_model_name,
        argocd_connection_id=conn.id,
        namespace=body.namespace,
        argo_app_name=app_name,
        helm_values=helm_values,
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
        raise HTTPException(status_code=502, detail=f"ArgoCD create failed: {_argo_error_message(e)}")
    await db.commit()
    await db.refresh(stack)
    return _serialize(stack, await _live_status(db, stack))


@router.post("/default-values")
async def default_values(
    body: DefaultValuesRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    """The starter values.yaml for a new stack — a correct minimal template the
    user edits. endpointSelector defaults to the chosen target model."""
    values = default_llmd_values(body.target_model_name, image_registry=settings.llmd_image_registry)
    return {
        "values": values,
        "values_yaml": yaml.safe_dump(values, sort_keys=False, default_flow_style=False),
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
    if body.values_yaml is not None:
        stack.helm_values = _parse_values_yaml(body.values_yaml)
    stack.values_snapshot = _values_for(stack)
    stack.updated_by = user.user_id
    await db.flush()

    conn = await _connection(db, stack.argocd_connection_id)
    try:
        await _client(conn).create_application(_application_for(stack))  # upsert
    except Exception as e:
        logger.exception("ArgoCD Application update failed for stack %s", stack.name)
        raise HTTPException(status_code=502, detail=f"ArgoCD update failed: {_argo_error_message(e)}")
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
            raise HTTPException(status_code=502, detail=f"ArgoCD delete failed: {_argo_error_message(e)}")
    await db.delete(stack)
    await db.commit()
    return {"ok": True}
