"""Redis-backed model catalog management endpoints with PostgreSQL sync."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.clients.redis import catalog_delete, catalog_get, catalog_get_all, catalog_set
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


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


async def _pg_upsert(db: AsyncSession, display_name: str, data: dict) -> None:
    """Upsert catalog entry to PostgreSQL for backup."""
    await db.execute(
        text("""
            INSERT INTO custom_redis_catalog (display_name, data, updated_at)
            VALUES (:name, :data, NOW())
            ON CONFLICT (display_name) DO UPDATE
            SET data = :data, updated_at = NOW()
        """),
        {"name": display_name, "data": json.dumps(data, ensure_ascii=False)},
    )


async def _pg_delete(db: AsyncSession, display_name: str) -> None:
    """Delete catalog entry from PostgreSQL."""
    await db.execute(
        text("DELETE FROM custom_redis_catalog WHERE display_name = :name"),
        {"name": display_name},
    )


async def _pg_rename(db: AsyncSession, old_name: str, new_name: str, data: dict) -> None:
    """Rename catalog entry in PostgreSQL (delete old + insert new)."""
    await _pg_delete(db, old_name)
    await _pg_upsert(db, new_name, data)


@router.get("")
async def list_catalog(
    user: CustomUser = Depends(get_current_user),
) -> dict:
    """List all catalog entries from Redis."""
    entries = await catalog_get_all()
    return {
        "entries": [
            {"display_name": name, **data}
            for name, data in entries.items()
        ],
        "total": len(entries),
    }


@router.get("/{display_name}")
async def get_catalog_entry(
    display_name: str,
    user: CustomUser = Depends(get_current_user),
) -> dict:
    """Get a single catalog entry."""
    data = await catalog_get(display_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return {"display_name": display_name, **data}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_catalog_entry(
    body: CatalogCreateRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new catalog entry (Super User only)."""
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required")

    existing = await catalog_get(body.display_name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"'{body.display_name}' already exists")

    data = body.entry.model_dump()
    await catalog_set(body.display_name, data)
    await _pg_upsert(db, body.display_name, data)
    return {"display_name": body.display_name, **data}


@router.put("/{display_name}")
async def update_catalog_entry(
    display_name: str,
    body: CatalogUpdateRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a catalog entry (Super User only)."""
    existing = await catalog_get(display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    if body.entry is not None:
        data = body.entry.model_dump()
    else:
        data = existing

    target_name = display_name
    if body.new_display_name and body.new_display_name != display_name:
        conflict = await catalog_get(body.new_display_name)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"'{body.new_display_name}' already exists")
        await catalog_delete(display_name)
        await _pg_rename(db, display_name, body.new_display_name, data)
        target_name = body.new_display_name
    else:
        await _pg_upsert(db, target_name, data)

    await catalog_set(target_name, data)
    return {"display_name": target_name, **data}


@router.delete("/{display_name}")
async def delete_catalog_entry(
    display_name: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a catalog entry (Super User only)."""
    deleted = await catalog_delete(display_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    await _pg_delete(db, display_name)
    return {"deleted": True, "display_name": display_name}


@router.patch("/{display_name}/apikey")
async def update_user_api_key(
    display_name: str,
    body: dict,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Allow a user to set their own apiKey for a catalog entry."""
    existing = await catalog_get(display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    api_key = body.get("apiKey", "")
    user_keys: dict = existing.get("userApiKeys", {})
    user_keys[user.user_id] = api_key
    existing["userApiKeys"] = user_keys
    await catalog_set(display_name, existing)
    await _pg_upsert(db, display_name, existing)

    return {"display_name": display_name, "apiKey": api_key}


@router.post("/sync-from-pg")
async def sync_from_pg(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Restore Redis catalog from PostgreSQL backup (Super User only)."""
    result = await db.execute(text("SELECT display_name, data FROM custom_redis_catalog"))
    rows = result.mappings().all()
    count = 0
    for row in rows:
        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
        await catalog_set(row["display_name"], data)
        count += 1
    return {"restored": count}


@router.post("/sync-to-pg")
async def sync_to_pg(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync Redis catalog to PostgreSQL (Super User only).

    Entries in Redis but not in PG are inserted.
    Entries in both are updated from Redis.
    Entries only in PG are left untouched.
    """
    redis_entries = await catalog_get_all()
    pg_result = await db.execute(text("SELECT display_name FROM custom_redis_catalog"))
    pg_names = {r["display_name"] for r in pg_result.mappings()}

    synced = 0
    for display_name, data in redis_entries.items():
        await _pg_upsert(db, display_name, data)
        synced += 1

    return {"synced": synced, "new": synced - len(pg_names & set(redis_entries.keys()))}
