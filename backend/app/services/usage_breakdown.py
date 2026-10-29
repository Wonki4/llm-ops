"""User → key → model-group usage, aggregated from LiteLLM_DailyUserSpend.

LiteLLM records daily usage per (user, api_key, model, ...), so the key is the
natural middle level between a user and their model groups. Shared by the team
usage tab (teams.py) and the admin usage page (admin_usage.py).

Deleted keys keep their usage rows but leave LiteLLM_VerificationToken, so a
plain JOIN silently drops their spend. Keys are therefore resolved from both
the live table and LiteLLM_DeletedVerificationToken; usage whose key is in
neither (e.g. hard-deleted before LiteLLM archived keys) falls back to the
row's own user_id and is reported as an "unknown" key.
"""

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

USAGE_SUMS_SQL = (
    "SUM(prompt_tokens + completion_tokens) AS total_tokens, "
    "SUM(prompt_tokens) AS input_tokens, "
    "SUM(completion_tokens) AS output_tokens, "
    "SUM(cache_read_input_tokens) AS cache_read_tokens, "
    "SUM(api_requests) AS api_requests, "
    "SUM(spend) AS spend"
)

_INT_FIELDS = ("total_tokens", "input_tokens", "output_tokens", "cache_read_tokens", "api_requests")

# Public model-group name, falling back to the model when LiteLLM left the
# group empty (some routes do), so there are no "(unknown)" buckets.
MODEL_GROUP_SQL = "COALESCE(NULLIF(model_group, ''), model)"


def empty_sums() -> dict:
    return {**{f: 0 for f in _INT_FIELDS}, "spend": 0.0}


def add_sums(acc: dict, row) -> dict:
    for f in _INT_FIELDS:
        acc[f] += int(row[f] or 0)
    acc["spend"] += float(row["spend"] or 0)
    return acc


@dataclass(frozen=True)
class KeyInfo:
    token: str
    user_id: str | None
    team_id: str | None
    alias: str | None
    sso: bool
    deleted: bool

    @property
    def personal(self) -> bool:
        return self.team_id is None


