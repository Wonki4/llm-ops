"""Effective-budget resolution and serialization for member budget boosts."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.litellm import LiteLLMClient
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


async def _active_boost_exists(db, team_id: str, user_id: str) -> bool:
    row = (
        await db.execute(
            select(CustomMemberBudgetBoost).where(
                CustomMemberBudgetBoost.team_id == team_id,
                CustomMemberBudgetBoost.user_id == user_id,
                CustomMemberBudgetBoost.status == "active",
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def apply_member_budget_boost(
    db,
    litellm: LiteLLMClient,
    litellm_db,
    *,
    team_id: str,
    user_id: str,
    boost_max_budget: float,
    expires_at: datetime,
    created_by: str | None,
) -> CustomMemberBudgetBoost:
    """Snapshot the member's effective budget, reserve the boost row, then apply
    the raised budget via LiteLLM. Reserve-before-apply so a race yields 409 and
    a LiteLLM failure rolls the reserved row back (get_db rolls back on the
    raised HTTPException). Raises 400 (non-positive / non-future / no revertable
    budget), 409 (active boost exists), 502 (LiteLLM failure)."""
    if boost_max_budget <= 0:
        raise HTTPException(status_code=400, detail="Boosted budget must be positive")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Boost end time must be in the future")
    if await _active_boost_exists(db, team_id, user_id):
        raise HTTPException(status_code=409, detail="An active boost already exists for this member")

    original = await resolve_effective_budget(litellm_db, team_id, user_id)
    if original is None:
        raise HTTPException(
            status_code=400,
            detail="Member has no budget limit to boost — set a budget first",
        )

    boost = CustomMemberBudgetBoost(
        id=uuid.uuid4(),
        team_id=team_id,
        user_id=user_id,
        original_max_budget=original,
        boost_max_budget=boost_max_budget,
        expires_at=expires_at,
        status="active",
        created_by=created_by,
    )
    db.add(boost)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An active boost already exists for this member")

    try:
        await litellm.update_team_member(team_id, user_id, max_budget_in_team=boost_max_budget)
    except Exception as e:  # noqa: BLE001 — surfaced as 502; get_db rolls back the reserved row
        raise HTTPException(status_code=502, detail=f"Failed to apply boosted budget: {e}")

    await db.refresh(boost)
    return boost
