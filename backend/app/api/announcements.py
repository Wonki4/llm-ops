"""Announcement endpoints (markdown-based, super user writes, all users read)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_super_user
from app.db.models.custom_announcement import CustomAnnouncement
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/announcements", tags=["announcements"])


def _to_dict(row: CustomAnnouncement) -> dict:
    return {
        "id": str(row.id),
        "title": row.title,
        "content": row.content,
        "author_id": row.author_id,
        "is_published": row.is_published,
        "is_pinned": row.is_pinned,
        "is_featured": row.is_featured,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("")
async def list_announcements(
    include_unpublished: bool = False,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List announcements (newest first). Unpublished drafts are super-user only."""
    stmt = select(CustomAnnouncement).order_by(
        desc(CustomAnnouncement.is_featured),
        desc(CustomAnnouncement.is_pinned),
        desc(CustomAnnouncement.created_at),
    )
    if not include_unpublished or user.global_role.value != "super_user":
        stmt = stmt.where(CustomAnnouncement.is_published.is_(True))

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {"announcements": [_to_dict(r) for r in rows]}


class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., min_length=1)
    is_published: bool = True
    is_pinned: bool = False
    is_featured: bool = False


async def _clear_other_featured(db: AsyncSession, keep_id: "uuid.UUID | None") -> None:
    """Unset is_featured on all rows except the given id (at most one can be featured)."""
    stmt = update(CustomAnnouncement).values(is_featured=False).where(
        CustomAnnouncement.is_featured.is_(True)
    )
    if keep_id is not None:
        stmt = stmt.where(CustomAnnouncement.id != keep_id)
    await db.execute(stmt)


def _ensure_featured_published(is_featured: bool, is_published: bool) -> None:
    """Invariant: a featured announcement must be published."""
    if is_featured and not is_published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="대표 공지는 게시 상태여야 합니다.",
        )


@router.post("")
async def create_announcement(
    body: AnnouncementCreate,
    admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure_featured_published(body.is_featured, body.is_published)

    if body.is_featured:
        await _clear_other_featured(db, keep_id=None)

    row = CustomAnnouncement(
        title=body.title,
        content=body.content,
        author_id=admin.user_id,
        is_published=body.is_published,
        is_pinned=body.is_pinned,
        is_featured=body.is_featured,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return _to_dict(row)


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=256)
    content: str | None = Field(None, min_length=1)
    is_published: bool | None = None
    is_pinned: bool | None = None
    is_featured: bool | None = None


@router.patch("/{announcement_id}")
async def update_announcement(
    announcement_id: str,
    body: AnnouncementUpdate,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        aid = uuid.UUID(announcement_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id") from e

    result = await db.execute(select(CustomAnnouncement).where(CustomAnnouncement.id == aid))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    new_published = body.is_published if body.is_published is not None else row.is_published
    new_featured = body.is_featured if body.is_featured is not None else row.is_featured
    _ensure_featured_published(new_featured, new_published)

    if body.title is not None:
        row.title = body.title
    if body.content is not None:
        row.content = body.content
    if body.is_published is not None:
        row.is_published = body.is_published
    if body.is_pinned is not None:
        row.is_pinned = body.is_pinned
    if body.is_featured is not None:
        if body.is_featured:
            await _clear_other_featured(db, keep_id=row.id)
        row.is_featured = body.is_featured

    await db.flush()
    await db.refresh(row)
    return _to_dict(row)


@router.delete("/{announcement_id}")
async def delete_announcement(
    announcement_id: str,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        aid = uuid.UUID(announcement_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id") from e

    result = await db.execute(select(CustomAnnouncement).where(CustomAnnouncement.id == aid))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    await db.delete(row)
    return {"status": "deleted", "id": announcement_id}
