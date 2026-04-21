"""Team management endpoints - proxies to LiteLLM + adds custom logic."""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.permissions import require_team_admin
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db

import json

MEMBER_PREVIEW_LIMIT = 20
router = APIRouter(prefix="/api/teams", tags=["teams"])


async def _get_membership_duration(db: AsyncSession, team_id: str) -> str | None:
    """Get membership duration setting for a team."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = :key"),
        {"key": f"team:{team_id}:membership_duration"},
    )
    return result.scalar() or None


async def _get_team_default_limits(db: AsyncSession, team_id: str) -> dict[str, int | None]:
    """Get team-scoped default TPM/RPM limits from portal settings. None when unset."""
    result = await db.execute(
        text(
            "SELECT key, value FROM custom_portal_settings "
            "WHERE key IN (:tpm_key, :rpm_key)"
        ),
        {
            "tpm_key": f"team:{team_id}:default_tpm_limit",
            "rpm_key": f"team:{team_id}:default_rpm_limit",
        },
    )
    rows = {r["key"]: r["value"] for r in result.mappings()}
    tpm_raw = rows.get(f"team:{team_id}:default_tpm_limit")
    rpm_raw = rows.get(f"team:{team_id}:default_rpm_limit")
    return {
        "default_tpm_limit": int(tpm_raw) if tpm_raw else None,
        "default_rpm_limit": int(rpm_raw) if rpm_raw else None,
    }


def _parse_duration(duration_str: str) -> "timedelta | None":
    """Parse duration string like '30d', '90d', '365d' to timedelta."""
    from datetime import timedelta
    import re
    match = re.match(r"^(\d+)([dhm])$", duration_str.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == "d":
        return timedelta(days=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "m":
        return timedelta(days=value * 30)
    return None


async def _get_default_member_budget(litellm_db: AsyncSession, team_id: str) -> float | None:
    """Get default member budget from TeamTable metadata -> BudgetTable."""
    result = await litellm_db.execute(
        text("SELECT metadata FROM \"LiteLLM_TeamTable\" WHERE team_id = :team_id"),
        {"team_id": team_id},
    )
    metadata = result.scalar()
    if not metadata or not isinstance(metadata, dict):
        return None
    budget_id = metadata.get("team_member_budget_id")
    if not budget_id:
        return None
    budget_result = await litellm_db.execute(
        text("SELECT max_budget FROM \"LiteLLM_BudgetTable\" WHERE budget_id = :budget_id"),
        {"budget_id": budget_id},
    )
    return budget_result.scalar()


async def _get_hidden_teams(db: AsyncSession) -> set[str]:
    """Get hidden team IDs from portal settings."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'hidden_teams'")
    )
    raw = result.scalar()
    return set(json.loads(raw)) if raw else set()

_TEAM_COLUMNS = (
    "t.team_id, t.team_alias, t.max_budget, t.spend, "
    "t.budget_duration, t.budget_reset_at, t.models, "
    "t.members, t.admins"
)


def _row_to_team(row: Mapping[str, Any], preview_limit: int = MEMBER_PREVIEW_LIMIT) -> dict:
    """Convert a LiteLLM_TeamTable row to a lightweight team dict."""
    all_members: list[str] = list(row["members"] or [])
    all_admins: list[str] = list(row["admins"] or [])
    return {
        "team_id": row["team_id"],
        "team_alias": row["team_alias"],
        "max_budget": row["max_budget"],
        "spend": float(row["spend"]),
        "budget_duration": row["budget_duration"],
        "budget_reset_at": (row["budget_reset_at"].isoformat() if row["budget_reset_at"] else None),
        "models": list(row["models"] or []),
        "members": all_members[:preview_limit],
        "admins": all_admins[:preview_limit],
        "member_count": len(all_members),
        "admin_count": len(all_admins),
    }


