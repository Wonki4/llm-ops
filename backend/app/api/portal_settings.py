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
        "hidden_teams_strict": json.loads(settings.get("hidden_teams_strict", "[]")),
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


@router.get("/default-team-rules")
async def get_default_team_rules(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get prefix-based default team rules (Super User only)."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'default_team_rules'")
    )
    raw = result.scalar()
    return {"rules": json.loads(raw) if raw else []}


@router.put("/default-team-rules")
async def update_default_team_rules(
    body: list[dict],
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update prefix-based default team rules (Super User only).

    Body format: [{"prefix": "X", "teams": ["team-a", "team-b"]}, ...]
    """
    await db.execute(
        text(
            "INSERT INTO custom_portal_settings (key, value, updated_by) "
            "VALUES ('default_team_rules', :value, :updated_by) "
            "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
        ),
        {"value": json.dumps(body), "updated_by": user.user_id},
    )
    await db.commit()
    return {"rules": body}


class HiddenTeamsBody(BaseModel):
    # Default hiding: gone from discovery only; members keep the team.
    hidden_teams: list[str] = []
    # Strict hiding: gone from members too — only super users see it.
    hidden_teams_strict: list[str] = []


@router.get("/hidden-teams")
async def get_hidden_teams(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get hidden team IDs per mode (Super User only)."""
    result = await db.execute(
        text(
            "SELECT key, value FROM custom_portal_settings "
            "WHERE key IN ('hidden_teams', 'hidden_teams_strict')"
        )
    )
    rows = {r["key"]: r["value"] for r in result.mappings()}
    return {
        "hidden_teams": json.loads(rows["hidden_teams"]) if rows.get("hidden_teams") else [],
        "hidden_teams_strict": (
            json.loads(rows["hidden_teams_strict"]) if rows.get("hidden_teams_strict") else []
        ),
    }


@router.put("/hidden-teams")
async def update_hidden_teams(
    body: HiddenTeamsBody,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update hidden team IDs per mode (Super User only). A team can be in
    only one mode — strict wins if sent in both lists."""
    strict = list(dict.fromkeys(body.hidden_teams_strict))
    base = [t for t in dict.fromkeys(body.hidden_teams) if t not in strict]
    for key, value in (("hidden_teams", base), ("hidden_teams_strict", strict)):
        await db.execute(
            text(
                "INSERT INTO custom_portal_settings (key, value, updated_by) "
                f"VALUES ('{key}', :value, :updated_by) "
                "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
            ),
            {"value": json.dumps(value), "updated_by": user.user_id},
        )
    await db.commit()
    return {"hidden_teams": base, "hidden_teams_strict": strict}
