"""Administrator projection of live LiteLLM connections. Credentials never leave this API."""

import hashlib
import json
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services.model_registry import registration_view

router = APIRouter(prefix="/api/admin/model-connections", tags=["model-connections"])


class ConnectionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_name: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    api_key: SecretStr | None = None
    extra_params: dict | None = None


class CreateConnection(ConnectionFields):
    request_key: uuid.UUID


class ApiBaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_base: str = Field(min_length=1, max_length=512)


def connection_payload(body: ConnectionFields, *, create: bool) -> tuple[str | None, dict]:
    name = body.model_name.strip() if body.model_name is not None else None
    model = body.model.strip() if body.model is not None else None
    if (create or body.model_name is not None) and (not name or len(name) > 256 or any(c.isspace() for c in name)):
        raise HTTPException(400, "A model alias without whitespace is required (maximum 256 characters)")
    if (create or body.model is not None) and (not model or "/" not in model or any(c.isspace() for c in model)):
        raise HTTPException(400, "Use a LiteLLM provider/model identifier")
    params = dict(body.extra_params or {})
    if any(
        k in {"model", "model_name", "model_info", "api_base", "api_key", "api_version"}
        or "cost" in k.lower()
        or "pricing" in k.lower()
        for k in params
    ):
        raise HTTPException(400, "Use the named connection fields; base pricing is managed in LiteLLM")
    if model is not None:
        params["model"] = model
    if body.api_base:
        base = body.api_base.strip()
        try:
            url = urlsplit(base)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or any(c.isspace() for c in base)
            ):
                raise ValueError()
            _ = url.port
        except ValueError:
            raise HTTPException(400, "API base must be an HTTP(S) URL without credentials, query or fragment") from None
        params["api_base"] = base
    if body.api_version is not None:
        params["api_version"] = body.api_version.strip()
    if body.api_key and body.api_key.get_secret_value().strip():
        params["api_key"] = body.api_key.get_secret_value().strip()
    return name, params


async def connection_rows(litellm: LiteLLMClient) -> list[dict]:
    try:
        return await litellm.get_model_info()
    except Exception as exc:
        raise HTTPException(502, "LiteLLM connections are unavailable") from exc


def find_connection(rows: list[dict], model_id: str) -> dict | None:
    return next((r for r in rows if str((r.get("model_info") or {}).get("id")) == model_id), None)


async def writable_connection(model_id: str, db: AsyncSession, litellm: LiteLLMClient) -> dict:
    row = find_connection(await connection_rows(litellm), model_id)
    if not row:
        raise HTTPException(404, "Connection not found")
    if (row.get("model_info") or {}).get("db_model") is not True:
        raise HTTPException(409, "Configuration-managed connections are read only")
    if model_id in await managed_connections(db):
        raise HTTPException(409, "Manage this connection from its serving deployment")
    return row


@router.post("", status_code=201)
async def create_connection(
    body: CreateConnection,
    admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    name, params = connection_payload(body, create=True)
    model_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"portal-connection:{admin.user_id}:{body.request_key}"))
    fingerprint = hashlib.sha256(json.dumps({"name": name, "params": params}, sort_keys=True).encode()).hexdigest()

    def existing_result(row):
        if (row.get("model_info") or {}).get("portal_request_hash") != fingerprint:
            raise HTTPException(409, "This request key was already used with different connection settings")
        return {"connection": registration_view(row), "reused": True}

    existing = find_connection(await connection_rows(litellm), model_id)
    if existing:
        return existing_result(existing)
    info = {"id": model_id, "portal_request_hash": fingerprint, "portal_connection_type": "external"}
    try:
        await litellm.create_model(
            model_name=name,
            litellm_model=params["model"],
            api_base=params.get("api_base"),
            api_key=params.get("api_key"),
            extra_params={k: v for k, v in params.items() if k not in {"model", "api_base", "api_key"}},
            model_id=model_id,
            model_info=info,
        )
    except Exception as exc:
        # A timeout may occur after the upstream committed. Never retry with a new model ID.
        existing = find_connection(await connection_rows(litellm), model_id)
        if existing:
            return existing_result(existing)
        raise HTTPException(502, "LiteLLM registration failed; retry this request with the same key") from exc
    return {
        "connection": registration_view(
            {"model_name": name, "litellm_params": params, "model_info": {**info, "db_model": True}}
        ),
        "reused": False,
    }


