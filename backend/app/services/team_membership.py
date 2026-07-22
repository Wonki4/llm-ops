"""Team-member removal: LiteLLM-authoritative delete + portal-side sync.

Membership lives in three overlapping places on ``LiteLLM_TeamTable``:
``members_with_roles`` (JSONB — what the proxy actually enforces on), and the
``members`` / ``admins`` ``String[]`` columns the portal reads. LiteLLM's
``/team/member_delete`` maintains the first (plus keys, the membership row, the
user's teams array, and the proxy cache) but NOT the arrays; the portal's old
DB-direct removal did the opposite. Doing only one side left the proxy and the
portal disagreeing about who is in a team. This helper does both, from one
place, so the two never diverge again.
"""

import logging

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.litellm import LiteLLMClient

logger = logging.getLogger(__name__)


async def remove_member_from_team(
    litellm: LiteLLMClient,
    litellm_db: AsyncSession,
    db: AsyncSession,
    *,
    team_id: str,
    user_id: str,
) -> None:
    """Remove a member from a team everywhere it is recorded.

    LiteLLM's ``/team/member_delete`` is authoritative: it clears the canonical
    ``members_with_roles``, deletes the member's team keys (persisting an audit
    row), drops the ``LiteLLM_TeamMembership`` row, removes the team from the
    user's ``teams`` array, and lets the proxy refresh its cache. It leaves the
    ``members`` / ``admins`` ``String[]`` columns the portal reads and the
    portal's own ``custom_team_membership`` expiry row untouched — this helper
    syncs both afterward.

    If LiteLLM reports the member is already gone (400/404), the portal cleanup
    still runs: repairing that drift is the point. Any other LiteLLM failure
    raises 502 before the portal is touched.
    """
    try:
        await litellm.remove_team_member(team_id, user_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (400, 404):
            logger.exception("LiteLLM member_delete failed for %s/%s", team_id, user_id)
            raise HTTPException(status_code=502, detail="LiteLLM member removal failed") from e
        logger.info(
            "LiteLLM reports %s not in team %s (status %s); syncing portal state anyway",
            user_id, team_id, e.response.status_code,
        )

    # Sync the members/admins String[] arrays (member_delete only touches
    # members_with_roles).
    row = (
        await litellm_db.execute(
            text('SELECT members, admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
            {"team_id": team_id},
        )
    ).mappings().first()
    if row is not None:
        members = [m for m in (row["members"] or []) if m != user_id]
        admins = [a for a in (row["admins"] or []) if a != user_id]
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamTable" SET members = :members, admins = :admins '
                "WHERE team_id = :team_id"
            ),
            {"members": members, "admins": admins, "team_id": team_id},
        )
        await litellm_db.commit()

    # Drop the portal's expiry-tracking row so a later rejoin starts clean.
    await db.execute(
        text("DELETE FROM custom_team_membership WHERE user_id = :user_id AND team_id = :team_id"),
        {"user_id": user_id, "team_id": team_id},
    )
    await db.commit()
