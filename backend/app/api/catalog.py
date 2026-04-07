"""PostgreSQL-backed model catalog management with Redis cache."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.clients.redis import catalog_delete as redis_delete
from app.clients.redis import catalog_set as redis_set
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

DEFAULT_CATALOG = "chat"


async def _get_allowed_catalogs(db: AsyncSession) -> list[str]:
    """Get allowed catalog suffixes from portal settings."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'catalog_suffixes'")
    )
    raw = result.scalar()
    return json.loads(raw) if raw else [DEFAULT_CATALOG]


async def _validate_catalog(catalog: str, db: AsyncSession) -> str:
    """Validate catalog suffix against allowlist. Raises 400 if invalid."""
    allowed = await _get_allowed_catalogs(db)
    if catalog not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 카탈로그입니다: '{catalog}'. 허용 목록: {allowed}",
        )
    return catalog


class CatalogEntry(BaseModel):
    model: str = ""
    apiBase: str = ""
    apiKey: str = ""
    options: dict[str, Any] = {}

    class Config:
        extra = "allow"


class CatalogCreateRequest(BaseModel):
    display_name: str
    entry: CatalogEntry


class CatalogUpdateRequest(BaseModel):
    entry: CatalogEntry | None = None
    new_display_name: str | None = None


# ─── PG helpers ───────────────────────────────────────────────

async def _pg_get_all(db: AsyncSession, catalog: str) -> dict[str, dict]:
    """Get all catalog entries from PostgreSQL."""
    prefix = f"{catalog}:"
    result = await db.execute(
        text("SELECT display_name, data FROM custom_redis_catalog WHERE display_name LIKE :prefix ORDER BY display_name"),
        {"prefix": f"{prefix}%"},
    )
    entries = {}
    for row in result.mappings():
        name = row["display_name"].removeprefix(prefix)
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
        entries[name] = data
    return entries


async def _pg_get(db: AsyncSession, catalog: str, display_name: str) -> dict | None:
    """Get a single catalog entry from PostgreSQL."""
    result = await db.execute(
        text("SELECT data FROM custom_redis_catalog WHERE display_name = :name"),
        {"name": f"{catalog}:{display_name}"},
    )
    raw = result.scalar()
    if raw is None:
        return None
    return raw if isinstance(raw, dict) else json.loads(raw)


async def _pg_upsert(db: AsyncSession, catalog: str, display_name: str, data: dict) -> None:
    """Upsert catalog entry to PostgreSQL."""
    await db.execute(
        text("""
            INSERT INTO custom_redis_catalog (display_name, data, updated_at)
            VALUES (:name, CAST(:data AS jsonb), NOW())
            ON CONFLICT (display_name) DO UPDATE
            SET data = CAST(:data AS jsonb), updated_at = NOW()
        """),
        {"name": f"{catalog}:{display_name}", "data": json.dumps(data, ensure_ascii=False)},
    )


async def _pg_delete(db: AsyncSession, catalog: str, display_name: str) -> bool:
    """Delete catalog entry from PostgreSQL. Returns True if deleted."""
    result = await db.execute(
        text("DELETE FROM custom_redis_catalog WHERE display_name = :name"),
        {"name": f"{catalog}:{display_name}"},
    )
    return result.rowcount > 0


async def _push_to_redis(catalog: str, display_name: str, data: dict) -> None:
    """Push catalog entry to Redis cache. Logs errors but doesn't raise."""
    try:
        await redis_set(catalog, display_name, data)
    except Exception:
        logger.warning("Failed to push %s:%s to Redis cache", catalog, display_name, exc_info=True)


async def _remove_from_redis(catalog: str, display_name: str) -> None:
    """Remove catalog entry from Redis cache. Logs errors but doesn't raise."""
    try:
        await redis_delete(catalog, display_name)
    except Exception:
        logger.warning("Failed to remove %s:%s from Redis cache", catalog, display_name, exc_info=True)


# ─── Catalog suffix management ──────────────────────────────────

