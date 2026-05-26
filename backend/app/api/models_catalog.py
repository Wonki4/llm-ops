"""Model catalog CRUD endpoints (Super User only) + public model list."""

import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.clients.redis import (
    catalog_delete as redis_delete,
    catalog_get as redis_get,
    catalog_set as redis_set,
)
from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.models.custom_model_cost_schedule import CustomModelCostSchedule
from app.db.models.custom_model_status_history import CustomModelStatusHistory
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db

router = APIRouter(prefix="/api/models", tags=["models"])


class CreateModelCatalogEntry(BaseModel):
    model_name: str
    display_name: str
    description: str | None = None
    status: ModelStatus = ModelStatus.TESTING
    status_schedule: dict | None = None  # {"testing": "2026-01-15", "lts": "2026-03-01", ...}
    visible: bool = True
    is_external: bool = False  # True: catalog-only (skip LiteLLM existence check)


class UpdateModelCatalogEntry(BaseModel):
    display_name: str | None = None
    description: str | None = None
    status: ModelStatus | None = None
    status_schedule: dict | None = None
    visible: bool | None = None
    default_input_cost_per_token: float | None = None
    default_output_cost_per_token: float | None = None


def _serialize_model(m: CustomModelCatalog) -> dict:
    return {
        "id": str(m.id),
        "model_name": m.model_name,
        "display_name": m.display_name,
        "description": m.description,
        "status": m.status.value,
        "status_schedule": m.status_schedule,
        "visible": m.visible,
        "default_input_cost_per_token": m.default_input_cost_per_token,
        "default_output_cost_per_token": m.default_output_cost_per_token,
        "status_change_date": m.status_change_date.isoformat() if m.status_change_date else None,
        "created_by": m.created_by,
        "updated_by": m.updated_by,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


# Fields to strip from litellm_params (may contain secrets)
SENSITIVE_LITELLM_FIELDS = {"api_key", "api_base", "api_version", "custom_llm_provider"}


def _sanitize_litellm_info(lm: dict) -> dict:
    """Remove sensitive fields from LiteLLM model info before sending to frontend."""
    sanitized = dict(lm)
    params = sanitized.get("litellm_params")
    if isinstance(params, dict):
        sanitized["litellm_params"] = {k: v for k, v in params.items() if k not in SENSITIVE_LITELLM_FIELDS}
    return sanitized


@router.get("")
async def list_models(
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all models (merged LiteLLM + catalog)."""
    # Get catalog entries from custom DB
    result = await db.execute(select(CustomModelCatalog).order_by(CustomModelCatalog.display_name))
    catalog = result.scalars().all()
    catalog_map = {m.model_name: _serialize_model(m) for m in catalog}

    # Get LiteLLM model info for runtime data
    try:
        litellm_models = await litellm.get_model_info()
    except Exception:
        logger.error("Failed to fetch LiteLLM model info", exc_info=True)
        litellm_models = []

    is_admin = user.global_role == GlobalRole.SUPER_USER

    # Merge: catalog entry + LiteLLM runtime info
    models = []
    for lm in litellm_models:
        model_name = lm.get("model_name", "")
        catalog_entry = catalog_map.pop(model_name, None)
        # Non-admin: skip models without catalog or hidden models
        if not is_admin:
            if not catalog_entry:
                continue
            if catalog_entry.get("visible") is False:
                continue
        models.append(
            {
                "model_name": model_name,
                "litellm_info": _sanitize_litellm_info(lm),
                "catalog": catalog_entry,
            }
        )

    # Add catalog-only entries (not yet in LiteLLM)
    for name, entry in catalog_map.items():
        if not is_admin and entry.get("visible") is False:
            continue
        models.append(
            {
                "model_name": name,
                "litellm_info": None,
                "catalog": entry,
            }
        )

    return {"models": models}


@router.get("/catalog")
async def list_catalog(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all catalog entries."""
    result = await db.execute(select(CustomModelCatalog).order_by(CustomModelCatalog.display_name))
    return {"catalog": [_serialize_model(m) for m in result.scalars().all()]}


@router.get("/catalog/history")
async def list_all_history(
    model_name: str | None = None,
    status_filter: str | None = None,
    changed_by: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all status change history across all catalog entries."""
    query = select(CustomModelStatusHistory)

    # Non-admin: exclude history for hidden models
    if user.global_role != GlobalRole.SUPER_USER:
        visible_models_q = select(CustomModelCatalog.model_name).where(CustomModelCatalog.visible == True)  # noqa: E712
        query = query.where(CustomModelStatusHistory.model_name.in_(visible_models_q))

    if model_name:
        query = query.where(CustomModelStatusHistory.model_name.ilike(f"%{model_name}%"))
    if status_filter:
        query = query.where(CustomModelStatusHistory.new_status == status_filter)
    if changed_by:
        query = query.where(CustomModelStatusHistory.changed_by == changed_by)
    if date_from:
        query = query.where(CustomModelStatusHistory.changed_at >= date_from)
    if date_to:
        query = query.where(CustomModelStatusHistory.changed_at < date_to)

    # Total count for pagination
    count_query = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Fetch page
    query = query.order_by(CustomModelStatusHistory.changed_at.desc()).offset(offset).limit(min(limit, 500))
    result = await db.execute(query)
    history = result.scalars().all()

    return {
        "history": [
            {
                "id": str(h.id),
                "catalog_id": str(h.catalog_id),
                "model_name": h.model_name,
                "previous_status": h.previous_status.value if h.previous_status else None,
                "new_status": h.new_status.value,
                "changed_by": h.changed_by,
                "comment": h.comment,
                "changed_at": h.changed_at.isoformat() if h.changed_at else None,
            }
            for h in history
        ],
        "total": total,
    }


@router.get("/catalog/history/summary")
async def history_summary(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    bucket: str = "month",
    top_n: int = 5,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Aggregated statistics for model status changes within a date range."""
    # Build shared WHERE conditions
    conditions = []
    if date_from:
        conditions.append(CustomModelStatusHistory.changed_at >= date_from)
    if date_to:
        conditions.append(CustomModelStatusHistory.changed_at < date_to)

    # a) Total changes
    total_q = select(sa_func.count()).where(*conditions).select_from(CustomModelStatusHistory)
    total_changes = (await db.execute(total_q)).scalar() or 0

    # b) Unique models
    unique_q = (
        select(sa_func.count(sa_func.distinct(CustomModelStatusHistory.model_name)))
        .where(*conditions)
        .select_from(CustomModelStatusHistory)
    )
    unique_models = (await db.execute(unique_q)).scalar() or 0

    # c) Changes grouped by new_status
    status_q = (
        select(
            CustomModelStatusHistory.new_status,
            sa_func.count().label("cnt"),
        )
        .where(*conditions)
        .group_by(CustomModelStatusHistory.new_status)
    )
    status_rows = (await db.execute(status_q)).all()
    to_status = {row.new_status.value: row.cnt for row in status_rows}

    # d) Time series (bucket-based trend)
    if bucket == "day":
        trunc_expr = sa_func.date_trunc("day", CustomModelStatusHistory.changed_at)
    else:
        trunc_expr = sa_func.date_trunc("month", CustomModelStatusHistory.changed_at)

    series_q = (
        select(
            trunc_expr.label("bucket"),
            sa_func.count().label("cnt"),
        )
        .where(*conditions)
        .group_by(sa_text("1"))
        .order_by(sa_text("1"))
    )
    series_rows = (await db.execute(series_q)).all()
    series = [
        {"bucket": row.bucket.isoformat() if row.bucket else None, "count": row.cnt}
        for row in series_rows
    ]

    # e) Transitions (from -> to status pairs)
    trans_q = (
        select(
            CustomModelStatusHistory.previous_status,
            CustomModelStatusHistory.new_status,
            sa_func.count().label("cnt"),
        )
        .where(*conditions)
        .group_by(
            CustomModelStatusHistory.previous_status,
            CustomModelStatusHistory.new_status,
        )
        .order_by(sa_func.count().desc())
    )
    trans_rows = (await db.execute(trans_q)).all()
    transitions = [
        {
            "from_status": row.previous_status.value if row.previous_status else None,
            "to_status": row.new_status.value,
            "count": row.cnt,
        }
        for row in trans_rows
    ]

    # f) Top N most changed models
    top_q = (
        select(
            CustomModelStatusHistory.model_name,
            sa_func.count().label("cnt"),
        )
        .where(*conditions)
        .group_by(CustomModelStatusHistory.model_name)
        .order_by(sa_func.count().desc())
        .limit(top_n)
    )
    top_rows = (await db.execute(top_q)).all()
    top_models = [{"model_name": row.model_name, "count": row.cnt} for row in top_rows]

    return {
        "total_changes": total_changes,
        "unique_models": unique_models,
        "to_status": to_status,
        "series": series,
        "transitions": transitions,
        "top_models": top_models,
    }

@router.post("/catalog", status_code=status.HTTP_201_CREATED)
async def create_catalog_entry(
    body: CreateModelCatalogEntry,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Create a new model catalog entry (Super User only).

    For LiteLLM-typed entries (is_external=False), model_name must match a
    deployment registered in LiteLLM so catalog and routing stay aligned by
    name. External entries skip this check (catalog-only docs for non-routed
    models such as offline tools or upcoming releases).
    """
    model_name = body.model_name.strip()
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_name is required",
        )

    if not body.is_external:
        try:
            litellm_models = await litellm.get_model_info()
        except Exception:
            logger.exception("Failed to fetch LiteLLM models for catalog validation")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="LiteLLM model list unavailable; cannot validate model_name",
            )
        litellm_names = {m.get("model_name") for m in litellm_models if m.get("model_name")}
        if model_name not in litellm_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{model_name}' is not registered in LiteLLM. "
                "Register it in LiteLLM first, or mark this entry as 외부.",
            )

    # Check uniqueness
    existing = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.model_name == model_name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Model '{model_name}' already exists in catalog"
        )

    entry = CustomModelCatalog(
        id=uuid.uuid4(),
        model_name=model_name,
        display_name=body.display_name,
        description=body.description,
        status=body.status,
        status_schedule=body.status_schedule,
        visible=body.visible,
        status_change_date=datetime.now() if body.status != ModelStatus.TESTING else None,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)

    # Record initial status in history
    history = CustomModelStatusHistory(
        id=uuid.uuid4(),
        catalog_id=entry.id,
        model_name=entry.model_name,
        previous_status=None,
        new_status=entry.status,
        changed_by=user.user_id,
    )
    db.add(history)

    return _serialize_model(entry)


@router.put("/catalog/{catalog_id}")
async def update_catalog_entry(
    catalog_id: str,
    body: UpdateModelCatalogEntry,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a model catalog entry (Super User only)."""
    result = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.id == uuid.UUID(catalog_id)))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog entry not found")

    update_data = body.model_dump(exclude_unset=True)

    # Track status changes with history
    previous_status = entry.status
    status_changed = "status" in update_data and update_data["status"] != entry.status
    if status_changed:
        entry.status_change_date = datetime.now()

    for field, value in update_data.items():
        setattr(entry, field, value)

    entry.updated_by = user.user_id
    await db.flush()

    # Record status change in history
    if status_changed:
        history = CustomModelStatusHistory(
            id=uuid.uuid4(),
            catalog_id=entry.id,
            model_name=entry.model_name,
            previous_status=previous_status,
            new_status=entry.status,
            changed_by=user.user_id,
        )
        db.add(history)
        await db.flush()

    await db.refresh(entry)

    return _serialize_model(entry)


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_entry(
    catalog_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a model catalog entry (Super User only)."""
    result = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.id == uuid.UUID(catalog_id)))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog entry not found")

    await db.delete(entry)

    return {"deleted": True, "model_name": entry.model_name}


@router.get("/catalog/{catalog_id}/history")
async def get_catalog_history(
    catalog_id: str,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get status change history for a catalog entry."""
    # Verify catalog entry exists
    result = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.id == uuid.UUID(catalog_id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog entry not found")

    # Fetch history ordered by newest first
    history_result = await db.execute(
        select(CustomModelStatusHistory)
        .where(CustomModelStatusHistory.catalog_id == uuid.UUID(catalog_id))
        .order_by(CustomModelStatusHistory.changed_at.desc())
    )
    history = history_result.scalars().all()

    return {
        "history": [
            {
                "id": str(h.id),
                "catalog_id": str(h.catalog_id),
                "model_name": h.model_name,
                "previous_status": h.previous_status.value if h.previous_status else None,
                "new_status": h.new_status.value,
                "changed_by": h.changed_by,
                "comment": h.comment,
                "changed_at": h.changed_at.isoformat() if h.changed_at else None,
            }
            for h in history
        ]
    }


# ─── Per-model Redis cache entries ──────────────────────────────
#
# Each model can have a Redis-backed cache entry per allowed suffix
# (chat, hcp, common, ...). The Redis hash key uses model_name directly,
# so a model is uniquely keyed by (suffix, model_name). Same model in
# the same suffix is one entry — saving overwrites.


DEFAULT_CATALOG = "chat"


async def _get_allowed_suffixes(db: AsyncSession) -> list[str]:
    """Read configured catalog suffixes from portal settings."""
    result = await db.execute(
        sa_text("SELECT value FROM custom_portal_settings WHERE key = 'catalog_suffixes'")
    )
    raw = result.scalar()
    import json as _json
    return _json.loads(raw) if raw else [DEFAULT_CATALOG]


class ModelCacheEntry(BaseModel):
    model: str = ""
    apiBase: str = ""
    apiKey: str = ""
    options: dict = {}

    class Config:
        extra = "allow"


@router.get("/{model_name}/cache")
async def get_model_cache(
    model_name: str,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return per-suffix cache entries for a model. Missing suffixes return null."""
    suffixes = await _get_allowed_suffixes(db)
    entries: dict[str, dict | None] = {}
    for suffix in suffixes:
        entries[suffix] = await redis_get(suffix, model_name)
    return {"model_name": model_name, "suffixes": suffixes, "entries": entries}


@router.put("/{model_name}/cache/{suffix}")
async def set_model_cache_entry(
    model_name: str,
    suffix: str,
    body: ModelCacheEntry,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set a model's cache entry for a specific suffix (Super User only)."""
    suffixes = await _get_allowed_suffixes(db)
    if suffix not in suffixes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않은 suffix입니다: '{suffix}'. 허용 목록: {suffixes}",
        )
    data = body.model_dump()
    await redis_set(suffix, model_name, data)
    return {"model_name": model_name, "suffix": suffix, **data}


@router.delete("/{model_name}/cache/{suffix}")
async def delete_model_cache_entry(
    model_name: str,
    suffix: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a model's cache entry for a specific suffix (Super User only)."""
    deleted = await redis_delete(suffix, model_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cache entry not found")
    return {"deleted": True, "model_name": model_name, "suffix": suffix}


# ─── Per-model cost schedule (time-of-day pricing) ──────────────


def _serialize_cost_schedule(r: CustomModelCostSchedule) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "days_of_week": list(r.days_of_week),
        "hour_start_utc": r.hour_start_utc,
        "hour_end_utc": r.hour_end_utc,
        "input_cost_per_token": r.input_cost_per_token,
        "output_cost_per_token": r.output_cost_per_token,
        "priority": r.priority,
        "enabled": r.enabled,
        "created_by": r.created_by,
        "updated_by": r.updated_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


class CostScheduleRequest(BaseModel):
    days_of_week: list[int]  # 1..7 ISO weekdays
    hour_start_utc: int  # 0..23
    hour_end_utc: int  # 1..24; <= start means day-spanning
    input_cost_per_token: float
    output_cost_per_token: float
    priority: int = 0
    enabled: bool = True


def _validate_cost_schedule(body: CostScheduleRequest) -> None:
    if not body.days_of_week or any(d < 1 or d > 7 for d in body.days_of_week):
        raise HTTPException(status_code=400, detail="days_of_week must be non-empty ints in 1..7")
    if body.hour_start_utc < 0 or body.hour_start_utc > 23:
        raise HTTPException(status_code=400, detail="hour_start_utc must be in 0..23")
    if body.hour_end_utc < 1 or body.hour_end_utc > 24:
        raise HTTPException(status_code=400, detail="hour_end_utc must be in 1..24")
    if body.hour_start_utc == body.hour_end_utc:
        raise HTTPException(status_code=400, detail="hour_start_utc and hour_end_utc must differ")
    if body.input_cost_per_token < 0 or body.output_cost_per_token < 0:
        raise HTTPException(status_code=400, detail="costs must be non-negative")


@router.get("/{model_name}/cost-schedule")
async def list_cost_schedule(
    model_name: str,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List time-of-day cost rules for a model."""
    result = await db.execute(
        select(CustomModelCostSchedule)
        .where(CustomModelCostSchedule.model_name == model_name)
        .order_by(CustomModelCostSchedule.priority.desc(), CustomModelCostSchedule.created_at)
    )
    return {"model_name": model_name, "rules": [_serialize_cost_schedule(r) for r in result.scalars().all()]}


async def _snapshot_default_cost_if_missing(
    db: AsyncSession,
    litellm: LiteLLMClient,
    model_name: str,
) -> None:
    """Capture the current LiteLLM cost as the catalog default before saving a rule.

    The cost-schedule worker reverts to `catalog.default_*_cost_per_token` when
    no rule is active. If the admin never set those, the worker's revert path
    silently skips and the model stays stuck on whatever the last active rule
    set. Snapshotting on rule create/update closes that gap automatically.

    Idempotent: only fills the side that is currently null, so an admin's
    explicit catalog default always wins. No-op when the catalog row doesn't
    exist or LiteLLM has no cost for this model.
    """
    result = await db.execute(
        select(CustomModelCatalog).where(CustomModelCatalog.model_name == model_name)
    )
    catalog = result.scalar_one_or_none()
    if catalog is None:
        return
    in_set = catalog.default_input_cost_per_token is not None
    out_set = catalog.default_output_cost_per_token is not None
    if in_set and out_set:
        return
    try:
        deployments = await litellm.get_model_info()
    except Exception:
        logger.exception(
            "cost-schedule: failed to fetch LiteLLM model_info for default snapshot of %s",
            model_name,
        )
        return
    for d in deployments:
        if d.get("model_name") != model_name:
            continue
        params = d.get("litellm_params") or {}
        info = d.get("model_info") or {}
        in_cost = params.get("input_cost_per_token") or info.get("input_cost_per_token")
        out_cost = params.get("output_cost_per_token") or info.get("output_cost_per_token")
        if not in_set and in_cost is not None:
            catalog.default_input_cost_per_token = float(in_cost)
        if not out_set and out_cost is not None:
            catalog.default_output_cost_per_token = float(out_cost)
        await db.flush()
        return


@router.post("/{model_name}/cost-schedule", status_code=status.HTTP_201_CREATED)
async def create_cost_schedule(
    model_name: str,
    body: CostScheduleRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Create a cost schedule rule for a model (Super User only)."""
    _validate_cost_schedule(body)
    await _snapshot_default_cost_if_missing(db, litellm, model_name)
    rule = CustomModelCostSchedule(
        id=uuid.uuid4(),
        model_name=model_name,
        days_of_week=sorted(set(body.days_of_week)),
        hour_start_utc=body.hour_start_utc,
        hour_end_utc=body.hour_end_utc,
        input_cost_per_token=body.input_cost_per_token,
        output_cost_per_token=body.output_cost_per_token,
        priority=body.priority,
        enabled=body.enabled,
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return _serialize_cost_schedule(rule)


@router.put("/cost-schedule/{rule_id}")
async def update_cost_schedule(
    rule_id: str,
    body: CostScheduleRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Update a cost schedule rule (Super User only)."""
    _validate_cost_schedule(body)
    result = await db.execute(select(CustomModelCostSchedule).where(CustomModelCostSchedule.id == uuid.UUID(rule_id)))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await _snapshot_default_cost_if_missing(db, litellm, rule.model_name)
    rule.days_of_week = sorted(set(body.days_of_week))
    rule.hour_start_utc = body.hour_start_utc
    rule.hour_end_utc = body.hour_end_utc
    rule.input_cost_per_token = body.input_cost_per_token
    rule.output_cost_per_token = body.output_cost_per_token
    rule.priority = body.priority
    rule.enabled = body.enabled
    rule.updated_by = user.user_id
    await db.flush()
    await db.refresh(rule)
    return _serialize_cost_schedule(rule)


@router.delete("/cost-schedule/{rule_id}")
async def delete_cost_schedule(
    rule_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a cost schedule rule (Super User only)."""
    result = await db.execute(select(CustomModelCostSchedule).where(CustomModelCostSchedule.id == uuid.UUID(rule_id)))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    return {"deleted": True, "id": rule_id}
