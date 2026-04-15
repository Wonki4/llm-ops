"""Team request workflow endpoints (join + budget increase)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.permissions import require_team_admin
from app.clients.slack import send_slack_notification
from app.db.models.custom_team_join_request import CustomTeamJoinRequest, JoinRequestStatus
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db

router = APIRouter(prefix="/api/team-requests", tags=["team-requests"])


class CreateJoinRequest(BaseModel):
    team_id: str
    message: str | None = None


class CreateBudgetRequest(BaseModel):
    team_id: str
    requested_budget: float
    message: str | None = None


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
                "SET members_with_roles = members_with_roles || :new_member::jsonb "
                "WHERE team_id = :team_id "
                "AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(members_with_roles) elem WHERE elem->>'user_id' = :user_id)"
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
        # 4. Apply default member budget if configured
        default_budget = await db.execute(
            text("SELECT value FROM custom_portal_settings WHERE key = :key"),
            {"key": f"team:{req.team_id}:default_member_budget"},
        )
        budget_val = default_budget.scalar()
        if budget_val:
            try:
                budget_amount = float(budget_val)
                # Find or create budget entry
                existing_budget = await litellm_db.execute(
                    text('SELECT budget_id FROM "LiteLLM_BudgetTable" WHERE max_budget = :max_budget LIMIT 1'),
                    {"max_budget": budget_amount},
                )
                existing_row = existing_budget.mappings().first()
                if existing_row:
                    target_budget_id = existing_row["budget_id"]
                else:
                    target_budget_id = str(uuid.uuid4())
                    await litellm_db.execute(
                        text(
                            'INSERT INTO "LiteLLM_BudgetTable" (budget_id, max_budget, created_by, updated_by) '
                            "VALUES (:budget_id, :max_budget, :created_by, :updated_by)"
                        ),
                        {"budget_id": target_budget_id, "max_budget": budget_amount, "created_by": user.user_id, "updated_by": user.user_id},
                    )
                await litellm_db.execute(
                    text('UPDATE "LiteLLM_TeamMembership" SET budget_id = :budget_id WHERE user_id = :user_id AND team_id = :team_id'),
                    {"budget_id": target_budget_id, "user_id": req.requester_id, "team_id": req.team_id},
                )
            except (ValueError, TypeError):
                pass
        # 5. Add team_id to UserTable.teams array
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_UserTable" '
                "SET teams = array_append(teams, :team_id) "
                "WHERE user_id = :user_id AND NOT (:team_id = ANY(teams))"
            ),
            {"user_id": req.requester_id, "team_id": req.team_id},
        )
    elif req.request_type == "budget":
        # Find an existing budget with the exact requested max_budget
        existing_budget = await litellm_db.execute(
            text(
                'SELECT budget_id FROM "LiteLLM_BudgetTable" '
                "WHERE max_budget = :max_budget LIMIT 1"
            ),
            {"max_budget": req.requested_budget},
        )
        existing_row = existing_budget.mappings().first()

        if existing_row:
            target_budget_id = existing_row["budget_id"]
        else:
            # Create new budget entry
            import uuid as _uuid
            target_budget_id = str(_uuid.uuid4())
            await litellm_db.execute(
                text(
                    'INSERT INTO "LiteLLM_BudgetTable" (budget_id, max_budget, created_by, updated_by) '
                    "VALUES (:budget_id, :max_budget, :created_by, :updated_by)"
                ),
                {
                    "budget_id": target_budget_id,
                    "max_budget": req.requested_budget,
                    "created_by": user.user_id,
                    "updated_by": user.user_id,
                },
            )

        # Point the user's membership to the target budget
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamMembership" SET budget_id = :budget_id '
                "WHERE user_id = :user_id AND team_id = :team_id"
            ),
            {"budget_id": target_budget_id, "user_id": req.requester_id, "team_id": req.team_id},
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
