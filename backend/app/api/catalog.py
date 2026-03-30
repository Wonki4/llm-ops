"""Redis-backed model catalog management endpoints with PostgreSQL sync."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.clients.redis import catalog_delete, catalog_get, catalog_get_all, catalog_set
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

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


async def _pg_upsert(db: AsyncSession, catalog: str, display_name: str, data: dict) -> None:
    """Upsert catalog entry to PostgreSQL for backup."""
    await db.execute(
        text("""
            INSERT INTO custom_redis_catalog (display_name, data, updated_at)
            VALUES (:name, :data, NOW())
            ON CONFLICT (display_name) DO UPDATE
            SET data = :data, updated_at = NOW()
        """),
        {"name": f"{catalog}:{display_name}", "data": json.dumps(data, ensure_ascii=False)},
    )


async def _pg_delete(db: AsyncSession, catalog: str, display_name: str) -> None:
    """Delete catalog entry from PostgreSQL."""
    await db.execute(
        text("DELETE FROM custom_redis_catalog WHERE display_name = :name"),
        {"name": f"{catalog}:{display_name}"},
    )


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
    """List all catalog entries from Redis."""
    await _validate_catalog(catalog, db)
    entries = await catalog_get_all(catalog)
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
    """Get a single catalog entry."""
    await _validate_catalog(catalog, db)
    data = await catalog_get(catalog, display_name)
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

    existing = await catalog_get(catalog, body.display_name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"'{body.display_name}' already exists")

    data = body.entry.model_dump()
    await catalog_set(catalog, body.display_name, data)
    await _pg_upsert(db, catalog, body.display_name, data)
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
    existing = await catalog_get(catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    if body.entry is not None:
        data = body.entry.model_dump()
    else:
        data = existing

    target_name = display_name
    if body.new_display_name and body.new_display_name != display_name:
        conflict = await catalog_get(catalog, body.new_display_name)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"'{body.new_display_name}' already exists")
        await catalog_delete(catalog, display_name)
        await _pg_delete(db, catalog, display_name)
        target_name = body.new_display_name

    await catalog_set(catalog, target_name, data)
    await _pg_upsert(db, catalog, target_name, data)
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
    deleted = await catalog_delete(catalog, display_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    await _pg_delete(db, catalog, display_name)
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
    existing = await catalog_get(catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    api_key = body.get("apiKey", "")
    user_keys: dict = existing.get("userApiKeys", {})
    user_keys[user.user_id] = api_key
    existing["userApiKeys"] = user_keys
    await catalog_set(catalog, display_name, existing)
    await _pg_upsert(db, catalog, display_name, existing)

    return {"display_name": display_name, "apiKey": api_key}


# ─── Sync ──────────────────────────────────────────────────────

@router.post("/sync-from-pg")
async def sync_from_pg(
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Restore Redis catalog from PostgreSQL backup (Super User only)."""
    await _validate_catalog(catalog, db)
    prefix = f"{catalog}:"
    result = await db.execute(
        text("SELECT display_name, data FROM custom_redis_catalog WHERE display_name LIKE :prefix"),
        {"prefix": f"{prefix}%"},
    )
    rows = result.mappings().all()
    count = 0
    for row in rows:
        name = row["display_name"].removeprefix(prefix)
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
        await catalog_set(catalog, name, data)
        count += 1
    return {"restored": count, "catalog": catalog}


@router.post("/sync-to-pg")
async def sync_to_pg(
    catalog: str = Query(DEFAULT_CATALOG),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync Redis catalog to PostgreSQL (Super User only)."""
    await _validate_catalog(catalog, db)
    redis_entries = await catalog_get_all(catalog)
    synced = 0
    for display_name, data in redis_entries.items():
        await _pg_upsert(db, catalog, display_name, data)
        synced += 1
    return {"synced": synced, "catalog": catalog}
