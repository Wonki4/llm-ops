"""Redis client factory supporting standalone and cluster modes."""

import json
import logging
from typing import Any

import redis
import redis.asyncio as aioredis
from redis.asyncio.cluster import RedisCluster

from app.config import settings

logger = logging.getLogger(__name__)

CATALOG_HASH_KEY = "llm_catalog"

_client: aioredis.Redis | RedisCluster | None = None


async def get_redis() -> aioredis.Redis | RedisCluster:
    """Get or create async Redis client (standalone or cluster)."""
    global _client
    if _client is not None:
        return _client

    if settings.redis_cluster:
        _client = RedisCluster.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    else:
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

    logger.info("Redis client created (cluster=%s, url=%s)", settings.redis_cluster, settings.redis_url)
    return _client


async def catalog_get_all() -> dict[str, dict]:
    """Get all catalog entries from Redis hash."""
    r = await get_redis()
    raw = await r.hgetall(CATALOG_HASH_KEY)
    result = {}
    for display_name, value in raw.items():
        try:
            result[display_name] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            result[display_name] = {"_raw": value}
    return result


async def catalog_get(display_name: str) -> dict | None:
    """Get a single catalog entry by display name."""
    r = await get_redis()
    raw = await r.hget(CATALOG_HASH_KEY, display_name)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


async def catalog_set(display_name: str, data: dict[str, Any]) -> None:
    """Set a catalog entry in Redis hash."""
    r = await get_redis()
    await r.hset(CATALOG_HASH_KEY, display_name, json.dumps(data, ensure_ascii=False))


async def catalog_delete(display_name: str) -> bool:
    """Delete a catalog entry. Returns True if deleted."""
    r = await get_redis()
    return bool(await r.hdel(CATALOG_HASH_KEY, display_name))


async def catalog_rename(old_name: str, new_name: str) -> bool:
    """Rename a catalog entry (get+set+delete)."""
    r = await get_redis()
    raw = await r.hget(CATALOG_HASH_KEY, old_name)
    if raw is None:
        return False
    await r.hset(CATALOG_HASH_KEY, new_name, raw)
    await r.hdel(CATALOG_HASH_KEY, old_name)
    return True
