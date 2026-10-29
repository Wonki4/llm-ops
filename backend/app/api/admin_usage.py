"""Admin global usage endpoints (Super User only).

Cross-team usage as user → key → model group, aggregated from
LiteLLM_DailyUserSpend. The admin counterpart to the per-team usage tab in
teams.py: same data source and api_key -> (team, user) attribution (see
app.services.usage_breakdown), but un-scoped from a single team.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db
from app.services.usage_breakdown import (
    add_sums,
    attributed_user,
    empty_sums,
    key_directory,
    nest_by_key,
    team_aliases,
    usage_by_key_and_model,
    usage_by_key_and_user,
)

router = APIRouter(prefix="/api/admin/usage", tags=["admin-usage"])

# Sort keys -> the row field they sort on. user_id sorts on text, the rest on
# numbers; all sorting happens in Python after cross-DB enrichment.
_SORT_FIELDS = {"user_id", "key_count", "total_tokens", "api_requests", "spend"}


@router.get("")
async def admin_usage_by_user(
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
    """Global per-user usage over a date range. Super user only.

    Each row is one user with the number of keys they used; the per-key and
    per-model-group breakdown comes from ``/by-key``. Usage counts toward the
    key's owner (deleted keys included), or the user LiteLLM stamped on the row
    when the key is unknown; ``user_id`` null collects usage with no user.
    `team_id` limits it to that team's keys. `start_date`/`end_date` are
    inclusive `YYYY-MM-DD` strings (the table stores date as text).

    User identities (email / display name) live in the portal DB while spend lives
    in the LiteLLM DB, so the two are merged in Python — search, sort, totals and
    pagination are all applied to the merged set. `totals` reflects the full
    filtered set (every matching row), not just the returned page.
    """
    if sort_by not in _SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    page = max(page, 1)
    page_size = max(1, min(page_size, 1000))

    if team_id:
        directory = await key_directory(litellm_db, team_id=team_id)
        usage = await usage_by_key_and_user(litellm_db, start=start_date, end=end_date, tokens=list(directory))
    else:
        usage = await usage_by_key_and_user(litellm_db, start=start_date, end=end_date)
        directory = await key_directory(litellm_db, tokens=list({r["api_key"] for r in usage}))

    by_user: dict[str, dict] = {}
    for r in usage:
        uid = attributed_user(r["user_id"], directory.get(r["api_key"]))
        acc = by_user.setdefault(uid, {**empty_sums(), "_keys": set()})
        add_sums(acc, r)
        acc["_keys"].add(r["api_key"])

    # All teams (for the filter dropdown). Predictable list: every team,
    # regardless of whether it has usage in the selected range.
    teams_result = await litellm_db.execute(
        text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" ORDER BY team_alias')
    )
    teams = [{"team_id": r["team_id"], "team_alias": r["team_alias"]} for r in teams_result.mappings()]
    teams.sort(key=lambda t: (t["team_alias"] or "").lower())

    # User identities from the portal DB (different database than spend).
    identities: dict[str, dict] = {}
    user_ids = [uid for uid in by_user if uid]
    if user_ids:
        ident_result = await db.execute(
            text("SELECT user_id, email, display_name FROM custom_users WHERE user_id = ANY(:ids)"),
            {"ids": user_ids},
        )
        identities = {
            r["user_id"]: {"email": r["email"], "display_name": r["display_name"]} for r in ident_result.mappings()
        }

    rows = []
    for uid, acc in by_user.items():
        ident = identities.get(uid, {})
        keys = acc.pop("_keys")
        rows.append(
            {
                "user_id": uid or None,
                "email": ident.get("email"),
                "display_name": ident.get("display_name"),
                "key_count": len(keys),
                **acc,
            }
        )

    # Free-text search across user id / email / name.
    if search:
        needle = search.lower()
        rows = [
            row
            for row in rows
            if any(needle in (str(row.get(f) or "")).lower() for f in ("user_id", "email", "display_name"))
        ]

    totals = empty_sums()
    for row in rows:
        add_sums(totals, row)
    total = len(rows)

    reverse = sort_dir.lower() == "desc"
    if sort_by == "user_id":
        rows.sort(key=lambda row: row["user_id"] or "", reverse=reverse)
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


@router.get("/by-key")
async def admin_user_usage_by_key(
    start_date: str,
    end_date: str,
    user_id: str = "",
    team_id: str | None = None,
    _admin: CustomUser = Depends(require_super_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """One user's usage per key, then per model group. Super user only.

    Same attribution as the per-user list: ``user_id=""`` is the no-user bucket.
    With `team_id`, only that team's keys. Keys carry their team (alias) and
    state (active / deleted / unknown); keys and model groups are ordered by spend.
    """
    if team_id:
        directory = await key_directory(litellm_db, team_id=team_id, user_id=user_id)
        rows = await usage_by_key_and_model(litellm_db, start=start_date, end=end_date, tokens=list(directory))
    else:
        directory = await key_directory(litellm_db, user_id=user_id)
        rows = await usage_by_key_and_model(
            litellm_db, start=start_date, end=end_date, tokens=list(directory), user_id=user_id
        )
        # Rows stamped with this user whose key we have not resolved yet: the
        # key may belong to someone else now, so look it up before attributing.
        missing = list({r["api_key"] for r in rows} - directory.keys())
        directory.update(await key_directory(litellm_db, tokens=missing))
        rows = [r for r in rows if attributed_user(r["user_id"], directory.get(r["api_key"])) == user_id]

    team_ids = {info.team_id for info in directory.values() if info.team_id}
    aliases = await team_aliases(litellm_db, team_ids)
    return {"user_id": user_id or None, "keys": nest_by_key(rows, directory, aliases)}


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
    With `team_id`, scoped to that team's keys, deleted ones included (same
    attribution as the table view); without it, every team's usage. Super
    user only. `start_date`/`end_date` are inclusive `YYYY-MM-DD` strings.
    """
    params: dict = {"start": start_date, "end": end_date}
    where_team = ""
    if team_id:
        directory = await key_directory(litellm_db, team_id=team_id)
        if not directory:
            return {"days": [], "totals": empty_sums()}
        where_team = "AND d.api_key = ANY(:tokens)"
        params["tokens"] = list(directory)

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
