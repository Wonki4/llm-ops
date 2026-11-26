"""Per-minute request demand, submitted forecasts, and shared capacity allocation."""

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

DIMENSIONS = ("rpm", "input_tpm", "output_tpm")
Scope = Literal["team", "user", "personal", "key"]
Source = Literal["submitted", "peak", "p95"]
SCOPE_PREDICATES = {
    "team": "team_id = :subject",
    "user": '"user" = :subject',
    "personal": """"user" = :subject AND NULLIF(team_id, '') IS NULL""",
    "key": "api_key = :subject",
}


class DemandScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scope: Scope
    subject: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=256)


class Forecast(DemandScope):
    request_id: UUID = Field(default_factory=uuid4)
    period_start: date
    period_end: date
    mode: Literal["per_request", "per_minute"]
    rpm: int = Field(ge=0, le=10**9)
    input_tokens: int = Field(ge=0, le=10**12)
    output_tokens: int = Field(ge=0, le=10**12)

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end < self.period_start:
            raise ValueError("Expected demand end must follow its start")
        if self.rpm == 0 and (self.input_tokens or self.output_tokens):
            raise ValueError("Nonzero token demand requires a positive request rate")
        return self

    def rates(self):
        scale = self.rpm if self.mode == "per_request" else 1
        return dict(rpm=self.rpm, input_tpm=self.input_tokens * scale, output_tpm=self.output_tokens * scale)


def scope_key(scope: DemandScope) -> str:
    data = json.dumps([scope.scope, scope.subject, scope.model], ensure_ascii=False).encode()
    return "model-demand:" + hashlib.sha256(data).hexdigest() + ":"


async def forecasts(db, scope: DemandScope, on_date=None):
    # Ordered range over primary keys works with the DB's ordinary btree index.
    prefix = scope_key(scope)
    where = (
        " WHERE CAST(value AS jsonb)->>'period_start' <= :day AND CAST(value AS jsonb)->>'period_end' >= :day"
        if on_date
        else ""
    )
    params = {"lo": prefix + "0", "hi": prefix + "g"}
    if on_date:
        params["day"] = on_date.isoformat()
    rows = (
        await db.execute(
            text(
                "WITH scoped AS MATERIALIZED (SELECT key,value,updated_by,updated_at FROM custom_portal_settings "
                "WHERE key >= :lo AND key < :hi) SELECT value,updated_by,updated_at FROM scoped "
                + where
                + " ORDER BY updated_at DESC, key DESC LIMIT 25"
            ),
            params,
        )
    ).mappings()
    return [
        dict(**json.loads(row["value"]), submitted_by=row["updated_by"], submitted_at=row["updated_at"]) for row in rows
    ]


async def observed(db, target: DemandScope, days: int, now=None):
    end = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    result = {
        "from": start,
        "until": end,
        "minutes_requested": days * 1440,
        "source": "LiteLLM_SpendLogs",
        "peak": None,
        "p95": None,
        "percentile_minutes": "recorded_active_minutes",
        "coverage": "unknown",
    }
    # Prisma timestamps are stored as UTC timestamp without time zone. Filter directly on the indexed startTime.
    params = dict(
        subject=target.subject, model=target.model, start=start.replace(tzinfo=None), end=end.replace(tzinfo=None)
    )
    columns = ", ".join(
        f"max({k}) FILTER (WHERE rpm > 0) AS peak_{k}, "
        f"percentile_disc(0.95) WITHIN GROUP (ORDER BY {k}) FILTER (WHERE rpm > 0) AS p95_{k}"
        for k in DIMENSIONS
    )
    query = text(f"""
        WITH minutes AS (
            SELECT date_trunc('minute', "startTime") AS minute,
                   min("startTime") FILTER (WHERE model_group = :model) AS first_seen,
                   max("startTime") FILTER (WHERE model_group = :model) AS last_seen,
                   count(*) FILTER (WHERE model_group = :model) AS rpm,
                   sum(prompt_tokens) FILTER (WHERE model_group = :model) AS input_tpm,
                   sum(completion_tokens) FILTER (WHERE model_group = :model) AS output_tpm,
                   count(*) FILTER (WHERE nullif(model_group,'') IS NULL) AS unassigned,
                   count(*) FILTER (WHERE model_group = :model AND
                       (prompt_tokens IS NULL OR completion_tokens IS NULL)) AS missing_tokens,
                   count(*) FILTER (WHERE model_group = :model
                       AND lower(status) IN ('failure','failed','error')) AS errors
            FROM "LiteLLM_SpendLogs" WHERE {SCOPE_PREDICATES[target.scope]}
              AND "startTime" >= :start AND "startTime" < :end
              AND (model_group = :model OR nullif(model_group,'') IS NULL)
            GROUP BY 1
        )
        SELECT min(first_seen) AS first_seen, max(last_seen) AS last_seen,
               count(*) FILTER (WHERE rpm > 0) AS active_minutes,
               coalesce(sum(unassigned),0) AS unassigned, coalesce(sum(errors),0) AS errors,
               coalesce(sum(rpm),0) AS requests, coalesce(sum(missing_tokens),0) AS missing_tokens,
               {columns}
        FROM minutes
    """)
    try:
        async with db.begin_nested():
            row = (await db.execute(query, params)).mappings().one()
    except DBAPIError as exc:
        # Missing or inaccessible request logs are normal in retention-restricted installations.
        code = getattr(exc.orig, "sqlstate", None)
        if code not in {"42P01", "42703", "42501"}:
            raise
        return {**result, "coverage": "unavailable"}
    counts = {k: int(row[k]) for k in ("active_minutes", "requests", "unassigned", "errors", "missing_tokens")}
    result.update(counts, first_seen=row["first_seen"], last_seen=row["last_seen"])
    if counts["active_minutes"]:
        result["coverage"] = "recorded"
        for kind in ("peak", "p95"):
            result[kind] = {
                k: (None if k != "rpm" and counts["missing_tokens"] else int(row[f"{kind}_{k}"])) for k in DIMENSIONS
            }
    return result


