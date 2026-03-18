"""Alembic migrations environment.

IMPORTANT: Only manages custom_* tables. Never touches LiteLLM tables.
Uses a separate version table (custom_alembic_version) to avoid conflicts.
"""

import os

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import CustomBase

# Import all models so metadata is populated
from app.db.models import CustomModelCatalog, CustomModelStatusHistory, CustomTeamJoinRequest, CustomUser  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment if available
db_url = os.environ.get("APP_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = CustomBase.metadata

# Only manage tables with 'custom_' prefix
INCLUDE_TABLES = {t for t in target_metadata.tables}


def include_object(obj, name, type_, reflected, compare_to):
    """Only include custom_* tables in migrations."""
    if type_ == "table":
        return name.startswith("custom_")
    if type_ == "column" and hasattr(obj, "table"):
        return obj.table.name.startswith("custom_")
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        version_table="custom_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        version_table="custom_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
