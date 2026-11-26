"""Share authorized, scoped latency results across viewers and portal replicas."""

import asyncio
import hashlib
import json
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models.custom_latency_cache import CustomLatencyCache

_MAX_LOCAL_ENTRIES = 128
_MAX_LOCAL_BYTES = 16 * 1024 * 1024
_MAX_RESULT_BYTES = 256 * 1024  # A 30-minute graph has 61 points per metric.
_LEASE_SECONDS = 45
_RENEW_SECONDS = _LEASE_SECONDS / 3
_DB_TIMEOUT = 1.5
_CACHE_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


def latency_tick(now: float, *, history: bool) -> int:
    interval = 30 if history else 15
    return int(now) // interval * interval


async def _lookup(factory, key, owner):
    """Return (result, owns_lease); reserve a miss without holding a transaction during PromQL."""
    table = CustomLatencyCache
    async with asyncio.timeout(_DB_TIMEOUT), factory.begin() as db:
        row = (await db.execute(select(table.payload).where(table.key == key, table.expires_at > func.now()))).first()
        if row is not None:
            return row.payload, False
        claim = insert(table).values(
            key=key, owner=owner, expires_at=func.now() + timedelta(seconds=_LEASE_SECONDS),
        )
        claim = claim.on_conflict_do_update(
            index_elements=[table.key],
            set_={"owner": owner, "payload": None, "expires_at": claim.excluded.expires_at},
            where=table.expires_at <= func.now(),
        )
        claimed = await db.scalar(claim.returning(table.key))
        if claimed is not None:
            # Purge older scopes as queries arrive, even after their last viewer leaves.
            expired = (select(table.key).where(table.expires_at <= func.now())
                       .order_by(table.expires_at).limit(64).with_for_update(skip_locked=True))
            await db.execute(delete(table).where(table.key.in_(expired)).execution_options(synchronize_session=False))
        return None, claimed is not None


async def _finish(factory, key, owner, result, ttl):
    table = CustomLatencyCache
    where = (table.key == key, table.owner == owner)
    async with asyncio.timeout(_DB_TIMEOUT), factory.begin() as db:
        if result is None:
            await db.execute(delete(table).where(*where))
        else:
            await db.execute(update(table).where(*where).values(
                payload=result, owner=None, expires_at=func.now() + timedelta(seconds=ttl),
            ))


async def _renew_while_loading(factory, key, owner):
    # Cold requests may need several batches of Kubernetes discovery before querying Prometheus.
    while True:
        await asyncio.sleep(_RENEW_SECONDS)
        try:
            async with asyncio.timeout(_DB_TIMEOUT), factory.begin() as db:
                renewed = await db.execute(update(CustomLatencyCache).where(
                    CustomLatencyCache.key == key, CustomLatencyCache.owner == owner,
                ).values(expires_at=func.now() + timedelta(seconds=_LEASE_SECONDS)))
                if not renewed.rowcount:
                    return
        except _CACHE_ERRORS:
            # The existing lease may still be valid; only its current owner can publish.
            continue


class LatencyCache:
    def __init__(self, *, max_result_bytes: int = _MAX_RESULT_BYTES):
        self.max_result_bytes = max_result_bytes
        self.recent = OrderedDict()
        self.recent_bytes = 0
        self.pending = {}

    async def read(
        self, db: AsyncSession, *, scope: dict, history: bool, now: float, load: Callable[[], Awaitable[dict]],
    ) -> dict:
        """Call after authorization. Only the digest of the cache key is persisted."""
        bind = getattr(db, "bind", None)
        if not isinstance(bind, AsyncEngine) or bind.dialect.name != "postgresql":
            return await load()
        tick = latency_tick(now, history=history)
        digest = hashlib.sha256(json.dumps([1, history, tick, scope], sort_keys=True).encode()).hexdigest()
        # A worker can serve more than one portal DB in tests. Never share results across binds.
        key = (bind, digest)
        while True:
            if key in self.recent:
                self.recent.move_to_end(key)
                return self.recent[key][0]
            if key not in self.pending:
                break
            value = await asyncio.shield(self.pending[key])
            if value is not None:
                return value
        # Keep the fetch in its originating request, whose HTTP client owns the connection.
        # Cancelled waiters leave the active reader alone. Cancelling the reader wakes the waiters.
        pending = self.pending[key] = asyncio.get_running_loop().create_future()
        try:
            value = await self._shared(async_sessionmaker(bind), digest, 30 if history else 15, load)
            size = len(json.dumps(value))
            if size <= self.max_result_bytes:
                self.recent[key] = (value, size)
                self.recent_bytes += size
                while len(self.recent) > _MAX_LOCAL_ENTRIES or self.recent_bytes > _MAX_LOCAL_BYTES:
                    _, (_, evicted_size) = self.recent.popitem(last=False)
                    self.recent_bytes -= evicted_size
            # Requests already waiting share even a result too large to retain.
            pending.set_result(value)
            return value
        except asyncio.CancelledError:
            pending.set_result(None)
            raise
        except Exception as exc:
            pending.set_exception(exc)
            pending.exception()  # A lone reader may have no waiters to retrieve it.
            raise
        finally:
            del self.pending[key]

    async def _shared(self, factory, key, ttl, load):
        owner = uuid.uuid4()
        interval = 0.1
        while True:
            try:
                value, claimed = await _lookup(factory, key, owner)
            except _CACHE_ERRORS:
                # During migration or DB pool contention the portal can still query Prometheus.
                return await load()
            if value is not None:
                return value
            if claimed:
                break
            await asyncio.sleep(interval)
            interval = min(1.0, 2 * interval)

        cacheable = None
        renewal = asyncio.create_task(_renew_while_loading(factory, key, owner))
        try:
            value = await load()
            if len(json.dumps(value)) <= self.max_result_bytes:
                cacheable = value
            return value
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            # Only the current lease owner can publish. A crashed worker's lease expires on its own.
            try:
                await _finish(factory, key, owner, cacheable, ttl)
            except _CACHE_ERRORS:
                pass


# Whole-model plans and history responses can cover many clusters and pools.
cached_latency = LatencyCache(max_result_bytes=4 * 1024 * 1024).read