class Consumer(DemandScope):
    source: Source = "submitted"
    workload_match: bool = False


def split_capacity(capacity, demands, reserve_percent):
    available = {k: capacity[k] * (1 - reserve_percent / 100) for k in DIMENSIONS}
    totals = {k: sum(d[k] for d in demands) for k in DIMENSIONS}
    ratios = {k: available[k] / total for k, total in totals.items() if total > 0}
    factor = min(1.0, min(ratios.values(), default=1.0))
    return {
        "available": available,
        "demand": totals,
        "share": factor,
        "limiting_dimension": min(ratios, key=ratios.get) if ratios and factor < 1 else None,
        "recommendations": [{k: math.floor(d[k] * factor) for k in DIMENSIONS} for d in demands],
    }


def counted_tpm(rates, accounting):
    if accounting == "total":
        return rates["input_tpm"] + rates["output_tpm"]
    return rates.get(f"{accounting}_tpm") if accounting in {"input", "output"} else None


def workload_basis(benchmark, consumers, demands):
    """Use a mixed baseline only for measured request lengths and measured traffic shares."""
    if not all(consumer.workload_match for consumer in consumers):
        return "scenario"
    measured = benchmark["rates"]
    if not measured["rpm"]:
        return "scenario"

    def matches(demand, inputs, outputs):
        if not demand["rpm"]:
            return True
        for metric, target in (("input_tpm", inputs), ("output_tpm", outputs)):
            if abs(demand[metric] / demand["rpm"] - target) > target * 0.25:
                return False
        return True

    profiles = benchmark.get("mixed_profiles") or []
    if not profiles:
        inputs = measured["input_tpm"] / measured["rpm"]
        outputs = measured["output_tpm"] / measured["rpm"]
        return "measured_profile" if all(matches(d, inputs, outputs) for d in demands) else "scenario"

    required = ("input_per_request", "output_per_request", "request_fraction")
    if any(
        not isinstance(p, dict)
        or any(not isinstance(p.get(k), (int, float)) or not math.isfinite(p[k]) or p[k] < 0 for k in required)
        for p in profiles
    ):
        return "scenario"
    if not math.isclose(sum(p["request_fraction"] for p in profiles), 1, abs_tol=0.01):
        return "scenario"
    arrivals = [0] * len(profiles)
    for d in demands:
        if not d["rpm"]:
            continue
        indices = [i for i, p in enumerate(profiles) if matches(d, p["input_per_request"], p["output_per_request"])]
        if len(indices) != 1:
            return "scenario"
        arrivals[indices[0]] += d["rpm"]
    total = sum(arrivals)
    if total and all(abs(n / total - p["request_fraction"]) <= 0.05 for n, p in zip(arrivals, profiles)):
        return "measured_mix"
    return "scenario"