@router.patch("/{model_id}")
async def update_connection(
    model_id: str,
    body: ConnectionFields,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await writable_connection(model_id, db, litellm)
    name, params = connection_payload(body, create=False)
    if not params and name is None:
        raise HTTPException(400, "No connection changes provided")
    try:
        await litellm.update_model_connection(model_id, params=params, model_name=name)
    except Exception as exc:
        raise HTTPException(
            502, "LiteLLM connection update failed; refresh to check the current state before retrying"
        ) from exc
    return {"id": model_id, "updated": True}


@router.delete("/{model_id}")
async def delete_connection(
    model_id: str,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not find_connection(await connection_rows(litellm), model_id):
        return {"id": model_id, "deleted": True}
    await writable_connection(model_id, db, litellm)
    try:
        await litellm.delete_model(model_id)
    except Exception as exc:
        if find_connection(await connection_rows(litellm), model_id):
            raise HTTPException(502, "LiteLLM connection deletion failed; retry after refreshing") from exc
    return {"id": model_id, "deleted": True}


@router.patch("/{model_id}/api-base")
async def update_api_base(
    model_id: str,
    body: ApiBaseUpdate,
    admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Change the live endpoint in place and retain it for future serving registration."""
    _, params = connection_payload(ConnectionFields(api_base=body.api_base), create=False)
    row = find_connection(await connection_rows(litellm), model_id)
    if not row:
        raise HTTPException(404, "Connection not found")
    if (row.get("model_info") or {}).get("db_model") is not True:
        raise HTTPException(409, "Configuration-managed connections are read only")

    owners = []
    for cls in (CustomModelDeployment, CustomExternalServing):
        owner = (
            await db.execute(select(cls).where(cls.litellm_model_id == model_id).with_for_update())
        ).scalar_one_or_none()
        if owner is not None:
            if isinstance(owner, CustomModelDeployment) and owner.validation_state in ("candidate", "retired"):
                raise HTTPException(409, "Manage this validation deployment from its recipe validation job")
            owners.append(owner)

    try:
        await litellm.update_model_connection(model_id, params=params)
    except Exception as exc:
        raise HTTPException(
            502, "LiteLLM endpoint update failed; refresh to check the current state before retrying"
        ) from exc

    for owner in owners:
        if isinstance(owner, CustomModelDeployment):
            owner.litellm_api_base = params["api_base"]
            owner.updated_by = admin.user_id
        else:
            owner.api_base = params["api_base"]
    await db.flush()
    return {"id": model_id, "api_base": params["api_base"], "updated": True}


async def managed_connections(db: AsyncSession) -> dict[str, dict]:
    owners = {}
    for cls, kind in [(CustomModelDeployment, "deployment"), (CustomExternalServing, "external")]:
        rows = (await db.execute(select(cls).where(cls.litellm_model_id.is_not(None)))).scalars().all()
        for row in rows:
            owners[str(row.litellm_model_id)] = {
                "kind": kind,
                "id": str(row.id),
                "cluster_id": str(row.cluster_id) if row.cluster_id else None,
                "namespace": row.namespace,
                "validation_state": getattr(row, "validation_state", None),
            }
    return owners


@router.get("")
async def list_connections(
    model_name: str | None = None,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        rows = await litellm.get_model_info()
    except Exception as exc:
        raise HTTPException(502, "LiteLLM connections are unavailable") from exc
    owners = await managed_connections(db)
    return {
        "connections": [
            registration_view(row, owners) for row in rows if model_name is None or row.get("model_name") == model_name
        ]
    }
