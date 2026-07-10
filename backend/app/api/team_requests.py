"""Team request workflow endpoints (join + budget increase)."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.permissions import require_team_admin
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.clients.slack import send_slack_notification
from app.db.models.custom_team_join_request import CustomTeamJoinRequest, JoinRequestStatus
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db
from app.services.member_budget_boost import apply_member_budget_boost, resolve_effective_budget

router = APIRouter(prefix="/api/team-requests", tags=["team-requests"])


class CreateJoinRequest(BaseModel):
    team_id: str
    message: str | None = None


class CreateBudgetRequest(BaseModel):
    team_id: str
    requested_budget: float
    message: str | None = None
    requested_duration_days: int | None = 30


class ReviewRequest(BaseModel):
    comment: str | None = None


def _request_to_dict(r: CustomTeamJoinRequest) -> dict:
    return {
        "id": str(r.id),
        "requester_id": r.requester_id,
        "team_id": r.team_id,
        "team_alias": r.team_alias,
        "request_type": r.request_type,
        "message": r.message,
        "requested_budget": r.requested_budget,
        "requested_duration_days": r.requested_duration_days,
        "status": r.status.value,
        "reviewed_by": r.reviewed_by,
        "review_comment": r.review_comment,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_join_request(
    body: CreateJoinRequest,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Request to join a team. Prevents duplicate pending requests."""
    existing = await db.execute(
        select(CustomTeamJoinRequest).where(
            CustomTeamJoinRequest.requester_id == user.user_id,
            CustomTeamJoinRequest.team_id == body.team_id,
            CustomTeamJoinRequest.request_type == "join",
            CustomTeamJoinRequest.status == JoinRequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You already have a pending request for this team"
        )

    # Check team exists and get alias + membership check via DB
    team_result = await litellm_db.execute(
        text('SELECT team_alias, members FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": body.team_id},
    )
    team_row = team_result.mappings().first()
    if not team_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if user.user_id in (team_row["members"] or []):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You are already a member of this team")
    team_alias = team_row["team_alias"] or body.team_id

    join_request = CustomTeamJoinRequest(
        id=uuid.uuid4(),
        requester_id=user.user_id,
        team_id=body.team_id,
        team_alias=team_alias,
        request_type="join",
        message=body.message,
        status=JoinRequestStatus.PENDING,
    )
    db.add(join_request)
    await db.flush()

    await send_slack_notification(
        requester_id=user.user_id,
        team_alias=team_alias,
        team_id=body.team_id,
        message=body.message,
    )

    return _request_to_dict(join_request)


@router.post("/budget", status_code=status.HTTP_201_CREATED)
async def create_budget_request(
    body: CreateBudgetRequest,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Request a budget increase for the user's keys in a team."""
    if body.requested_budget <= 0:
        raise HTTPException(status_code=400, detail="Requested budget must be positive")
    if body.requested_duration_days is not None and body.requested_duration_days <= 0:
        raise HTTPException(status_code=400, detail="Requested duration must be positive")

    # Check duplicate pending budget request
    existing = await db.execute(
        select(CustomTeamJoinRequest).where(
            CustomTeamJoinRequest.requester_id == user.user_id,
            CustomTeamJoinRequest.team_id == body.team_id,
            CustomTeamJoinRequest.request_type == "budget",
            CustomTeamJoinRequest.status == JoinRequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You already have a pending budget request for this team"
        )

    # Get team alias
    result = await litellm_db.execute(
        text('SELECT team_alias FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": body.team_id},
    )
    row = result.mappings().first()
    team_alias = row["team_alias"] if row else body.team_id

    budget_request = CustomTeamJoinRequest(
        id=uuid.uuid4(),
        requester_id=user.user_id,
        team_id=body.team_id,
        team_alias=team_alias,
        request_type="budget",
        requested_budget=body.requested_budget,
        requested_duration_days=body.requested_duration_days,
        message=body.message,
        status=JoinRequestStatus.PENDING,
    )
    db.add(budget_request)
    await db.flush()

    return _request_to_dict(budget_request)


@router.get("")
async def list_requests(
    team_id: str | None = None,
    status_filter: str | None = None,
    request_type: str | None = None,
    mine_only: bool = False,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List requests. mine_only=true shows only own requests regardless of role."""
    query = select(CustomTeamJoinRequest).order_by(CustomTeamJoinRequest.created_at.desc())

    if mine_only:
        query = query.where(CustomTeamJoinRequest.requester_id == user.user_id)
    elif user.global_role == GlobalRole.SUPER_USER:
        if team_id:
            query = query.where(CustomTeamJoinRequest.team_id == team_id)
    else:
        if team_id:
            await require_team_admin(user, team_id, litellm_db)
            query = query.where(CustomTeamJoinRequest.team_id == team_id)
        else:
            # Team admin: show requests for all teams they admin
            admin_teams_result = await litellm_db.execute(
                text(
                    'SELECT team_id FROM "LiteLLM_TeamTable" '
                    "WHERE :user_id = ANY(COALESCE(admins, ARRAY[]::text[]))"
                ),
                {"user_id": user.user_id},
            )
            admin_team_ids = [r["team_id"] for r in admin_teams_result.mappings()]
            if admin_team_ids:
                query = query.where(CustomTeamJoinRequest.team_id.in_(admin_team_ids))
            else:
                query = query.where(CustomTeamJoinRequest.requester_id == user.user_id)

    if status_filter:
        query = query.where(CustomTeamJoinRequest.status == JoinRequestStatus(status_filter))

    if request_type:
        query = query.where(CustomTeamJoinRequest.request_type == request_type)

    result = await db.execute(query)
    requests = result.scalars().all()

    return {"requests": [_request_to_dict(r) for r in requests]}


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: ReviewRequest = ReviewRequest(),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Approve a request. Must be team admin or super user."""
    result = await db.execute(select(CustomTeamJoinRequest).where(CustomTeamJoinRequest.id == uuid.UUID(request_id)))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != JoinRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Request already {req.status.value}"
        )

    await require_team_admin(user, req.team_id, litellm_db)

    if req.request_type == "join":
        # 1. Add to TeamTable.members array
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamTable" '
                "SET members = array_append(members, :user_id) "
                "WHERE team_id = :team_id AND NOT (:user_id = ANY(members))"
            ),
            {"user_id": req.requester_id, "team_id": req.team_id},
        )
        # 2. Add to members_with_roles JSONB
        import json as _json
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamTable" '
                "SET members_with_roles = COALESCE(members_with_roles, CAST('[]' AS jsonb)) || CAST(:new_member AS jsonb) "
                "WHERE team_id = :team_id "
                "AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(members_with_roles, CAST('[]' AS jsonb))) elem WHERE elem->>'user_id' = :user_id)"
            ),
            {
                "new_member": _json.dumps([{"role": "user", "user_id": req.requester_id, "user_email": None}]),
                "team_id": req.team_id,
                "user_id": req.requester_id,
            },
        )
        # 3. Create TeamMembership row (upsert)
        await litellm_db.execute(
            text(
                'INSERT INTO "LiteLLM_TeamMembership" (user_id, team_id, spend) '
                "VALUES (:user_id, :team_id, 0) "
                "ON CONFLICT (user_id, team_id) DO NOTHING"
            ),
            {"user_id": req.requester_id, "team_id": req.team_id},
        )
        # 4. Apply default member budget from team metadata (team_member_budget_id)
        team_meta_result = await litellm_db.execute(
            text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
            {"team_id": req.team_id},
        )
        team_metadata = team_meta_result.scalar()
        if isinstance(team_metadata, dict) and team_metadata.get("team_member_budget_id"):
            await litellm_db.execute(
                text('UPDATE "LiteLLM_TeamMembership" SET budget_id = :budget_id WHERE user_id = :user_id AND team_id = :team_id'),
                {"budget_id": team_metadata["team_member_budget_id"], "user_id": req.requester_id, "team_id": req.team_id},
            )
        # 5. Create membership expiry if duration is configured
        duration_result = await db.execute(
            text("SELECT value FROM custom_portal_settings WHERE key = :key"),
            {"key": f"team:{req.team_id}:membership_duration"},
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
                        "ON CONFLICT (user_id, team_id) DO UPDATE SET expires_at = :expires_at, status = 'active'"
                    ),
                    {"user_id": req.requester_id, "team_id": req.team_id, "expires_at": expires_at},
                )
        # 6. Add team_id to UserTable.teams array
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_UserTable" '
                "SET teams = array_append(teams, :team_id) "
                "WHERE user_id = :user_id AND NOT (:team_id = ANY(teams))"
            ),
            {"user_id": req.requester_id, "team_id": req.team_id},
        )
    elif req.request_type == "budget":
        original = (
            await resolve_effective_budget(litellm_db, req.team_id, req.requester_id)
            if req.requested_duration_days
            else None
        )
        if req.requested_duration_days and original is not None:
            # Temporary increase → member budget boost (auto-reverts at expiry).
            await apply_member_budget_boost(
                db, litellm, litellm_db,
                team_id=req.team_id, user_id=req.requester_id,
                boost_max_budget=req.requested_budget,
                expires_at=datetime.now(UTC) + timedelta(days=req.requested_duration_days),
                created_by=user.user_id,
            )
        else:
            # Permanent increase (no period, or nothing to revert to). Same
            # path as a manual per-member budget change
            # (teams.change_member_budget) — LiteLLM clone-on-writes a
            # dedicated budget row for this member, so approving two requests
            # for the same amount never makes them share one row.
            await litellm.update_team_member(
                req.team_id,
                req.requester_id,
                max_budget_in_team=req.requested_budget,
            )

    req.status = JoinRequestStatus.APPROVED
    req.reviewed_by = user.user_id
    req.review_comment = body.comment
    await db.flush()

    return {"status": "approved", "team_id": req.team_id, "requester_id": req.requester_id}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: ReviewRequest = ReviewRequest(),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Reject a request. Must be team admin or super user."""
    result = await db.execute(select(CustomTeamJoinRequest).where(CustomTeamJoinRequest.id == uuid.UUID(request_id)))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.status != JoinRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Request already {req.status.value}"
        )

    await require_team_admin(user, req.team_id, litellm_db)

    req.status = JoinRequestStatus.REJECTED
    req.reviewed_by = user.user_id
    req.review_comment = body.comment
    await db.flush()

    return {"status": "rejected", "team_id": req.team_id, "requester_id": req.requester_id}
