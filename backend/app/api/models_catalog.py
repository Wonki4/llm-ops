"""Model catalog CRUD endpoints (Super User only) + public model list."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.api.catalog import _pg_upsert as catalog_pg_upsert, _pg_delete as catalog_pg_delete, _push_to_redis, _remove_from_redis, DEFAULT_CATALOG
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.models.custom_model_status_history import CustomModelStatusHistory
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/models", tags=["models"])


class CreateModelCatalogEntry(BaseModel):
    model_name: str = ""
    display_name: str
    description: str | None = None
    status: ModelStatus = ModelStatus.TESTING
    status_schedule: dict | None = None  # {"testing": "2026-01-15", "lts": "2026-03-01", ...}
    visible: bool = True


class UpdateModelCatalogEntry(BaseModel):
    display_name: str | None = None
    description: str | None = None
    status: ModelStatus | None = None
    status_schedule: dict | None = None
    visible: bool | None = None


def _serialize_model(m: CustomModelCatalog) -> dict:
    return {
        "id": str(m.id),
        "model_name": m.model_name,
        "display_name": m.display_name,
        "description": m.description,
        "status": m.status.value,
        "status_schedule": m.status_schedule,
        "visible": m.visible,
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
        litellm_models = []

    # Merge: catalog entry + LiteLLM runtime info
    models = []
    for lm in litellm_models:
        model_name = lm.get("model_name", "")
        catalog_entry = catalog_map.pop(model_name, None)
        models.append(
            {
                "model_name": model_name,
                "litellm_info": _sanitize_litellm_info(lm),
                "catalog": catalog_entry,
            }
        )

    # Add catalog-only entries (not yet in LiteLLM)
    for name, entry in catalog_map.items():
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
) -> dict:
    """Create a new model catalog entry (Super User only)."""
    # Check uniqueness
    existing = await db.execute(select(CustomModelCatalog).where(CustomModelCatalog.model_name == body.model_name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Model '{body.model_name}' already exists in catalog"
        )

    entry = CustomModelCatalog(
        id=uuid.uuid4(),
        model_name=body.model_name or body.display_name,
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

    # Sync to catalog cache (custom_redis_catalog + Redis)
    cache_data = {"model": entry.model_name}
    await catalog_pg_upsert(db, DEFAULT_CATALOG, entry.display_name, cache_data)
    await _push_to_redis(DEFAULT_CATALOG, entry.display_name, cache_data)

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

    # Sync to catalog cache (custom_redis_catalog + Redis)
    cache_data = {"model": entry.model_name}
    await catalog_pg_upsert(db, DEFAULT_CATALOG, entry.display_name, cache_data)
    await _push_to_redis(DEFAULT_CATALOG, entry.display_name, cache_data)

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

    # Remove from catalog cache (custom_redis_catalog + Redis)
    await catalog_pg_delete(db, DEFAULT_CATALOG, entry.display_name)
    await _remove_from_redis(DEFAULT_CATALOG, entry.display_name)

    return {"deleted": True, "model_name": entry.model_name}


@router.post("/catalog/sync-to-catalog-cache")
async def sync_model_catalog_to_cache(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync all custom_model_catalog entries to custom_redis_catalog + Redis cache."""
    result = await db.execute(select(CustomModelCatalog))
    entries = result.scalars().all()
    synced = 0
    for entry in entries:
        cache_data = {"model": entry.model_name}
        await catalog_pg_upsert(db, DEFAULT_CATALOG, entry.display_name, cache_data)
        await _push_to_redis(DEFAULT_CATALOG, entry.display_name, cache_data)
        synced += 1
    return {"synced": synced}


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
