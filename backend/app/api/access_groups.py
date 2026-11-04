"""Model access groups (Super User only): the groups in ``model_info.access_groups`` and
the users whose personal-key grant (``LiteLLM_UserTable.models``) names them.

Groups are edited through LiteLLM's ``/access_group/*`` API, which only touches DB
models; a group a config.yaml model carries keeps that membership (shown locked).
A user is a member when the group's name is in their grant — the same list the
per-user personal-access editor writes, so both screens stay in step.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db
from app.services.personal_access import (
    ALL_PROXY_MODELS,
    add_grant_entry,
    grant_state,
    normalize_user_id,
    remove_grant_entry,
    validate_group_name,
)
from app.services.user_provisioning import PREGRANT_MARKER

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/access-groups", tags=["access-groups"])


# ─── what /model/info says ──────────────────────────────────────────────────


class _Catalog:
    """Groups and models from LiteLLM ``/model/info`` (one row per deployment)."""

    def __init__(self, rows: list[dict]):
        self.models: dict[str, dict] = {}  # name → {editable: has a DB deployment, access_groups}
        self.groups: dict[str, dict[str, bool]] = {}  # group → {model name: locked}
        self.on_db: set[str] = set()  # groups some DB deployment carries (all LiteLLM's API sees)
        for row in rows or []:
            name = row.get("model_name")
            if not name:
                continue
            info = row.get("model_info") or {}
            is_db = info.get("db_model") is True
            groups = [str(g) for g in info.get("access_groups") or [] if g]
            model = self.models.setdefault(name, {"editable": False, "access_groups": []})
            model["editable"] = model["editable"] or is_db
            for g in groups:
                if g not in model["access_groups"]:
                    model["access_groups"].append(g)
                members = self.groups.setdefault(g, {})
                # locked: a config deployment of this model carries the group (LiteLLM won't untag it)
                members[name] = members.get(name, False) or not is_db
                if is_db:
                    self.on_db.add(g)

    def members(self, group: str) -> dict[str, bool]:
        if group not in self.groups:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"access group {group!r} not found")
        return self.groups[group]

    def locked(self, group: str) -> bool:
        return any(self.groups.get(group, {}).values())


async def _catalog(litellm: LiteLLMClient) -> _Catalog:
    return _Catalog(await litellm.get_model_info())


def _litellm_error(e: httpx.HTTPStatusError) -> HTTPException:
    try:
        detail = e.response.json().get("detail")
        detail = detail.get("error", detail) if isinstance(detail, dict) else detail
    except ValueError:
        detail = e.response.text
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LiteLLM: {detail}")


def _editable_names(catalog: _Catalog, names: list[str]) -> list[str]:
    """``names`` deduplicated; 400 for a name LiteLLM cannot tag (unknown or config-only)."""
    out = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    unknown = [n for n in out if n not in catalog.models]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown models: {', '.join(unknown)}")
    config_only = [n for n in out if not catalog.models[n]["editable"]]
    if config_only:
        raise HTTPException(
            status_code=400,
            detail=f"config.yaml models cannot be added or removed here: {', '.join(config_only)}",
        )
    return out


# ─── users ──────────────────────────────────────────────────────────────────


async def _fetch(session: AsyncSession, sql: str, params: dict | None = None) -> list[dict]:
    result = await session.execute(text(sql), params or {})
    return [dict(r) for r in result.mappings()]


async def _portal_users(db: AsyncSession, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = await _fetch(
        db, "SELECT user_id, display_name, email FROM custom_users WHERE user_id = ANY(:ids)", {"ids": ids}
    )
    return {r["user_id"]: r for r in rows}


async def _grants(litellm_db: AsyncSession, ids: list[str]) -> dict[str, list[str]]:
    if not ids:
        return {}
    rows = await _fetch(
        litellm_db, 'SELECT user_id, models FROM "LiteLLM_UserTable" WHERE user_id = ANY(:ids)', {"ids": ids}
    )
    return {r["user_id"]: list(r["models"] or []) for r in rows}


async def _members(litellm_db: AsyncSession, group: str) -> list[dict]:
    """User rows whose grant names ``group``."""
    sql = 'SELECT user_id, models FROM "LiteLLM_UserTable" WHERE :group = ANY(models) ORDER BY user_id'
    return await _fetch(litellm_db, sql, {"group": group})


def _person(user_id: str, portal: dict[str, dict]) -> dict:
    row = portal.get(user_id) or {}
    return {
        "user_id": user_id,
        "display_name": row.get("display_name"),
        "email": row.get("email"),
        "logged_in": user_id in portal,
    }


# ─── endpoints ──────────────────────────────────────────────────────────────


@router.get("")
async def list_access_groups(
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Every group (with its models, locked ones marked) and every model (editable = has a DB deployment)."""
    catalog = await _catalog(litellm)
    sql = 'SELECT entry, COUNT(*) AS n FROM "LiteLLM_UserTable", unnest(models) AS entry GROUP BY entry'
    counts = {r["entry"]: r["n"] for r in await _fetch(litellm_db, sql)}
    groups = [
        {
            "name": name,
            "models": [{"model_name": m, "locked": locked} for m, locked in sorted(members.items())],
            "locked": catalog.locked(name),
            "user_count": int(counts.get(name, 0)),
        }
        for name, members in sorted(catalog.groups.items())
    ]
    models = [{"model_name": name, **info} for name, info in sorted(catalog.models.items())]
    return {"groups": groups, "models": models}


