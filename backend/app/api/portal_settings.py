"""Portal settings endpoints (Super User only)."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    default_tpm_limit: int | None = None
    default_rpm_limit: int | None = None
    default_team_id: str | None = None


@router.get("")
async def get_settings(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get portal settings. All authenticated users can read."""
    result = await db.execute(text('SELECT key, value FROM custom_portal_settings'))
    settings = {r["key"]: r["value"] for r in result.mappings()}
    return {
        "default_tpm_limit": int(settings.get("default_tpm_limit", "100000")),
        "default_rpm_limit": int(settings.get("default_rpm_limit", "1000")),
        "default_team_id": settings.get("default_team_id", ""),
        "hidden_teams": json.loads(settings.get("hidden_teams", "[]")),
    }


@router.put("")
async def update_settings(
    body: UpdateSettingsRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update portal settings (Super User only)."""
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is not None:
            await db.execute(
                text(
                    "INSERT INTO custom_portal_settings (key, value, updated_by) "
                    "VALUES (:key, :value, :updated_by) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
                ),
                {"key": key, "value": str(value), "updated_by": user.user_id},
            )
    await db.commit()
    return await get_settings(user=user, db=db)


@router.get("/hidden-teams")
async def get_hidden_teams(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get hidden team IDs (Super User only)."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'hidden_teams'")
    )
    raw = result.scalar()
    return {"hidden_teams": json.loads(raw) if raw else []}


@router.put("/hidden-teams")
async def update_hidden_teams(
    body: list[str],
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update hidden team IDs (Super User only)."""
    await db.execute(
        text(
            "INSERT INTO custom_portal_settings (key, value, updated_by) "
            "VALUES ('hidden_teams', :value, :updated_by) "
            "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
        ),
        {"value": json.dumps(body), "updated_by": user.user_id},
    )
    await db.commit()
    return {"hidden_teams": body}
