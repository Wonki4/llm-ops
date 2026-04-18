"""Admin user management endpoints (Super User only)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("")
async def list_users(
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    role: str | None = None,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List all portal users (사번 기준) with pagination and optional search/role filter."""
    conditions: list[str] = []
    params: dict = {}

    if search:
        conditions.append("(u.user_id ILIKE :search OR u.email ILIKE :search OR u.display_name ILIKE :search)")
        params["search"] = f"%{search}%"

    if role in {"user", "super_user"}:
        conditions.append("u.global_role = :role")
        params["role"] = role

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM custom_users u {where_clause}"),
            params,
        )
    ).scalar() or 0

    offset = (page - 1) * page_size
    page_size = min(page_size, 1000)

    users_result = await db.execute(
        text(
            f"""
            SELECT u.user_id, u.email, u.display_name, u.global_role, u.created_at, u.updated_at
            FROM custom_users u
            {where_clause}
            ORDER BY u.user_id
            OFFSET :offset LIMIT :limit
            """
        ),
        {**params, "offset": offset, "limit": page_size},
    )
    rows = list(users_result.mappings())
    user_ids = [r["user_id"] for r in rows]

    key_counts: dict[str, int] = {}
    team_counts: dict[str, int] = {}
    spend_map: dict[str, float] = {}

    if user_ids:
        key_result = await litellm_db.execute(
            text(
                'SELECT user_id, COUNT(*) AS cnt FROM "LiteLLM_VerificationToken" '
                "WHERE user_id = ANY(:ids) GROUP BY user_id"
            ),
            {"ids": user_ids},
        )
        key_counts = {r["user_id"]: int(r["cnt"]) for r in key_result.mappings()}

        team_result = await litellm_db.execute(
            text(
                'SELECT user_id, COUNT(DISTINCT team_id) AS cnt FROM "LiteLLM_TeamMembership" '
                "WHERE user_id = ANY(:ids) GROUP BY user_id"
            ),
            {"ids": user_ids},
        )
        team_counts = {r["user_id"]: int(r["cnt"]) for r in team_result.mappings()}

        spend_result = await litellm_db.execute(
            text(
                'SELECT user_id, spend, max_budget FROM "LiteLLM_UserTable" '
                "WHERE user_id = ANY(:ids)"
            ),
            {"ids": user_ids},
        )
        spend_map = {
            r["user_id"]: {
                "spend": float(r["spend"] or 0),
                "max_budget": float(r["max_budget"]) if r["max_budget"] is not None else None,
            }
            for r in spend_result.mappings()
        }

    users = []
    for r in rows:
        uid = r["user_id"]
        budget = spend_map.get(uid, {"spend": 0.0, "max_budget": None})
        users.append(
            {
                "user_id": uid,
                "email": r["email"],
                "display_name": r["display_name"],
                "global_role": r["global_role"].value if hasattr(r["global_role"], "value") else r["global_role"],
                "key_count": key_counts.get(uid, 0),
                "team_count": team_counts.get(uid, 0),
                "spend": budget["spend"],
                "max_budget": budget["max_budget"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
        )

    return {"users": users, "total": total, "page": page, "page_size": page_size}


@router.get("/{user_id}")
async def get_user_detail(
    user_id: str,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Get detailed info for a user: profile, keys, teams."""
    user_result = await db.execute(
        text(
            "SELECT user_id, email, display_name, global_role, litellm_user_id, created_at, updated_at "
            "FROM custom_users WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    user_row = user_result.mappings().first()
    if not user_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    litellm_user_result = await litellm_db.execute(
        text(
            'SELECT spend, max_budget, teams, tpm_limit, rpm_limit, budget_duration, budget_reset_at '
            'FROM "LiteLLM_UserTable" WHERE user_id = :uid'
        ),
        {"uid": user_id},
    )
    litellm_user = litellm_user_result.mappings().first()

    keys_result = await litellm_db.execute(
        text(
            "SELECT token, key_alias, key_name, team_id, spend, max_budget, "
            "       budget_duration, budget_reset_at, models, tpm_limit, rpm_limit, expires, created_at "
            'FROM "LiteLLM_VerificationToken" '
            "WHERE user_id = :uid "
            "ORDER BY created_at DESC"
        ),
        {"uid": user_id},
    )
    keys = [
        {
            "token": k["token"],
            "key_alias": k["key_alias"],
            "key_name": k["key_name"],
            "team_id": k["team_id"],
            "spend": float(k["spend"] or 0),
            "max_budget": float(k["max_budget"]) if k["max_budget"] is not None else None,
            "budget_duration": k["budget_duration"],
            "budget_reset_at": k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None,
            "models": list(k["models"] or []),
            "tpm_limit": k["tpm_limit"],
            "rpm_limit": k["rpm_limit"],
            "expires": k["expires"].isoformat() if k["expires"] else None,
            "created_at": k["created_at"].isoformat() if k["created_at"] else None,
        }
        for k in keys_result.mappings()
    ]

    teams_result = await litellm_db.execute(
        text(
            """
            SELECT tm.team_id, t.team_alias,
                   tm.spend AS membership_spend,
                   b.max_budget AS membership_max_budget,
                   t.admins
            FROM "LiteLLM_TeamMembership" tm
            LEFT JOIN "LiteLLM_TeamTable" t ON tm.team_id = t.team_id
            LEFT JOIN "LiteLLM_BudgetTable" b ON tm.budget_id = b.budget_id
            WHERE tm.user_id = :uid
            ORDER BY t.team_alias NULLS LAST, tm.team_id
            """
        ),
        {"uid": user_id},
    )
    teams = [
        {
            "team_id": t["team_id"],
            "team_alias": t["team_alias"],
            "is_admin": user_id in (t["admins"] or []),
            "spend": float(t["membership_spend"] or 0),
            "max_budget": float(t["membership_max_budget"]) if t["membership_max_budget"] is not None else None,
        }
        for t in teams_result.mappings()
    ]

    expiry_result = await db.execute(
        text(
            "SELECT team_id, expires_at, status FROM custom_team_membership "
            "WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    expiry_map = {
        r["team_id"]: {
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "status": r["status"],
        }
        for r in expiry_result.mappings()
    }
    for team in teams:
        expiry = expiry_map.get(team["team_id"])
        team["expires_at"] = expiry["expires_at"] if expiry else None
        team["expiry_status"] = expiry["status"] if expiry else None

    role_value = (
        user_row["global_role"].value
        if hasattr(user_row["global_role"], "value")
        else user_row["global_role"]
    )

    return {
        "user": {
            "user_id": user_row["user_id"],
            "email": user_row["email"],
            "display_name": user_row["display_name"],
            "global_role": role_value,
            "litellm_user_id": user_row["litellm_user_id"],
            "created_at": user_row["created_at"].isoformat() if user_row["created_at"] else None,
            "updated_at": user_row["updated_at"].isoformat() if user_row["updated_at"] else None,
            "spend": float(litellm_user["spend"] or 0) if litellm_user else 0.0,
            "max_budget": (
                float(litellm_user["max_budget"])
                if litellm_user and litellm_user["max_budget"] is not None
                else None
            ),
            "tpm_limit": litellm_user["tpm_limit"] if litellm_user else None,
            "rpm_limit": litellm_user["rpm_limit"] if litellm_user else None,
        },
        "keys": keys,
        "teams": teams,
    }


class UpdateKeyLimitsRequest(BaseModel):
    tpm_limit: int | None = Field(None, ge=0)
    rpm_limit: int | None = Field(None, ge=0)


@router.patch("/{user_id}/keys/{token}/limits")
async def update_user_key_limits(
    user_id: str,
    token: str,
    body: UpdateKeyLimitsRequest,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update TPM/RPM limits for a user's key (super user only)."""
    owner_result = await litellm_db.execute(
        text('SELECT user_id FROM "LiteLLM_VerificationToken" WHERE token = :token'),
        {"token": token},
    )
    owner_row = owner_result.mappings().first()
    if not owner_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    if owner_row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Key does not belong to the specified user",
        )

    await litellm.update_key(token, tpm_limit=body.tpm_limit, rpm_limit=body.rpm_limit)
    return {"status": "updated", "tpm_limit": body.tpm_limit, "rpm_limit": body.rpm_limit}


@router.delete("/{user_id}/teams/{team_id}")
async def remove_user_from_team(
    user_id: str,
    team_id: str,
    _admin: CustomUser = Depends(require_super_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Force-remove a user from a team (super user only)."""
    result = await litellm_db.execute(
        text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    all_members = list(row["members"] or [])
    all_admins = list(row["admins"] or [])

    if user_id in all_admins and len(all_admins) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="마지막 관리자는 삭제할 수 없습니다.",
        )

    if user_id in all_members:
        all_members.remove(user_id)
    if user_id in all_admins:
        all_admins.remove(user_id)

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamTable" SET admins = :admins, members = :members '
            "WHERE team_id = :team_id"
        ),
        {"admins": all_admins, "members": all_members, "team_id": team_id},
    )

    await litellm_db.execute(
        text('DELETE FROM "LiteLLM_TeamMembership" WHERE team_id = :team_id AND user_id = :user_id'),
        {"team_id": team_id, "user_id": user_id},
    )

    user_result = await litellm_db.execute(
        text('SELECT teams FROM "LiteLLM_UserTable" WHERE user_id = :user_id'),
        {"user_id": user_id},
    )
    user_row = user_result.mappings().first()
    if user_row and user_row["teams"]:
        user_teams = [t for t in user_row["teams"] if t != team_id]
        await litellm_db.execute(
            text('UPDATE "LiteLLM_UserTable" SET teams = :teams WHERE user_id = :user_id'),
            {"teams": user_teams, "user_id": user_id},
        )

    await litellm_db.commit()
    return {"status": "removed", "user_id": user_id, "team_id": team_id}


class AssignTeamRequest(BaseModel):
    team_id: str
    role: str = Field("user", pattern="^(user|admin)$")


@router.post("/{user_id}/teams")
async def assign_user_to_team(
    user_id: str,
    body: AssignTeamRequest,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Force-add a user to a team (super user only)."""
    import json as _json

    user_check = await db.execute(
        text("SELECT 1 FROM custom_users WHERE user_id = :uid"),
        {"uid": user_id},
    )
    if user_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    team_result = await litellm_db.execute(
        text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": body.team_id},
    )
    team_row = team_result.mappings().first()
    if not team_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    current_members = list(team_row["members"] or [])
    current_admins = list(team_row["admins"] or [])
    if user_id in current_members or user_id in current_admins:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 팀에 소속되어 있습니다.",
        )

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamTable" '
            "SET members = array_append(members, :user_id) "
            "WHERE team_id = :team_id AND NOT (:user_id = ANY(members))"
        ),
        {"user_id": user_id, "team_id": body.team_id},
    )
    if body.role == "admin":
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamTable" '
                "SET admins = array_append(admins, :user_id) "
                "WHERE team_id = :team_id AND NOT (:user_id = ANY(admins))"
            ),
            {"user_id": user_id, "team_id": body.team_id},
        )

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_TeamTable" '
            "SET members_with_roles = COALESCE(members_with_roles, CAST('[]' AS jsonb)) "
            "|| CAST(:new_member AS jsonb) "
            "WHERE team_id = :team_id "
            "AND NOT EXISTS ("
            "    SELECT 1 FROM jsonb_array_elements("
            "        COALESCE(members_with_roles, CAST('[]' AS jsonb))"
            "    ) elem WHERE elem->>'user_id' = :user_id"
            ")"
        ),
        {
            "new_member": _json.dumps(
                [{"role": body.role, "user_id": user_id, "user_email": None}]
            ),
            "team_id": body.team_id,
            "user_id": user_id,
        },
    )

    await litellm_db.execute(
        text(
            'INSERT INTO "LiteLLM_TeamMembership" (user_id, team_id, spend) '
            "VALUES (:user_id, :team_id, 0) "
            "ON CONFLICT (user_id, team_id) DO NOTHING"
        ),
        {"user_id": user_id, "team_id": body.team_id},
    )

    team_meta_result = await litellm_db.execute(
        text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": body.team_id},
    )
    team_metadata = team_meta_result.scalar()
    if isinstance(team_metadata, dict) and team_metadata.get("team_member_budget_id"):
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamMembership" SET budget_id = :budget_id '
                "WHERE user_id = :user_id AND team_id = :team_id"
            ),
            {
                "budget_id": team_metadata["team_member_budget_id"],
                "user_id": user_id,
                "team_id": body.team_id,
            },
        )

    duration_result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = :key"),
        {"key": f"team:{body.team_id}:membership_duration"},
    )
    duration_val = duration_result.scalar()
    if duration_val:
        from app.api.teams import _parse_duration
        delta = _parse_duration(duration_val)
        if delta:
            from datetime import datetime as _dt
            expires_at = _dt.now() + delta
            await db.execute(
                text(
                    "INSERT INTO custom_team_membership (id, user_id, team_id, expires_at, status) "
                    "VALUES (gen_random_uuid(), :user_id, :team_id, :expires_at, 'active') "
                    "ON CONFLICT (user_id, team_id) DO UPDATE "
                    "SET expires_at = :expires_at, status = 'active'"
                ),
                {"user_id": user_id, "team_id": body.team_id, "expires_at": expires_at},
            )

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_UserTable" '
            "SET teams = array_append(teams, :team_id) "
            "WHERE user_id = :user_id AND NOT (:team_id = ANY(teams))"
        ),
        {"user_id": user_id, "team_id": body.team_id},
    )

    await litellm_db.commit()
    await db.commit()
    return {"status": "added", "user_id": user_id, "team_id": body.team_id, "role": body.role}