@router.get("/model-users")
async def model_users(
    model: str,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Users whose personal-key grant reaches ``model``: by name, through a group, or "all"."""
    catalog = await _catalog(litellm)
    groups = list((catalog.models.get(model) or {}).get("access_groups") or [])
    names = [model, *groups, ALL_PROXY_MODELS]
    sql = 'SELECT user_id, models FROM "LiteLLM_UserTable" WHERE models && CAST(:names AS text[]) ORDER BY user_id'
    rows = await _fetch(litellm_db, sql, {"names": names})
    portal = await _portal_users(db, [r["user_id"] for r in rows])
    users = []
    for r in rows:
        held = set(r["models"] or [])
        via = (["direct"] if model in held else []) + (["all"] if ALL_PROXY_MODELS in held else [])
        users.append({**_person(r["user_id"], portal), "via": via + [g for g in groups if g in held]})
    return {"model": model, "access_groups": groups, "users": users}


@router.get("/{group}/users")
async def group_users(
    group: str,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """The users whose grant names ``group``."""
    (await _catalog(litellm)).members(group)
    rows = await _members(litellm_db, group)
    ids = [r["user_id"] for r in rows]
    portal = await _portal_users(db, ids)
    keys: dict[str, int] = {}
    if ids:
        sql = (
            'SELECT user_id, COUNT(*) AS n FROM "LiteLLM_VerificationToken" '
            "WHERE team_id IS NULL AND user_id = ANY(:ids) GROUP BY user_id"
        )
        keys = {r["user_id"]: int(r["n"]) for r in await _fetch(litellm_db, sql, {"ids": ids})}
    users = [
        {
            **_person(r["user_id"], portal),
            "personal_access": grant_state(list(r["models"] or [])),
            "personal_key_count": keys.get(r["user_id"], 0),
        }
        for r in rows
    ]
    return {"group": group, "users": users}


class CreateGroupRequest(BaseModel):
    name: str
    model_names: list[str]


@router.post("")
async def create_access_group(
    body: CreateGroupRequest,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    catalog = await _catalog(litellm)
    name = body.name.strip()
    if name in catalog.groups:
        raise HTTPException(status_code=409, detail=f"access group {name!r} already exists")
    try:
        validate_group_name(name, model_names=set(catalog.models))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    models = _editable_names(catalog, body.model_names)
    if not models:
        raise HTTPException(status_code=400, detail="an access group needs at least one model")
    try:
        await litellm.create_access_group(name, models)
    except httpx.HTTPStatusError as e:
        raise _litellm_error(e)
    return {"name": name, "model_names": models}


class UpdateGroupRequest(BaseModel):
    model_names: list[str]


@router.put("/{group}")
async def update_access_group(
    group: str,
    body: UpdateGroupRequest,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """The group's models become ``model_names``. Locked (config) members must stay listed."""
    catalog = await _catalog(litellm)
    members = catalog.members(group)
    locked = [m for m, is_locked in members.items() if is_locked]
    wanted = list(dict.fromkeys(n.strip() for n in body.model_names if n and n.strip()))
    dropped = [m for m in locked if m not in wanted]
    if dropped:
        raise HTTPException(
            status_code=400, detail=f"config.yaml models stay in the group: {', '.join(sorted(dropped))}"
        )
    # What LiteLLM tags: every wanted model with a DB deployment (a locked model may have one too).
    _editable_names(catalog, [n for n in wanted if n not in locked])
    db_models = [n for n in wanted if catalog.models[n]["editable"]]
    if not wanted:
        raise HTTPException(
            status_code=400, detail="an access group needs at least one model; delete the group instead"
        )
    try:
        if db_models:
            # LiteLLM only sees groups on DB models: one carried by config models alone is "new" to it.
            if group in catalog.on_db:
                await litellm.update_access_group(group, db_models)
            else:
                await litellm.create_access_group(group, db_models)
        elif group in catalog.on_db:
            await litellm.delete_access_group(group)  # untags the DB models; the config ones keep it
    except httpx.HTTPStatusError as e:
        raise _litellm_error(e)
    return {"name": group, "model_names": wanted}


@router.delete("/{group}")
async def delete_access_group(
    group: str,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Remove the group from its models, then from every user's grant (a deleted name in a
    grant would come back to life if a group of that name were created again)."""
    catalog = await _catalog(litellm)
    catalog.members(group)
    if catalog.locked(group):
        raise HTTPException(
            status_code=409, detail="config.yaml models carry this group; remove it from config.yaml first"
        )
    try:
        await litellm.delete_access_group(group)
    except httpx.HTTPStatusError as e:
        raise _litellm_error(e)
    revoked, failed = 0, []
    for r in await _members(litellm_db, group):
        models = remove_grant_entry(list(r["models"] or []), group)
        if models is None:
            continue
        try:
            await litellm.update_user(r["user_id"], models=models)
            revoked += 1
        except Exception:
            logger.exception("Failed to revoke access group %s from %s", group, r["user_id"])
            failed.append(r["user_id"])
    return {"name": group, "revoked_users": revoked, "failed_users": failed}


class GroupUsersRequest(BaseModel):
    user_ids: list[str]
    dry_run: bool = False  # classify only (the add dialog's preview)


def _parse_ids(raw_ids: list[str]) -> list[tuple[str, str | None]]:
    """(input, canonical id or None) per distinct non-blank input, first occurrence kept."""
    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    for raw in raw_ids:
        value = (raw or "").strip()
        if not value:
            continue
        uid = normalize_user_id(value)
        key = uid or value
        if key in seen:
            continue
        seen.add(key)
        out.append((value, uid))
    return out


@router.post("/{group}/users")
async def add_group_users(
    group: str,
    body: GroupUsersRequest,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Grant ``group`` to each user. A user with no LiteLLM row yet (never signed in) gets one
    now, marked so their first login still places them in the default teams."""
    (await _catalog(litellm)).members(group)
    parsed = _parse_ids(body.user_ids)
    ids = [uid for _, uid in parsed if uid]
    grants = await _grants(litellm_db, ids)
    portal = await _portal_users(db, ids)
    results = []
    for raw, uid in parsed:
        person = _person(uid, portal) if uid else {"logged_in": False, "display_name": None}
        entry = {"input": raw, "user_id": uid, "logged_in": person["logged_in"], "display_name": person["display_name"]}
        if uid is None:
            results.append({**entry, "status": "invalid"})
            continue
        if uid in grants:
            models = add_grant_entry(grants[uid], group)
            status_ = "already" if models is None else "added"
        else:
            models, status_ = [group], "pregranted"
        if not body.dry_run and status_ != "already":
            try:
                if status_ == "added":
                    await litellm.update_user(uid, models=models)
                else:
                    email = (portal.get(uid) or {}).get("email")
                    await litellm.create_user(uid, email, models=models, metadata={PREGRANT_MARKER: True})
            except Exception:
                logger.exception("Failed to grant access group %s to %s", group, uid)
                status_ = "error"
        results.append({**entry, "status": status_})
    return {"group": group, "dry_run": body.dry_run, "results": results}


@router.post("/{group}/users/remove")
async def remove_group_users(
    group: str,
    body: GroupUsersRequest,
    _admin: CustomUser = Depends(require_super_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Take ``group`` out of each user's grant (nothing left → no personal access)."""
    (await _catalog(litellm)).members(group)
    parsed = _parse_ids(body.user_ids)
    grants = await _grants(litellm_db, [uid for _, uid in parsed if uid])
    results = []
    for raw, uid in parsed:
        models = remove_grant_entry(grants.get(uid), group) if uid else None
        status_ = "invalid" if uid is None else "not_member" if models is None else "removed"
        if status_ == "removed" and not body.dry_run:
            try:
                await litellm.update_user(uid, models=models)
            except Exception:
                logger.exception("Failed to remove access group %s from %s", group, uid)
                status_ = "error"
        results.append({"input": raw, "user_id": uid, "status": status_})
    return {"group": group, "dry_run": body.dry_run, "results": results}
