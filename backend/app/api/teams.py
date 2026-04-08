"""Team management endpoints - proxies to LiteLLM + adds custom logic."""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db

import json

MEMBER_PREVIEW_LIMIT = 20
router = APIRouter(prefix="/api/teams", tags=["teams"])


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
        team_data = _row_to_team(row)
        if team_data["team_id"] in hidden:
            continue
        is_member = (
            team_data["team_id"] in user_team_ids
            or user.user_id in (row["members"] or [])
            or user.user_id in (row["admins"] or [])
        )
        teams.append({
            **team_data,
            "is_member": is_member,
            "has_pending_request": team_data["team_id"] in pending_team_ids,
        })

    return {"teams": teams}


@router.get("/{team_id}")
async def get_team_detail(
    team_id: str,
    user: CustomUser = Depends(get_current_user),
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
            "       models, expires, created_at "
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
            "key_alias": k["key_alias"],
            "team_id": k["team_id"],
            "user_id": k["user_id"],
            "spend": float(k["spend"]),
            "max_budget": k["max_budget"],
            "budget_duration": k["budget_duration"],
            "budget_reset_at": (k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None),
            "models": list(k["models"] or []),
            "expires": k["expires"].isoformat() if k["expires"] else None,
            "created_at": k["created_at"].isoformat() if k["created_at"] else None,
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
        "is_admin": user.global_role == GlobalRole.SUPER_USER or user.user_id in all_admins,
        "my_membership": {
            "spend": float(membership_row["spend"]) if membership_row else 0,
            "max_budget": membership_row["max_budget"] if membership_row else None,
            "budget_duration": membership_row["budget_duration"] if membership_row else None,
            "budget_reset_at": (membership_row["budget_reset_at"].isoformat() if membership_row and membership_row["budget_reset_at"] else None),
        },
    }


@router.get("/{team_id}/members")
async def list_team_members(
    team_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List team members with their key/budget info (admin only, paginated)."""
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
    membership_result = await litellm_db.execute(
        text(f"""
            SELECT tm.user_id
            FROM "LiteLLM_TeamMembership" tm
            WHERE tm.team_id = :team_id {search_condition}
            ORDER BY tm.user_id
            OFFSET :offset LIMIT :limit
        """),
        {**search_params, "offset": offset, "limit": page_size},
    )
    paged_ids = [r["user_id"] for r in membership_result.mappings()]

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

    # 6. Build member objects
    members = []
    for uid in paged_ids:
        user_keys = keys_by_user.get(uid, [])
        total_spend = sum(k["spend"] for k in user_keys)
        has_unlimited = any(k["max_budget"] is None for k in user_keys)
        total_max_budget: float | None = (
            None if has_unlimited else (sum(k["max_budget"] for k in user_keys) if user_keys else None)  # type: ignore[arg-type]
        )
        members.append(
            {
                "user_id": uid,
                "is_admin": uid in all_admins_set,
                "key_count": len(user_keys),
                "total_spend": total_spend,
                "total_max_budget": total_max_budget,
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

    # Target must be a member of the team
    if body.user_id not in all_members and body.user_id not in all_admins:
        raise HTTPException(status_code=404, detail="User is not a member of this team")

    if body.role == "admin":
        if body.user_id in all_admins:
            return {"status": "unchanged", "message": "User is already an admin"}
        all_admins.append(body.user_id)
    else:
        if body.user_id not in all_admins:
            return {"status": "unchanged", "message": "User is already a member"}
        # Prevent removing the last admin
        if len(all_admins) <= 1:
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
    max_budget: float | None = None
    budget_duration: str | None = None
    tpm_limit: int | None = None
    rpm_limit: int | None = None


@router.put("/{team_id}/settings")
async def update_team_settings(
    team_id: str,
    body: UpdateTeamSettingsRequest,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update team budget/rate limit settings. Requires team admin or super user."""
    result = await litellm_db.execute(
        text('SELECT admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    if user.global_role != GlobalRole.SUPER_USER and user.user_id not in (row["admins"] or []):
        raise HTTPException(status_code=403, detail="Admin access required")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "unchanged"}

    set_clauses = []
    params: dict = {"team_id": team_id}
    for field, value in updates.items():
        set_clauses.append(f"{field} = :{field}")
        params[field] = value

    await litellm_db.execute(
        text(f'UPDATE "LiteLLM_TeamTable" SET {", ".join(set_clauses)} WHERE team_id = :team_id'),
        params,
    )
    await litellm_db.commit()

    return {"status": "updated", "team_id": team_id, **updates}
