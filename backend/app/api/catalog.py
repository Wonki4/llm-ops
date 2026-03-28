"""Redis-backed model catalog management endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import get_current_user, require_super_user
from app.clients.redis import catalog_delete, catalog_get, catalog_get_all, catalog_rename, catalog_set
from app.db.models.custom_user import CustomUser

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
) -> dict:
    """Create a new catalog entry (Super User only)."""
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required")

    existing = await catalog_get(body.display_name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"'{body.display_name}' already exists")

    data = body.entry.model_dump()
    await catalog_set(body.display_name, data)
    return {"display_name": body.display_name, **data}


@router.put("/{display_name}")
async def update_catalog_entry(
    display_name: str,
    body: CatalogUpdateRequest,
    user: CustomUser = Depends(require_super_user),
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
        target_name = body.new_display_name

    await catalog_set(target_name, data)
    return {"display_name": target_name, **data}


@router.delete("/{display_name}")
async def delete_catalog_entry(
    display_name: str,
    user: CustomUser = Depends(require_super_user),
) -> dict:
    """Delete a catalog entry (Super User only)."""
    deleted = await catalog_delete(display_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return {"deleted": True, "display_name": display_name}


@router.patch("/{display_name}/apikey")
async def update_user_api_key(
    display_name: str,
    body: dict,
    user: CustomUser = Depends(get_current_user),
) -> dict:
    """Allow a user to set their own apiKey for a catalog entry.

    This stores a user-specific key by appending the user_id to the field.
    """
    existing = await catalog_get(display_name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    api_key = body.get("apiKey", "")
    user_keys: dict = existing.get("userApiKeys", {})
    user_keys[user.user_id] = api_key
    existing["userApiKeys"] = user_keys
    await catalog_set(display_name, existing)

    return {"display_name": display_name, "apiKey": api_key}
