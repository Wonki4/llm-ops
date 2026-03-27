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
    orphans_only: bool = False,
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
    if orphans_only:
        conditions.append("""
            NOT EXISTS (SELECT 1 FROM "LiteLLM_TeamMembership" tm WHERE tm.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_VerificationToken" vt WHERE vt.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_ProjectTable" p WHERE p.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_EndUserTable" eu WHERE eu.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_TagTable" t WHERE t.budget_id = b.budget_id)
            AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationMembership" om WHERE om.budget_id = b.budget_id)
        """)

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
            (SELECT COUNT(*) FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = b.budget_id) AS org_count,
            (SELECT COUNT(*) FROM "LiteLLM_ProjectTable" p WHERE p.budget_id = b.budget_id) AS project_count,
            (SELECT COUNT(*) FROM "LiteLLM_EndUserTable" eu WHERE eu.budget_id = b.budget_id) AS end_user_count,
            (SELECT COUNT(*) FROM "LiteLLM_TagTable" t WHERE t.budget_id = b.budget_id) AS tag_count,
            (SELECT COUNT(*) FROM "LiteLLM_OrganizationMembership" om WHERE om.budget_id = b.budget_id) AS org_membership_count
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
            "project_count": r["project_count"],
            "end_user_count": r["end_user_count"],
            "tag_count": r["tag_count"],
            "org_membership_count": r["org_membership_count"],
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

    # Projects
    prj_result = await db.execute(
        text('SELECT project_id, project_name FROM "LiteLLM_ProjectTable" WHERE budget_id = :budget_id'),
        {"budget_id": budget_id},
    )
    projects = [{"project_id": r["project_id"], "project_name": r["project_name"]} for r in prj_result.mappings()]

    # End users
    eu_result = await db.execute(
        text('SELECT user_id, alias, spend FROM "LiteLLM_EndUserTable" WHERE budget_id = :budget_id'),
        {"budget_id": budget_id},
    )
    end_users = [{"user_id": r["user_id"], "alias": r["alias"], "spend": float(r["spend"])} for r in eu_result.mappings()]

    # Tags
    tag_result = await db.execute(
        text('SELECT tag_name FROM "LiteLLM_TagTable" WHERE budget_id = :budget_id'),
        {"budget_id": budget_id},
    )
    tags = [r["tag_name"] for r in tag_result.mappings()]

    # Organization memberships
    om_result = await db.execute(
        text(
            'SELECT om.user_id, om.organization_id, om.spend '
            'FROM "LiteLLM_OrganizationMembership" om '
            'WHERE om.budget_id = :budget_id'
        ),
        {"budget_id": budget_id},
    )
    org_memberships = [
        {"user_id": r["user_id"], "organization_id": r["organization_id"], "spend": float(r["spend"] or 0)}
        for r in om_result.mappings()
    ]

    return {
        "team_memberships": team_memberships,
        "keys": keys,
        "organizations": orgs,
        "projects": projects,
        "end_users": end_users,
        "tags": tags,
        "org_memberships": org_memberships,
    }


@router.get("/orphans")
async def list_orphan_budgets(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List budgets not linked to any entity."""
    result = await db.execute(text("""
        SELECT b.budget_id, b.max_budget, b.created_at
        FROM "LiteLLM_BudgetTable" b
        WHERE NOT EXISTS (SELECT 1 FROM "LiteLLM_TeamMembership" tm WHERE tm.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_VerificationToken" vt WHERE vt.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_ProjectTable" p WHERE p.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_EndUserTable" eu WHERE eu.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_TagTable" t WHERE t.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationMembership" om WHERE om.budget_id = b.budget_id)
        ORDER BY b.created_at DESC
    """))
    orphans = [
        {
            "budget_id": r["budget_id"],
            "max_budget": r["max_budget"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in result.mappings()
    ]
    return {"orphans": orphans, "count": len(orphans)}


@router.delete("/orphans")
async def delete_all_orphan_budgets(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete all orphan budgets (not linked to anything)."""
    result = await db.execute(text("""
        DELETE FROM "LiteLLM_BudgetTable" b
        WHERE NOT EXISTS (SELECT 1 FROM "LiteLLM_TeamMembership" tm WHERE tm.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_VerificationToken" vt WHERE vt.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_ProjectTable" p WHERE p.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_EndUserTable" eu WHERE eu.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_TagTable" t WHERE t.budget_id = b.budget_id)
          AND NOT EXISTS (SELECT 1 FROM "LiteLLM_OrganizationMembership" om WHERE om.budget_id = b.budget_id)
    """))
    await db.commit()
    return {"deleted": result.rowcount}


@router.delete("/{budget_id}")
async def delete_budget(
    budget_id: str,
    force: bool = False,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a single budget by ID. Rejects if linked unless force=true."""
    from fastapi import HTTPException

    # Check linked entities
    linked_result = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM "LiteLLM_TeamMembership" tm WHERE tm.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_VerificationToken" vt WHERE vt.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_OrganizationTable" o WHERE o.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_ProjectTable" p WHERE p.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_EndUserTable" eu WHERE eu.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_TagTable" t WHERE t.budget_id = :bid) +
            (SELECT COUNT(*) FROM "LiteLLM_OrganizationMembership" om WHERE om.budget_id = :bid)
        AS total_linked
    """), {"bid": budget_id})
    total_linked = linked_result.scalar() or 0

    if total_linked > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=f"이 예산에 {total_linked}개의 연결된 항목이 있습니다. 연결을 먼저 해제하세요.",
        )

    result = await db.execute(
        text('DELETE FROM "LiteLLM_BudgetTable" WHERE budget_id = :budget_id'),
        {"budget_id": budget_id},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Budget not found")
    return {"deleted": True, "budget_id": budget_id}
