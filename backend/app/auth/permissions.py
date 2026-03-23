"""Authorization helpers for team-level and global permissions."""

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_user import CustomUser, GlobalRole


async def require_team_admin(user: CustomUser, team_id: str, db: AsyncSession) -> None:
    """Verify the user is an admin of the specified team or a super user."""
    if user.global_role == GlobalRole.SUPER_USER:
        return

    result = await db.execute(
        text('SELECT admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    admins = list(row["admins"] or []) if row else []
    if user.user_id not in admins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You are not an admin of team {team_id}",
        )
