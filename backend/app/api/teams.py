"""Team management endpoints - proxies to LiteLLM + adds custom logic."""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

MEMBER_PREVIEW_LIMIT = 20
router = APIRouter(prefix="/api/teams", tags=["teams"])

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
) -> dict:
    """List teams the current user belongs to (direct DB query)."""
    result = await db.execute(
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
    return {"teams": teams}


@router.get("/discover")
async def discover_teams(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all available teams for discovery (direct DB query)."""
    # Fetch all teams
    all_result = await db.execute(
        text(f'SELECT {_TEAM_COLUMNS} FROM "LiteLLM_TeamTable" t ORDER BY t.team_alias'),
    )
    # Fetch user's current team memberships
    membership_result = await db.execute(
        text(
            'SELECT team_id FROM "LiteLLM_TeamMembership" WHERE user_id = :user_id '
            "UNION "
            "SELECT UNNEST(COALESCE(teams, ARRAY[]::text[])) AS team_id "
            'FROM "LiteLLM_UserTable" WHERE user_id = :user_id'
        ),
        {"user_id": user.user_id},
    )
    user_team_ids = {r["team_id"] for r in membership_result.mappings()}

    teams = []
    for row in all_result.mappings():
        team_data = _row_to_team(row)
        # Also check if user is in the admins/members arrays directly
        # (covers edge cases where membership row doesn't exist)
        is_member = (
            team_data["team_id"] in user_team_ids
            or user.user_id in (row["members"] or [])
            or user.user_id in (row["admins"] or [])
        )
        teams.append({**team_data, "is_member": is_member})

    return {"teams": teams}


@router.get("/{team_id}")
async def get_team_detail(
    team_id: str,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),  # noqa: ARG001 — kept for consistency
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get detailed team info including budget, keys, models.

    Optimised for large teams (30K+ members, 90K+ keys):
    - Team metadata AND user keys are read directly from the DB
      (avoids LiteLLM /team/info fetching every key in the team).
    - Member/admin lists are truncated to first N for preview; total counts
      are returned separately.
    """
    # Direct DB read for team row (single row, fast even with 30K member IDs)
    result = await db.execute(
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
    keys_result = await db.execute(
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
        "is_admin": user.user_id in all_admins,
    }


@router.get("/{team_id}/members")
async def list_team_members(
    team_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List team members with their key/budget info (admin only, paginated)."""
    # 1. Get team row
    result = await db.execute(
        text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    all_admins_set = set(row["admins"] or [])

    # Admin check
    if user.user_id not in all_admins_set:
        raise HTTPException(status_code=403, detail="Admin access required")

    # 2. Build unique member list (members + admins combined, sorted)
    all_member_ids = sorted(set(row["members"] or []) | all_admins_set)

    # 3. Filter by search
    if search:
        search_lower = search.lower()
        all_member_ids = [m for m in all_member_ids if search_lower in m.lower()]

    total = len(all_member_ids)

    # 4. Paginate
    offset = (page - 1) * page_size
    paged_ids = all_member_ids[offset : offset + page_size]

    if not paged_ids:
        return {"members": [], "total": total, "page": page, "page_size": page_size}

    # 5. Get keys for paginated members
    keys_result = await db.execute(
        text(
            "SELECT user_id, token, key_alias, key_name, spend, max_budget, "
            "       budget_duration, budget_reset_at, models, created_at "
            'FROM "LiteLLM_VerificationToken" '
            "WHERE team_id = :team_id AND user_id = ANY(:member_ids) "
            "ORDER BY user_id, created_at DESC"
        ),
        {"team_id": team_id, "member_ids": paged_ids},
    )

    # 6. Group keys by user_id
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

    # 7. Build member objects
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
