"""Effective-budget resolution and serialization for member budget boosts."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_member_budget_boost import CustomMemberBudgetBoost


async def _team_default_member_budget(litellm_db: AsyncSession, team_id: str) -> float | None:
    """The team's default member max_budget (metadata.team_member_budget_id row)."""
    metadata = (
        await litellm_db.execute(
            text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
            {"team_id": team_id},
        )
    ).scalar()
    if not metadata or not isinstance(metadata, dict):
        return None
    budget_id = metadata.get("team_member_budget_id")
    if not budget_id:
        return None
    row = (
        await litellm_db.execute(
            text('SELECT max_budget FROM "LiteLLM_BudgetTable" WHERE budget_id = :budget_id'),
            {"budget_id": budget_id},
        )
    ).mappings().first()
    if row and row["max_budget"] is not None:
        return float(row["max_budget"])
    return None


async def resolve_effective_budget(
    litellm_db: AsyncSession, team_id: str, user_id: str
) -> float | None:
    """The member's current effective max_budget.

    Prefers the membership's dedicated budget row; falls back to the team's
    default member budget. None means unset/unlimited.
    """
    result = await litellm_db.execute(
        text(
            "SELECT b.max_budget "
            'FROM "LiteLLM_TeamMembership" m '
            'LEFT JOIN "LiteLLM_BudgetTable" b ON m.budget_id = b.budget_id '
            "WHERE m.team_id = :team_id AND m.user_id = :user_id"
        ),
        {"team_id": team_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if row and row["max_budget"] is not None:
        return float(row["max_budget"])
    return await _team_default_member_budget(litellm_db, team_id)


def serialize_boost(row: CustomMemberBudgetBoost) -> dict:
    return {
        "id": str(row.id),
        "team_id": row.team_id,
        "user_id": row.user_id,
        "original_max_budget": row.original_max_budget,
        "boost_max_budget": row.boost_max_budget,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
        "reverted_at": row.reverted_at.isoformat() if row.reverted_at else None,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