@router.get("")
async def list_my_teams(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List teams the current user belongs to (direct DB query)."""
    result = await litellm_db.execute(
        text(
            f"SELECT {_TEAM_COLUMNS} "
            'FROM "LiteLLM_TeamTable" t '
            'WHERE t.team_id = ANY('
            '    SELECT unnest(u.teams) FROM "LiteLLM_UserTable" u '
            '    WHERE u.user_id = :user_id'
            ') '
            'ORDER BY t.team_alias'
        ),
        {"user_id": user.user_id},
    )
    teams = [_row_to_team(r) for r in result.mappings()]

    # Hide teams for non-super users
    if user.global_role != GlobalRole.SUPER_USER:
        hidden = await _get_hidden_teams(db)
        teams = [t for t in teams if t["team_id"] not in hidden]

    return {"teams": teams}


@router.get("/discover")
async def discover_teams(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List all available teams for discovery (direct DB query)."""
    # Fetch all teams
    all_result = await litellm_db.execute(
        text(f'SELECT {_TEAM_COLUMNS} FROM "LiteLLM_TeamTable" t ORDER BY t.team_alias'),
    )
    # Fetch user's current team memberships
    membership_result = await litellm_db.execute(
        text(
            'SELECT team_id FROM "LiteLLM_TeamMembership" WHERE user_id = :user_id '
            "UNION "
            "SELECT UNNEST(COALESCE(teams, ARRAY[]::text[])) AS team_id "
            'FROM "LiteLLM_UserTable" WHERE user_id = :user_id'
        ),
        {"user_id": user.user_id},
    )
    user_team_ids = {r["team_id"] for r in membership_result.mappings()}

    # Fetch user's pending join requests
    pending_result = await db.execute(
        text(
            "SELECT team_id FROM custom_team_join_requests "
            "WHERE requester_id = :user_id AND status = 'pending' AND request_type = 'join'"
        ),
        {"user_id": user.user_id},
    )
    pending_team_ids = {r["team_id"] for r in pending_result.mappings()}

    # Get hidden teams for non-super users
    hidden = set()
    if user.global_role != GlobalRole.SUPER_USER:
        hidden = await _get_hidden_teams(db)

    teams = []
    for row in all_result.mappings():
        team_id = row["team_id"]
        if team_id in hidden:
            continue
        all_members: list[str] = list(row["members"] or [])
        all_admins: list[str] = list(row["admins"] or [])
        is_member = (
            team_id in user_team_ids
            or user.user_id in all_members
            or user.user_id in all_admins
        )
        teams.append({
            "team_id": team_id,
            "team_alias": row["team_alias"],
            "models": list(row["models"] or []),
            "admins": all_admins,
            "is_member": is_member,
            "has_pending_request": team_id in pending_team_ids,
        })

    return {"teams": teams}


@router.get("/{team_id}")
async def get_team_detail(
    team_id: str,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: ARG001 — kept for consistency
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Get detailed team info including budget, keys, models.

    Optimised for large teams (30K+ members, 90K+ keys):
    - Team metadata AND user keys are read directly from the DB
      (avoids LiteLLM /team/info fetching every key in the team).
    - Member/admin lists are truncated to first N for preview; total counts
      are returned separately.
    """
    # Direct DB read for team row (single row, fast even with 30K member IDs)
    result = await litellm_db.execute(
        text(
            "SELECT team_id, team_alias, max_budget, spend, budget_duration, "
            "       budget_reset_at, models, members, admins "
            'FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'
        ),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    all_members: list[str] = list(row["members"] or [])
    all_admins: list[str] = list(row["admins"] or [])

    # Fetch ONLY the current user's keys directly from DB (indexed on user_id + team_id)
    keys_result = await litellm_db.execute(
        text(
            "SELECT token, key_name, key_alias, team_id, user_id, "
            "       spend, max_budget, budget_duration, budget_reset_at, "
            "       models, expires, created_at, metadata, tpm_limit, rpm_limit "
            'FROM "LiteLLM_VerificationToken" '
            "WHERE user_id = :user_id AND team_id = :team_id "
            "ORDER BY created_at DESC"
        ),
        {"user_id": user.user_id, "team_id": team_id},
    )
    my_keys = [
        {
            "token": k["token"],
            "key_name": k["key_name"],
            "key_alias": (k["metadata"] or {}).get("display_alias", ""),
            "team_id": k["team_id"],
            "user_id": k["user_id"],
            "spend": float(k["spend"]),
            "max_budget": k["max_budget"],
            "budget_duration": k["budget_duration"],
            "budget_reset_at": (k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None),
            "models": list(k["models"] or []),
            "expires": k["expires"].isoformat() if k["expires"] else None,
            "created_at": k["created_at"].isoformat() if k["created_at"] else None,
            "tpm_limit": k["tpm_limit"],
            "rpm_limit": k["rpm_limit"],
        }
        for k in keys_result.mappings()
    ]

    # Fetch user's team membership budget (spend + max_budget/duration/reset from BudgetTable)
    membership_result = await litellm_db.execute(
        text(
            "SELECT m.spend, b.max_budget, b.budget_duration, b.budget_reset_at "
            'FROM "LiteLLM_TeamMembership" m '
            'LEFT JOIN "LiteLLM_BudgetTable" b ON m.budget_id = b.budget_id '
            "WHERE m.user_id = :user_id AND m.team_id = :team_id"
        ),
        {"user_id": user.user_id, "team_id": team_id},
    )
    membership_row = membership_result.mappings().first()

    return {
        "team": {
            "team_id": row["team_id"],
            "team_alias": row["team_alias"],
            "max_budget": row["max_budget"],
            "spend": float(row["spend"]),
            "budget_duration": row["budget_duration"],
            "budget_reset_at": (row["budget_reset_at"].isoformat() if row["budget_reset_at"] else None),
            "models": list(row["models"] or []),
            "members": all_members[:MEMBER_PREVIEW_LIMIT],
            "admins": all_admins[:MEMBER_PREVIEW_LIMIT],
            "member_count": len(all_members),
            "admin_count": len(all_admins),
        },
        "my_keys": my_keys,
        "default_member_budget": await _get_default_member_budget(litellm_db, team_id),
        "membership_duration": await _get_membership_duration(db, team_id),
        **(await _get_team_default_limits(db, team_id)),
        "is_admin": user.global_role == GlobalRole.SUPER_USER or user.user_id in all_admins,
        "my_membership": {
            "spend": float(membership_row["spend"]) if membership_row else 0,
            "max_budget": membership_row["max_budget"] if membership_row else None,
            "budget_duration": membership_row["budget_duration"] if membership_row else None,
            "budget_reset_at": (membership_row["budget_reset_at"].isoformat() if membership_row and membership_row["budget_reset_at"] else None),
        },
    }


_MEMBER_SORT_COLUMNS = {
    "user_id": "tm.user_id",
    "spend": "tm.spend",
    "budget": "b.max_budget",
}


@router.get("/{team_id}/members")
async def list_team_members(
    team_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    sort_by: str = "user_id",
    sort_dir: str = "asc",
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List team members with their key/budget info (admin only, paginated).

    sort_by: user_id | spend | budget (default: user_id)
    sort_dir: asc | desc (default: asc)
    """
    if sort_by not in _MEMBER_SORT_COLUMNS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    sort_column = _MEMBER_SORT_COLUMNS[sort_by]
    # Push NULL budgets last when sorting by budget so "무제한/미설정" doesn't dominate.
    nulls_clause = " NULLS LAST" if sort_by == "budget" else ""

    # 1. Get team admins for role check
    result = await litellm_db.execute(
        text('SELECT admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    all_admins_set = set(row["admins"] or [])

    # Admin or super user check
    if user.global_role != GlobalRole.SUPER_USER and user.user_id not in all_admins_set:
        raise HTTPException(status_code=403, detail="Admin access required")

    # 2. Get members from TeamMembership table (source of truth)
    search_condition = ""
    search_params: dict = {"team_id": team_id}
    if search:
        search_condition = "AND tm.user_id ILIKE :search"
        search_params["search"] = f"%{search}%"

    count_result = await litellm_db.execute(
        text(f'SELECT COUNT(*) FROM "LiteLLM_TeamMembership" tm WHERE tm.team_id = :team_id {search_condition}'),
        search_params,
    )
    total = count_result.scalar() or 0

    # 3. Paginate via DB
    offset = (page - 1) * page_size
    order_clause = f"{sort_column} {direction}{nulls_clause}, tm.user_id ASC"
    membership_result = await litellm_db.execute(
        text(f"""
            SELECT tm.user_id, tm.spend AS membership_spend,
                   b.max_budget AS membership_max_budget
            FROM "LiteLLM_TeamMembership" tm
            LEFT JOIN "LiteLLM_BudgetTable" b ON tm.budget_id = b.budget_id
            WHERE tm.team_id = :team_id {search_condition}
            ORDER BY {order_clause}
            OFFSET :offset LIMIT :limit
        """),
        {**search_params, "offset": offset, "limit": page_size},
    )
    paged_rows = list(membership_result.mappings())
    paged_ids = [r["user_id"] for r in paged_rows]
    membership_budget = {
        r["user_id"]: {
            "spend": float(r["membership_spend"] or 0),
            "max_budget": float(r["membership_max_budget"]) if r["membership_max_budget"] is not None else None,
        }
        for r in paged_rows
    }

    if not paged_ids:
        return {"members": [], "total": total, "page": page, "page_size": page_size}

    # 4. Get keys for paginated members
    keys_result = await litellm_db.execute(
        text(
            "SELECT user_id, token, key_alias, key_name, spend, max_budget, "
            "       budget_duration, budget_reset_at, models, created_at "
            'FROM "LiteLLM_VerificationToken" '
            "WHERE team_id = :team_id AND user_id = ANY(:member_ids) "
            "ORDER BY user_id, created_at DESC"
        ),
        {"team_id": team_id, "member_ids": paged_ids},
    )

    # 5. Group keys by user_id
    keys_by_user: dict[str, list[dict]] = {uid: [] for uid in paged_ids}
    for k in keys_result.mappings():
        keys_by_user[k["user_id"]].append(
            {
                "token": k["token"],
                "key_alias": k["key_alias"],
                "key_name": k["key_name"],
                "spend": float(k["spend"]),
                "max_budget": float(k["max_budget"]) if k["max_budget"] is not None else None,
                "budget_duration": k["budget_duration"],
                "budget_reset_at": (k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None),
                "models": list(k["models"] or []),
                "created_at": k["created_at"].isoformat() if k["created_at"] else None,
            }
        )

    # 6. Get membership expiry info from portal DB
    expiry_result = await db.execute(
        text(
            "SELECT user_id, expires_at, status FROM custom_team_membership "
            "WHERE team_id = :team_id AND user_id = ANY(:member_ids)"
        ),
        {"team_id": team_id, "member_ids": paged_ids},
    )
    expiry_map = {
        r["user_id"]: {
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "status": r["status"],
        }
        for r in expiry_result.mappings()
    }

    # 7. Build member objects
    members = []
    for uid in paged_ids:
        user_keys = keys_by_user.get(uid, [])
        budget = membership_budget.get(uid, {"spend": 0, "max_budget": None})
        expiry = expiry_map.get(uid)
        members.append(
            {
                "user_id": uid,
                "is_admin": uid in all_admins_set,
                "key_count": len(user_keys),
                "total_spend": budget["spend"],
                "total_max_budget": budget["max_budget"],
                "expires_at": expiry["expires_at"] if expiry else None,
                "expiry_status": expiry["status"] if expiry else None,
                "keys": user_keys,
            }
        )

    return {"members": members, "total": total, "page": page, "page_size": page_size}


class ChangeRoleRequest(BaseModel):
    user_id: str
    role: str  # "admin" or "member"


@router.post("/{team_id}/members/role")
async def change_member_role(
    team_id: str,
    body: ChangeRoleRequest,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Change a team member's role (admin <-> member). Requires team admin or super user."""
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")

    # Get team
    result = await litellm_db.execute(
        text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    all_members = list(row["members"] or [])
    all_admins = list(row["admins"] or [])

    # Permission check: must be team admin or super user
    if user.global_role != GlobalRole.SUPER_USER and user.user_id not in all_admins:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Target must be a member of the team (check TeamMembership table too)
    is_team_member = body.user_id in all_members or body.user_id in all_admins
    if not is_team_member:
        membership_check = await litellm_db.execute(
            text('SELECT 1 FROM "LiteLLM_TeamMembership" WHERE user_id = :user_id AND team_id = :team_id'),
            {"user_id": body.user_id, "team_id": team_id},
        )
        if membership_check.scalar() is None:
            raise HTTPException(status_code=404, detail="User is not a member of this team")

    if body.role == "admin":
        if body.user_id in all_admins:
            return {"status": "unchanged", "message": "User is already an admin"}
        all_admins.append(body.user_id)
    else:
        if body.user_id not in all_admins:
            return {"status": "unchanged", "message": "User is already a member"}
        # Prevent removing the last admin (super_user can override)
        remaining_admins = [a for a in all_admins if a != body.user_id]
        if len(remaining_admins) == 0 and user.global_role != GlobalRole.SUPER_USER:
            raise HTTPException(status_code=400, detail="Cannot remove the last admin")
        all_admins.remove(body.user_id)
        # Ensure user stays in members
        if body.user_id not in all_members:
            all_members.append(body.user_id)

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamTable" SET admins = :admins, members = :members '
            "WHERE team_id = :team_id"
        ),
        {"admins": all_admins, "members": all_members, "team_id": team_id},
    )
    await litellm_db.commit()

    return {"status": "changed", "user_id": body.user_id, "new_role": body.role}


class ChangeBudgetRequest(BaseModel):
    max_budget: float


@router.put("/{team_id}/members/{member_id}/budget")
async def change_member_budget(
    team_id: str,
    member_id: str,
    body: ChangeBudgetRequest,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Change a team member's budget. Requires team admin or super user."""
    await require_team_admin(user, team_id, litellm_db)

    # Find or create budget with the target max_budget
    existing_budget = await litellm_db.execute(
        text(
            'SELECT budget_id FROM "LiteLLM_BudgetTable" '
            "WHERE max_budget = :max_budget LIMIT 1"
        ),
        {"max_budget": body.max_budget},
    )
    existing_row = existing_budget.mappings().first()

    if existing_row:
        target_budget_id = existing_row["budget_id"]
    else:
        import uuid as _uuid
        target_budget_id = str(_uuid.uuid4())
        await litellm_db.execute(
            text(
                'INSERT INTO "LiteLLM_BudgetTable" (budget_id, max_budget, created_by, updated_by) '
                "VALUES (:budget_id, :max_budget, :created_by, :updated_by)"
            ),
            {
                "budget_id": target_budget_id,
                "max_budget": body.max_budget,
                "created_by": user.user_id,
                "updated_by": user.user_id,
            },
        )

    # Point the member's membership to the target budget
    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamMembership" SET budget_id = :budget_id '
            "WHERE user_id = :user_id AND team_id = :team_id"
        ),
        {"budget_id": target_budget_id, "user_id": member_id, "team_id": team_id},
    )
    await litellm_db.commit()

    return {"status": "changed", "user_id": member_id, "max_budget": body.max_budget}


class SetMemberExpiryRequest(BaseModel):
    expires_at: str | None = None  # ISO datetime string, null to remove


@router.put("/{team_id}/members/{member_id}/expiry")
async def set_member_expiry(
    team_id: str,
    member_id: str,
    body: SetMemberExpiryRequest,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Set or remove membership expiry for a team member. Requires team admin."""
    await require_team_admin(user, team_id, litellm_db)

    if body.expires_at:
        from datetime import datetime as _dt
        expires_at = _dt.fromisoformat(body.expires_at)
        await db.execute(
            text(
                "INSERT INTO custom_team_membership (id, user_id, team_id, expires_at, status) "
                "VALUES (gen_random_uuid(), :user_id, :team_id, :expires_at, 'active') "
                "ON CONFLICT (user_id, team_id) DO UPDATE SET expires_at = :expires_at, status = 'active'"
            ),
            {"user_id": member_id, "team_id": team_id, "expires_at": expires_at},
        )
        return {"status": "set", "user_id": member_id, "expires_at": expires_at.isoformat()}
    else:
        await db.execute(
            text("DELETE FROM custom_team_membership WHERE user_id = :user_id AND team_id = :team_id"),
            {"user_id": member_id, "team_id": team_id},
        )
        return {"status": "removed", "user_id": member_id, "expires_at": None}


@router.delete("/{team_id}/members/{member_id}")
async def remove_team_member(
    team_id: str,
    member_id: str,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Remove a member from the team. Requires team admin or super user."""
    # Get team
    result = await litellm_db.execute(
        text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    all_members = list(row["members"] or [])
    all_admins = list(row["admins"] or [])

    # Permission check
    if user.global_role != GlobalRole.SUPER_USER and user.user_id not in all_admins:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Cannot remove yourself
    if member_id == user.user_id:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")

    # Cannot remove the last admin
    if member_id in all_admins and len(all_admins) <= 1:
        raise HTTPException(status_code=400, detail="마지막 관리자는 삭제할 수 없습니다.")

    # Remove from members and admins arrays
    if member_id in all_members:
        all_members.remove(member_id)
    if member_id in all_admins:
        all_admins.remove(member_id)

    # Update TeamTable
    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamTable" SET admins = :admins, members = :members '
            "WHERE team_id = :team_id"
        ),
        {"admins": all_admins, "members": all_members, "team_id": team_id},
    )

    # Remove from TeamMembership
    await litellm_db.execute(
        text('DELETE FROM "LiteLLM_TeamMembership" WHERE team_id = :team_id AND user_id = :user_id'),
        {"team_id": team_id, "user_id": member_id},
    )

    # Remove team from user's teams array
    user_result = await litellm_db.execute(
        text('SELECT teams FROM "LiteLLM_UserTable" WHERE user_id = :user_id'),
        {"user_id": member_id},
    )
    user_row = user_result.mappings().first()
    if user_row and user_row["teams"]:
        user_teams = [t for t in user_row["teams"] if t != team_id]
        await litellm_db.execute(
            text('UPDATE "LiteLLM_UserTable" SET teams = :teams WHERE user_id = :user_id'),
            {"teams": user_teams, "user_id": member_id},
        )

    await litellm_db.commit()
    return {"status": "removed", "user_id": member_id, "team_id": team_id}


class UpdateTeamSettingsRequest(BaseModel):
    default_member_budget: float | None = None
    membership_duration: str | None = None  # e.g. "90d", "180d", "365d"
    default_tpm_limit: int | None = None
    default_rpm_limit: int | None = None


@router.put("/{team_id}/settings")
async def update_team_settings(
    team_id: str,
    body: UpdateTeamSettingsRequest,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update team settings. Requires team admin or super user."""
    await require_team_admin(user, team_id, litellm_db)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "unchanged"}

    # Set team_member_budget via LiteLLM API
    if "default_member_budget" in updates:
        await litellm.update_team(team_id, team_member_budget=updates["default_member_budget"])

    # Store team-scoped portal settings (membership_duration, default TPM/RPM).
    portal_setting_keys = {
        "membership_duration": f"team:{team_id}:membership_duration",
        "default_tpm_limit": f"team:{team_id}:default_tpm_limit",
        "default_rpm_limit": f"team:{team_id}:default_rpm_limit",
    }
    for field, key in portal_setting_keys.items():
        if field not in updates:
            continue
        raw = updates[field]
        value = "" if raw is None or raw == "" else str(raw)
        if value:
            await db.execute(
                text(
                    "INSERT INTO custom_portal_settings (key, value, updated_by) "
                    "VALUES (:key, :value, :updated_by) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :updated_by"
                ),
                {"key": key, "value": value, "updated_by": user.user_id},
            )
        else:
            await db.execute(
                text("DELETE FROM custom_portal_settings WHERE key = :key"),
                {"key": key},
            )

    return {"status": "updated", "team_id": team_id, **updates}
