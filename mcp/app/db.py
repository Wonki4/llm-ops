"""Async engines / sessions for the portal and LiteLLM databases.

Mirrors backend/app/db/session.py so the MCP service reads the same data with
its own connection pools (fully decoupled from the backend process).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Portal DB (custom_* tables — e.g. hidden_teams_strict).
engine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True, pool_recycle=300)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# LiteLLM DB (LiteLLM_* tables) — defaults to the same DB if not configured.
_litellm_db_url = settings.litellm_database_url or settings.database_url
litellm_engine = create_async_engine(_litellm_db_url, echo=settings.debug, pool_pre_ping=True, pool_recycle=300)
litellm_session_factory = async_sessionmaker(litellm_engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Portal DB session (read-only usage here)."""
    async with async_session_factory() as session:
        yield session


async def get_litellm_db() -> AsyncGenerator[AsyncSession, None]:
    """LiteLLM DB session (read-only usage here)."""
    async with litellm_session_factory() as session:
        yield session
