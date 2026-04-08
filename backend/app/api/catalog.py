"""Redis-backed model cache management."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.clients.redis import (
    catalog_delete as redis_delete,
    catalog_get as redis_get,
    catalog_get_all as redis_get_all,
    catalog_set as redis_set,
)
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


# ─── CRUD (Redis only) ──────────────────────────────────────────

@router.get("")
async def list_catalog(
    catalog: str = Query(DEFAULT_CATALOG, description="Catalog suffix (e.g. chat, hcp)"),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all catalog entries from Redis."""
    await _validate_catalog(catalog, db)
    entries = await redis_get_all(catalog)
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
    """Get a single catalog entry from Redis."""
    await _validate_catalog(catalog, db)
    data = await redis_get(catalog, display_name)
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

    existing = await redis_get(catalog, body.display_name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"'{body.display_name}' already exists")

    data = body.entry.model_dump()
    await redis_set(catalog, body.display_name, data)
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
    existing = await redis_get(catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    if body.entry is not None:
        data = body.entry.model_dump()
    else:
        data = existing

    target_name = display_name
    if body.new_display_name and body.new_display_name != display_name:
        conflict = await redis_get(catalog, body.new_display_name)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"'{body.new_display_name}' already exists")
        await redis_delete(catalog, display_name)
        target_name = body.new_display_name

    await redis_set(catalog, target_name, data)
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
    deleted = await redis_delete(catalog, display_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
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
    existing = await redis_get(catalog, display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    api_key = body.get("apiKey", "")
    user_keys: dict = existing.get("userApiKeys", {})
    user_keys[user.user_id] = api_key
    existing["userApiKeys"] = user_keys
    await redis_set(catalog, display_name, existing)

    return {"display_name": display_name, "apiKey": api_key}
