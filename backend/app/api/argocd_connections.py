"""Admin endpoints for registering ArgoCD connections.

Stores an ArgoCD server URL + API token. The token is Fernet-encrypted at rest
and never returned to the client — list/get responses are masked and expose only
``has_token``. llm-d stacks reference a connection by ``id`` to manage their
Applications through ArgoCD's REST API.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.argocd import probe_argocd
from app.db.models.custom_argocd_connection import CustomArgocdConnection
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import crypto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/argocd-connections", tags=["argocd-connections"])


class CreateConnectionRequest(BaseModel):
    name: str
    server_url: str
    token: str
    insecure_skip_verify: bool = False
    description: str | None = None
    is_default: bool = False


class UpdateConnectionRequest(BaseModel):
    name: str | None = None
    server_url: str | None = None
    token: str | None = None  # omitted/empty = keep existing
    insecure_skip_verify: bool | None = None
    description: str | None = None
    is_default: bool | None = None


class TestConnectionRequest(BaseModel):
    server_url: str
    token: str
    insecure_skip_verify: bool = False


def _serialize(c: CustomArgocdConnection) -> dict:
    """Masked representation — never includes the token."""
    return {
        "id": str(c.id),
        "name": c.name,
        "server_url": c.server_url,
        "insecure_skip_verify": c.insecure_skip_verify,
        "is_default": c.is_default,
        "description": c.description,
        "has_token": bool(c.token_encrypted),
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def _unset_other_defaults(db: AsyncSession, keep_id: uuid.UUID | None) -> None:
    stmt = update(CustomArgocdConnection).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(CustomArgocdConnection.id != keep_id)
    await db.execute(stmt)


async def _run_probe(server_url: str, token: str, insecure: bool) -> dict:
    try:
        version = await probe_argocd(server_url, token, insecure_skip_verify=insecure)
        return {"ok": True, "server_version": version, "message": "Connected"}
    except Exception as e:  # noqa: BLE001 — surface any connection error to the admin
        logger.info("ArgoCD connection test failed: %s", e)
        return {"ok": False, "server_version": None, "message": str(e)}


@router.get("")
async def list_connections(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomArgocdConnection).order_by(
            CustomArgocdConnection.is_default.desc(), CustomArgocdConnection.created_at.desc()
        )
    )
    return {"connections": [_serialize(c) for c in result.scalars().all()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: CreateConnectionRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.execute(
        select(CustomArgocdConnection).where(CustomArgocdConnection.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Connection '{body.name}' already exists")

    conn = CustomArgocdConnection(
        id=uuid.uuid4(),
        name=body.name,
        server_url=body.server_url,
        token_encrypted=crypto.encrypt(body.token),
        insecure_skip_verify=body.insecure_skip_verify,
        is_default=body.is_default,
        description=body.description,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(conn)
    await db.flush()
    if body.is_default:
        await _unset_other_defaults(db, keep_id=conn.id)
    await db.commit()
    await db.refresh(conn)
    return _serialize(conn)


@router.put("/{connection_id}")
async def update_connection(
    connection_id: str,
    body: UpdateConnectionRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomArgocdConnection).where(CustomArgocdConnection.id == uuid.UUID(connection_id))
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    if body.name is not None:
        conn.name = body.name
    if body.server_url is not None:
        conn.server_url = body.server_url
    if body.description is not None:
        conn.description = body.description
    if body.insecure_skip_verify is not None:
        conn.insecure_skip_verify = body.insecure_skip_verify
    if (body.token or "").strip():
        conn.token_encrypted = crypto.encrypt(body.token)
    if body.is_default is not None:
        conn.is_default = body.is_default

    conn.updated_by = user.user_id
    await db.flush()
    if body.is_default:
        await _unset_other_defaults(db, keep_id=conn.id)
    await db.commit()
    await db.refresh(conn)
    return _serialize(conn)


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomArgocdConnection).where(CustomArgocdConnection.id == uuid.UUID(connection_id))
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    await db.delete(conn)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Connection is in use by an llm-d stack and cannot be deleted",
        )
    return {"ok": True}


@router.post("/test")
async def test_unsaved_connection(
    body: TestConnectionRequest,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    return await _run_probe(body.server_url, body.token, body.insecure_skip_verify)


@router.post("/{connection_id}/test")
async def test_saved_connection(
    connection_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(CustomArgocdConnection).where(CustomArgocdConnection.id == uuid.UUID(connection_id))
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    token = crypto.decrypt(conn.token_encrypted)
    return await _run_probe(conn.server_url, token, conn.insecure_skip_verify)
