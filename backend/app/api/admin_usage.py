"""Admin global usage endpoint (Super User only).

Cross-team view of per-(user, team) usage, aggregated from LiteLLM_DailyUserSpend.
This is the admin counterpart to the per-team usage tab in teams.py: same data
source and api_key -> (team, user) attribution, but un-scoped from a single team.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db

router = APIRouter(prefix="/api/admin/usage", tags=["admin-usage"])

# Sort keys -> the row field they sort on. user_id/team sort on text, the rest
# on numbers; all sorting happens in Python after cross-DB enrichment.
_SORT_FIELDS = {"user_id", "team", "total_tokens", "api_requests", "spend"}


@router.get("")
async def admin_usage_by_user_team(
    start_date: str,
    end_date: str,
    team_id: str | None = None,
    search: str | None = None,
    sort_by: str = "spend",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Global per-(user, team) usage over a date range. Super user only.

    Each row is one (user, team) pair: a user's usage is split across the teams
    whose keys they used. Aggregated from LiteLLM_DailyUserSpend joined to
    LiteLLM_VerificationToken (api_key -> team_id + user_id). `start_date`/
    `end_date` are inclusive `YYYY-MM-DD` strings (the table stores date as text).

    User identities (email / display name) live in the portal DB while spend lives
    in the LiteLLM DB, so the two are merged in Python — search, sort, totals and
    pagination are all applied to the merged set. `totals` reflects the full
    filtered set (every matching row), not just the returned page.
    """
    if sort_by not in _SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    page = max(page, 1)
    page_size = max(1, min(page_size, 1000))

    # Aggregate spend per (team, user). team_id is optionally pinned to one team.
    params: dict = {"start": start_date, "end": end_date}
    team_filter = ""
    if team_id:
        team_filter = "AND vt.team_id = :team_id"
        params["team_id"] = team_id

    agg = await litellm_db.execute(
        text(
            "SELECT vt.team_id AS team_id, vt.user_id AS user_id, "
            "       SUM(d.prompt_tokens + d.completion_tokens) AS total_tokens, "
            "       SUM(d.prompt_tokens) AS input_tokens, "
            "       SUM(d.completion_tokens) AS output_tokens, "
            "       SUM(d.cache_read_input_tokens) AS cache_read_tokens, "
            "       SUM(d.api_requests) AS api_requests, "
            "       SUM(d.spend) AS spend "
            'FROM "LiteLLM_DailyUserSpend" d '
            'JOIN "LiteLLM_VerificationToken" vt ON vt.token = d.api_key '
            "WHERE d.date >= :start AND d.date <= :end "
            f"{team_filter} "
            "GROUP BY vt.team_id, vt.user_id"
        ),
        params,
    )
    agg_rows = [
        {
            "team_id": r["team_id"],
            "user_id": r["user_id"],
            "total_tokens": int(r["total_tokens"] or 0),
            "input_tokens": int(r["input_tokens"] or 0),
            "output_tokens": int(r["output_tokens"] or 0),
            "cache_read_tokens": int(r["cache_read_tokens"] or 0),
            "api_requests": int(r["api_requests"] or 0),
            "spend": float(r["spend"] or 0),
        }
        for r in agg.mappings()
    ]

    # All teams (for the filter dropdown + alias enrichment). Predictable list:
    # every team, regardless of whether it has usage in the selected range.
    teams_result = await litellm_db.execute(
        text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" ORDER BY team_alias')
    )
    team_alias = {r["team_id"]: r["team_alias"] for r in teams_result.mappings()}
    teams = [
        {"team_id": r_id, "team_alias": alias}
        for r_id, alias in team_alias.items()
    ]
    teams.sort(key=lambda t: (t["team_alias"] or "").lower())

    # User identities from the portal DB (different database than spend).
    user_ids = list({r["user_id"] for r in agg_rows if r["user_id"]})
    identities: dict[str, dict] = {}
    if user_ids:
        ident_result = await db.execute(
            text(
                "SELECT user_id, email, display_name FROM custom_users "
                "WHERE user_id = ANY(:ids)"
            ),
            {"ids": user_ids},
        )
        identities = {
            r["user_id"]: {"email": r["email"], "display_name": r["display_name"]}
            for r in ident_result.mappings()
        }

    rows = []
    for r in agg_rows:
        ident = identities.get(r["user_id"], {})
        rows.append(
            {
                "user_id": r["user_id"],
                "email": ident.get("email"),
                "display_name": ident.get("display_name"),
                "team_id": r["team_id"],
                "team_alias": team_alias.get(r["team_id"]),
                "total_tokens": r["total_tokens"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "api_requests": r["api_requests"],
                "spend": r["spend"],
            }
        )

    # Free-text search across user id / email / name / team alias.
    if search:
        needle = search.lower()
        rows = [
            row
            for row in rows
            if any(
                needle in (str(row.get(f) or "")).lower()
                for f in ("user_id", "email", "display_name", "team_alias")
            )
        ]

    totals = {
        "total_tokens": sum(row["total_tokens"] for row in rows),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "cache_read_tokens": sum(row["cache_read_tokens"] for row in rows),
        "api_requests": sum(row["api_requests"] for row in rows),
        "spend": sum(row["spend"] for row in rows),
    }
    total = len(rows)

    reverse = sort_dir.lower() == "desc"
    if sort_by == "team":
        rows.sort(key=lambda row: ((row["team_alias"] or "").lower(), row["user_id"] or ""), reverse=reverse)
    elif sort_by == "user_id":
        rows.sort(key=lambda row: (row["user_id"] or "", row["team_id"] or ""), reverse=reverse)
    else:
        rows.sort(key=lambda row: (row[sort_by], row["user_id"] or ""), reverse=reverse)

    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    return {
        "rows": page_rows,
        "totals": totals,
        "teams": teams,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/daily")
async def admin_usage_daily(
    start_date: str,
    end_date: str,
    team_id: str | None = None,
    _admin: CustomUser = Depends(require_super_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Per-day usage totals over a date range, for the admin usage calendar.

    Aggregated from LiteLLM_DailyUserSpend (already daily, so no timezone math).
    With `team_id`, scoped to that team's keys via api_key -> VerificationToken
    (same attribution as the table view); without it, every team's usage. Super
    user only. `start_date`/`end_date` are inclusive `YYYY-MM-DD` strings.
    """
    params: dict = {"start": start_date, "end": end_date}
    if team_id:
        join = 'JOIN "LiteLLM_VerificationToken" vt ON vt.token = d.api_key'
        where_team = "AND vt.team_id = :team_id"
        params["team_id"] = team_id
    else:
        join = ""
        where_team = ""

    rows = await litellm_db.execute(
        text(
            "SELECT d.date AS date, "
            "       SUM(d.prompt_tokens + d.completion_tokens) AS total_tokens, "
            "       SUM(d.prompt_tokens) AS input_tokens, "
            "       SUM(d.completion_tokens) AS output_tokens, "
            "       SUM(d.cache_read_input_tokens) AS cache_read_tokens, "
            "       SUM(d.api_requests) AS api_requests, "
            "       SUM(d.spend) AS spend "
            'FROM "LiteLLM_DailyUserSpend" d '
            f"{join} "
            "WHERE d.date >= :start AND d.date <= :end "
            f"{where_team} "
            "GROUP BY d.date ORDER BY d.date"
        ),
        params,
    )
    days = [
        {
            "date": r["date"],
            "total_tokens": int(r["total_tokens"] or 0),
            "input_tokens": int(r["input_tokens"] or 0),
            "output_tokens": int(r["output_tokens"] or 0),
            "cache_read_tokens": int(r["cache_read_tokens"] or 0),
            "api_requests": int(r["api_requests"] or 0),
            "spend": float(r["spend"] or 0),
        }
        for r in rows.mappings()
    ]
    totals = {
        "total_tokens": sum(d["total_tokens"] for d in days),
        "input_tokens": sum(d["input_tokens"] for d in days),
        "output_tokens": sum(d["output_tokens"] for d in days),
        "cache_read_tokens": sum(d["cache_read_tokens"] for d in days),
        "api_requests": sum(d["api_requests"] for d in days),
        "spend": sum(d["spend"] for d in days),
    }
    return {"days": days, "totals": totals}
