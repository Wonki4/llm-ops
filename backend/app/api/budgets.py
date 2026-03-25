"""Budget management endpoints (Super User only)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("")
async def list_budgets(
    page: int = 1,
    page_size: int = 50,
    search_id: str | None = None,
    search_amount: float | None = None,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all budgets with pagination and linked entity counts."""
    # Build WHERE conditions
    conditions = []
    search_params: dict = {}
    if search_id:
        conditions.append("b.budget_id ILIKE :search_id")
        search_params["search_id"] = f"%{search_id}%"
    if search_amount is not None:
        conditions.append("b.max_budget = :search_amount")
        search_params["search_amount"] = search_amount

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Count total
    count_query = f'SELECT COUNT(*) FROM "LiteLLM_BudgetTable" b {where_clause}'
    total = (await db.execute(text(count_query), search_params)).scalar() or 0

    # Fetch budgets with linked counts via subqueries
    offset = (page - 1) * page_size
    query = text(f"""
        SELECT
            b.budget_id,
            b.max_budget,
            b.soft_budget,
            b.budget_duration,
            b.budget_reset_at,
            b.tpm_limit,
            b.rpm_limit,
            b.created_at,
            b.created_by,
            b.updated_at,
            b.updated_by,
            (SELECT COUNT(*) FROM "LiteLLM_TeamMembership" tm WHERE tm.budget_id = b.budget_id) AS team_membership_count,
            (SELECT COUNT(*) FROM "LiteLLM_VerificationToken" vt WHERE vt.budget_id = b.budget_id) AS key_count,
            (SELECT COUNT(*) FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = b.budget_id) AS org_count
        FROM "LiteLLM_BudgetTable" b
        {where_clause}
        ORDER BY b.created_at DESC
        OFFSET :offset LIMIT :limit
    """)

    params = {**search_params, "offset": offset, "limit": min(page_size, 100)}

    result = await db.execute(query, params)
    budgets = [
        {
            "budget_id": r["budget_id"],
            "max_budget": r["max_budget"],
            "soft_budget": r["soft_budget"],
            "budget_duration": r["budget_duration"],
            "budget_reset_at": r["budget_reset_at"].isoformat() if r["budget_reset_at"] else None,
            "tpm_limit": r["tpm_limit"],
            "rpm_limit": r["rpm_limit"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "created_by": r["created_by"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "updated_by": r["updated_by"],
            "team_membership_count": r["team_membership_count"],
            "key_count": r["key_count"],
            "org_count": r["org_count"],
        }
        for r in result.mappings()
    ]

    return {"budgets": budgets, "total": total, "page": page, "page_size": page_size}


@router.get("/{budget_id}/details")
async def get_budget_details(
    budget_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get detailed linked entities for a specific budget."""
    # Team memberships
    tm_result = await db.execute(
        text(
            'SELECT tm.user_id, tm.team_id, tm.spend, t.team_alias '
            'FROM "LiteLLM_TeamMembership" tm '
            'LEFT JOIN "LiteLLM_TeamTable" t ON tm.team_id = t.team_id '
            'WHERE tm.budget_id = :budget_id'
        ),
        {"budget_id": budget_id},
    )
    team_memberships = [
        {
            "user_id": r["user_id"],
            "team_id": r["team_id"],
            "team_alias": r["team_alias"],
            "spend": float(r["spend"]),
        }
        for r in tm_result.mappings()
    ]

    # Keys
    key_result = await db.execute(
        text(
            'SELECT token, key_alias, key_name, user_id, team_id, spend '
            'FROM "LiteLLM_VerificationToken" '
            'WHERE budget_id = :budget_id '
            'ORDER BY spend DESC LIMIT 50'
        ),
        {"budget_id": budget_id},
    )
    keys = [
        {
            "token": r["token"][:8] + "...",
            "key_alias": r["key_alias"],
            "key_name": r["key_name"],
            "user_id": r["user_id"],
            "team_id": r["team_id"],
            "spend": float(r["spend"]),
        }
        for r in key_result.mappings()
    ]

    # Organizations
    org_result = await db.execute(
        text(
            'SELECT organization_id, organization_alias '
            'FROM "LiteLLM_OrganizationTable" '
            'WHERE budget_id = :budget_id'
        ),
        {"budget_id": budget_id},
    )
    orgs = [
        {"organization_id": r["organization_id"], "organization_alias": r["organization_alias"]}
        for r in org_result.mappings()
    ]

    return {
        "team_memberships": team_memberships,
        "keys": keys,
        "organizations": orgs,
    }
