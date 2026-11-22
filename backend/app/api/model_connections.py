"""Administrator projection of live LiteLLM connections. Credentials never leave this API."""

from fastapi import APIRouter, Depends, HTTPException
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
