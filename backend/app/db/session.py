from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Portal DB (custom_* tables, Alembic migrations)
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# LiteLLM DB (LiteLLM_* tables) — defaults to same DB if not configured
_litellm_db_url = settings.litellm_database_url or settings.database_url

litellm_engine = create_async_engine(
    _litellm_db_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
)

litellm_session_factory = async_sessionmaker(litellm_engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Portal DB session (custom_* tables)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_litellm_db() -> AsyncGenerator[AsyncSession, None]:
    """LiteLLM DB session (LiteLLM_* tables). Read/write to LiteLLM's database."""
    async with litellm_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