@router.get("/catalogs")
async def list_catalogs(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List configured catalog suffixes."""
    result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'catalog_suffixes'")
    )
    raw = result.scalar()
    suffixes = json.loads(raw) if raw else [DEFAULT_CATALOG]
    return {"catalogs": suffixes}


@router.put("/catalogs")
async def update_catalogs(
    body: list[str],
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update catalog suffix list (Super User only)."""
    if not body:
        raise HTTPException(status_code=400, detail="최소 1개의 카탈로그가 필요합니다.")
    await db.execute(
        text(
            "INSERT INTO custom_portal_settings (key, value, updated_by) "
            "VALUES ('catalog_suffixes', :value, :user_id) "
            "ON CONFLICT (key) DO UPDATE SET value = :value, updated_by = :user_id"
        ),
        {"value": json.dumps(body), "user_id": user.user_id},
    )
    return {"catalogs": body}


# ─── CRUD ──────────────────────────────────────────────────────

@router.get("")
async def list_catalog(
    catalog: str = Query(DEFAULT_CATALOG, description="Catalog suffix (e.g. chat, hcp)"),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all catalog entries from PostgreSQL."""
    await _validate_catalog(catalog, db)
    entries = await _pg_get_all(db, catalog)
    return {
        "catalog": catalog,
        "entries": [
            {"display_name": name, **data}
            for name, data in entries.items()
        ],
        "total": len(entries),
    }


@router.get("/entry/{display_name}")
async def get_catalog_entry(
    display_name: str,
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a single catalog entry from PostgreSQL."""
    await _validate_catalog(catalog, db)
    data = await _pg_get(db, catalog, display_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return {"display_name": display_name, "catalog": catalog, **data}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_catalog_entry(
    body: CatalogCreateRequest,
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new catalog entry (Super User only)."""
    await _validate_catalog(catalog, db)
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required")

    existing = await _pg_get(db, catalog, body.display_name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"'{body.display_name}' already exists")

    data = body.entry.model_dump()
    await _pg_upsert(db, catalog, body.display_name, data)
    await _push_to_redis(catalog, body.display_name, data)
    return {"display_name": body.display_name, "catalog": catalog, **data}


@router.put("/entry/{display_name}")
async def update_catalog_entry(
    display_name: str,
    body: CatalogUpdateRequest,
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a catalog entry (Super User only)."""
    await _validate_catalog(catalog, db)
    existing = await _pg_get(db, catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    if body.entry is not None:
        data = body.entry.model_dump()
    else:
        data = existing

    target_name = display_name
    if body.new_display_name and body.new_display_name != display_name:
        conflict = await _pg_get(db, catalog, body.new_display_name)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"'{body.new_display_name}' already exists")
        await _pg_delete(db, catalog, display_name)
        await _remove_from_redis(catalog, display_name)
        target_name = body.new_display_name

    await _pg_upsert(db, catalog, target_name, data)
    await _push_to_redis(catalog, target_name, data)
    return {"display_name": target_name, "catalog": catalog, **data}


@router.delete("/entry/{display_name}")
async def delete_catalog_entry(
    display_name: str,
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a catalog entry (Super User only)."""
    await _validate_catalog(catalog, db)
    deleted = await _pg_delete(db, catalog, display_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    await _remove_from_redis(catalog, display_name)
    return {"deleted": True, "display_name": display_name, "catalog": catalog}


@router.patch("/entry/{display_name}/apikey")
async def update_user_api_key(
    display_name: str,
    body: dict,
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Allow a user to set their own apiKey for a catalog entry."""
    await _validate_catalog(catalog, db)
    existing = await _pg_get(db, catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    api_key = body.get("apiKey", "")
    user_keys: dict = existing.get("userApiKeys", {})
    user_keys[user.user_id] = api_key
    existing["userApiKeys"] = user_keys
    await _pg_upsert(db, catalog, display_name, existing)
    await _push_to_redis(catalog, display_name, existing)

    return {"display_name": display_name, "apiKey": api_key}


# ─── Sync ──────────────────────────────────────────────────────

@router.post("/sync-to-redis")
async def sync_to_redis(
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync model catalog → redis catalog cache → Redis (Super User only).

    1. Pull custom_model_catalog entries into custom_redis_catalog (backfill)
    2. Push all custom_redis_catalog entries to Redis
    """
    await _validate_catalog(catalog, db)

    # Step 1: Backfill from custom_model_catalog → custom_redis_catalog
    model_result = await db.execute(
        text("SELECT model_name, display_name FROM custom_model_catalog")
    )
    backfilled = 0
    for row in model_result.mappings():
        cache_data = {"model": row["model_name"]}
        await _pg_upsert(db, catalog, row["display_name"], cache_data)
        backfilled += 1

    # Step 2: Push all custom_redis_catalog → Redis
    entries = await _pg_get_all(db, catalog)
    synced = 0
    for display_name, data in entries.items():
        await redis_set(catalog, display_name, data)
        synced += 1
    return {"synced": synced, "backfilled": backfilled, "catalog": catalog}