def _metadata(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def key_info_from_row(row, *, deleted: bool) -> KeyInfo:
    meta = _metadata(row.get("metadata"))
    return KeyInfo(
        token=row["token"],
        user_id=row.get("user_id"),
        team_id=row.get("team_id"),
        # Same label the portal's key list shows, then LiteLLM's own names.
        alias=meta.get("display_alias") or row.get("key_alias") or row.get("key_name"),
        sso=meta.get("issued_via") == "gateway_sso",
        deleted=deleted,
    )


async def key_directory(
    litellm_db: AsyncSession,
    *,
    team_id: str | None = None,
    user_id: str | None = None,
    tokens: list[str] | None = None,
) -> dict[str, KeyInfo]:
    """Live and deleted keys matching every given filter, by hashed token.

    ``user_id=""`` selects keys with no owner. A live key wins over an archived
    copy of the same token.
    """
    conditions: list[str] = []
    params: dict = {}
    if team_id is not None:
        conditions.append("team_id = :team_id")
        params["team_id"] = team_id
    if user_id is not None:
        conditions.append("COALESCE(user_id, '') = :user_id")
        params["user_id"] = user_id
    if tokens is not None:
        if not tokens:
            return {}
        conditions.append("token = ANY(:tokens)")
        params["tokens"] = tokens
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    cols = "token, user_id, team_id, key_alias, key_name, metadata"

    deleted = await litellm_db.execute(
        text(
            f"SELECT DISTINCT ON (token) {cols} "
            'FROM "LiteLLM_DeletedVerificationToken" '
            f"{where}"
            "ORDER BY token, deleted_at DESC"
        ),
        params,
    )
    directory = {r["token"]: key_info_from_row(r, deleted=True) for r in deleted.mappings()}
    live = await litellm_db.execute(text(f'SELECT {cols} FROM "LiteLLM_VerificationToken" {where}'), params)
    directory.update({r["token"]: key_info_from_row(r, deleted=False) for r in live.mappings()})
    return directory


def attributed_user(row_user_id: str | None, info: KeyInfo | None) -> str:
    """Owner a usage row counts toward ("" = no user): the key's owner when the
    key is known, else the user LiteLLM stamped on the row."""
    uid = info.user_id if info is not None else row_user_id
    return uid or ""


async def usage_by_key_and_user(
    litellm_db: AsyncSession,
    *,
    start: str,
    end: str,
    tokens: list[str] | None = None,
) -> list:
    """Sums per (api_key, row user_id) over the date range, optionally limited to ``tokens``."""
    params: dict = {"start": start, "end": end}
    token_filter = ""
    if tokens is not None:
        if not tokens:
            return []
        token_filter = "AND api_key = ANY(:tokens) "
        params["tokens"] = tokens
    result = await litellm_db.execute(
        text(
            f"SELECT api_key, user_id, {USAGE_SUMS_SQL} "
            'FROM "LiteLLM_DailyUserSpend" '
            f"WHERE date >= :start AND date <= :end {token_filter}"
            "GROUP BY api_key, user_id"
        ),
        params,
    )
    return list(result.mappings())


async def usage_by_key_and_model(
    litellm_db: AsyncSession,
    *,
    start: str,
    end: str,
    tokens: list[str],
    user_id: str | None = None,
) -> list:
    """Sums per (api_key, row user_id, model group) for ``tokens`` — plus, when
    ``user_id`` is given, rows LiteLLM stamped with that user (keys no longer
    known anywhere). ``user_id=""`` matches rows without a user."""
    params: dict = {"start": start, "end": end, "tokens": tokens}
    scope = "api_key = ANY(:tokens)"
    if user_id is not None:
        scope = f"({scope} OR COALESCE(user_id, '') = :user_id)"
        params["user_id"] = user_id
    elif not tokens:
        return []
    result = await litellm_db.execute(
        text(
            f"SELECT api_key, user_id, {MODEL_GROUP_SQL} AS model, {USAGE_SUMS_SQL} "
            'FROM "LiteLLM_DailyUserSpend" '
            f"WHERE date >= :start AND date <= :end AND {scope} "
            f"GROUP BY api_key, user_id, {MODEL_GROUP_SQL}"
        ),
        params,
    )
    return list(result.mappings())


def nest_by_key(rows, directory: dict[str, KeyInfo], team_aliases: dict[str, str | None]) -> list[dict]:
    """(api_key, model, sums) rows → keys (by spend) each holding their model groups (by spend)."""
    keys: dict[str, dict] = {}
    for r in rows:
        token = r["api_key"]
        entry = keys.get(token)
        if entry is None:
            info = directory.get(token)
            team_id = info.team_id if info else None
            entry = keys[token] = {
                "api_key": token,
                "alias": info.alias if info else None,
                "team_id": team_id,
                "team_alias": team_aliases.get(team_id) if team_id else None,
                "personal": bool(info and info.personal),
                "sso": bool(info and info.sso),
                "state": "unknown" if info is None else ("deleted" if info.deleted else "active"),
                **empty_sums(),
                "_models": {},
            }
        add_sums(entry, r)
        label = r["model"] or "(unknown)"
        add_sums(entry["_models"].setdefault(label, empty_sums()), r)

    out = []
    for entry in keys.values():
        models = entry.pop("_models")
        entry["models"] = sorted(
            ({"model": name, **sums} for name, sums in models.items()),
            key=lambda m: (-m["spend"], m["model"]),
        )
        out.append(entry)
    out.sort(key=lambda k: (-k["spend"], k["alias"] or "", k["api_key"]))
    return out


async def team_aliases(litellm_db: AsyncSession, team_ids: set[str]) -> dict[str, str | None]:
    if not team_ids:
        return {}
    result = await litellm_db.execute(
        text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" WHERE team_id = ANY(:ids)'),
        {"ids": list(team_ids)},
    )
    return {r["team_id"]: r["team_alias"] for r in result.mappings()}
