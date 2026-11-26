"""Model demand and allocation. Simulations never write to the proxy."""

import json
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.key_limits import effective_model_limits
from app.auth.deps import get_current_user, require_super_user
from app.auth.permissions import get_team_access, require_team_admin
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db
from app.services.benchmark_credentials import public_snapshot
from app.services.litellm_registration import registration_preview
from app.services.model_demand import (
    DIMENSIONS,
    Consumer,
    DemandScope,
    Forecast,
    Scope,
    counted_tpm,
    forecasts,
    observed,
    scope_key,
    split_capacity,
    workload_basis,
)

router = APIRouter(prefix="/api/model-demand", tags=["model-demand"])


async def key_record(db, key):
    return (
        (
            await db.execute(
                text("""
        SELECT k.user_id, k.team_id, k.metadata, t.metadata AS inherited
        FROM "LiteLLM_VerificationToken" k LEFT JOIN "LiteLLM_TeamTable" t ON t.team_id=k.team_id
        WHERE k.token=:key"""),
                {"key": key},
            )
        )
        .mappings()
        .first()
    )


async def authorize(target, user, proxy, writing=False):
    if target.scope == "team":
        if writing:
            await require_team_admin(user, target.subject, proxy)
        else:
            await get_team_access(user, target.subject, proxy)
    elif target.scope == "key":
        row = await key_record(proxy, target.subject)
        if not row or (
            user.global_role != GlobalRole.SUPER_USER and row["user_id"] != (user.litellm_user_id or user.user_id)
        ):
            raise HTTPException(403, "Key access required")
    elif target.subject in {user.user_id, user.litellm_user_id}:
        target.subject = user.litellm_user_id or user.user_id
    elif user.global_role != GlobalRole.SUPER_USER:
        raise HTTPException(403, "Personal demand is private")


async def live_limits(proxy, target):
    if target.scope == "key":
        row = await key_record(proxy, target.subject)
        if not row:
            raise HTTPException(404, "Key unavailable")
        full = effective_model_limits(row["metadata"], row["inherited"])
    elif target.scope == "team":
        row = (
            await proxy.execute(
                text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id=:id'), {"id": target.subject}
            )
        ).first()
        if not row:
            raise HTTPException(404, "Team unavailable")
        full = effective_model_limits(row[0], {})
    else:
        row = (
            (
                await proxy.execute(
                    text('SELECT rpm_limit,tpm_limit FROM "LiteLLM_UserTable" WHERE user_id=:id'),
                    {"id": target.subject},
                )
            )
            .mappings()
            .first()
        )
        return {"scope": "user_global", "rpm": row and row["rpm_limit"], "tpm": row and row["tpm_limit"]}
    per_key = target.scope == "key" and bool(row["team_id"])
    return {
        "scope": target.scope,
        "rpm_starts_key_override": per_key and not (row["metadata"] or {}).get("model_rpm_limit"),
        "tpm_starts_key_override": per_key and not (row["metadata"] or {}).get("model_tpm_limit"),
        "rpm": (full["model_rpm_limit"] or {}).get(target.model),
        "tpm": (full["model_tpm_limit"] or {}).get(target.model),
        "rpm_inherited": full["model_rpm_inherited"],
        "tpm_inherited": full["model_tpm_inherited"],
    }


@router.get("")
async def read_demand(
    scope: Scope,
    subject: str = Query(min_length=1, max_length=256),
    model: str = Query(min_length=1, max_length=256),
    days: int = Query(7, ge=1, le=90),
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    proxy: AsyncSession = Depends(get_litellm_db),
):
    target = DemandScope(scope=scope, subject=subject, model=model)
    await authorize(target, user, proxy)
    return {
        "observed": await observed(proxy, target, days),
        "forecasts": await forecasts(db, target),
        "current_limits": await live_limits(proxy, target),
    }


@router.post("/forecasts", status_code=201)
async def submit(
    body: Forecast,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    proxy: AsyncSession = Depends(get_litellm_db),
):
    await authorize(body, user, proxy, writing=True)
    value = {**body.model_dump(mode="json"), "rates": body.rates()}
    key = scope_key(body) + body.request_id.hex
    created = await db.execute(
        text(
            "INSERT INTO custom_portal_settings(key,value,updated_by) VALUES (:key,:value,:actor) "
            "ON CONFLICT (key) DO NOTHING RETURNING key"
        ),
        {"key": key, "value": json.dumps(value), "actor": user.user_id},
    )
    if not created.first():
        existing = (
            await db.execute(text("SELECT value, updated_by FROM custom_portal_settings WHERE key=:key"), {"key": key})
        ).one()
        if existing.updated_by != user.user_id or json.loads(existing.value) != value:
            raise HTTPException(409, "This submission ID was already used")
    return value


def measurement_matches(run, dep):
    stored = (run.params or {}).get("_capacity_identity")
    if not stored:
        return False
    try:
        from app.services.capacity_identity import deployment_identity
    except ModuleNotFoundError as exc:
        if exc.name != "app.services.capacity_identity":
            raise
        return False
    return stored == deployment_identity(dep)


def eligible_capacity(run, dep, connections):
    from app.api.benchmarks import _serving_snapshot

    search = (run.result or {}).get("capacity_search") or {}
    best = search.get("best") or {}
    rates = best.get("slo_rates") or {}
    if (
        not measurement_matches(run, dep)
        or search.get("scope") != "deployment"
        or search.get("confirmed") is not True
        or not all(isinstance(rates.get(k), (float, int)) and 0 <= rates[k] < float("inf") for k in DIMENSIONS)
    ):
        return None
    if (
        run.cluster_id != dep.cluster_id
        or run.k8s_namespace != dep.namespace
        or dep.status != "Ready"
        or not dep.ready_replicas
        or (dep.serving_mode != "pd" and dep.ready_replicas < dep.replicas)
    ):
        return None
    # Retired candidate measurements and URL-only/router measurements cannot establish a live deployment's capacity.
    if not (
        run.ephemeral
        or (
            run.validation_attempt_id
            and dep.validation_state == "published"
            and run.validation_attempt_id == dep.validation_attempt_id
        )
    ):
        return None
    expected = public_snapshot(_serving_snapshot(dep))
    measured = public_snapshot(run.serving_snapshot or {})
    if any(measured.get(k) != v for k, v in expected.items()):
        return None

    intended = registration_preview(dep)
    endpoint = intended["api_base"].rstrip("/")
    aliases = {}
    for row in connections:
        name = row.get("model_name")
        params = row.get("litellm_params") or {}
        if name:
            matches = (params.get("api_base") or "").rstrip("/") == endpoint and params.get("model") == intended[
                "litellm_model"
            ]
            aliases[name] = aliases.get(name, True) and matches
    names = sorted(name for name, same in aliases.items() if same)
    if not names:
        return None
    params = run.params or {}
    return {
        "id": run.id,
        "deployment_id": dep.id,
        "deployment": dep.model_name,
        "aliases": names,
        "rates": rates,
        "created_at": run.created_at,
        "cluster_id": run.cluster_id,
        "namespace": dep.namespace,
        "preset": params.get("preset") or params.get("preset_id"),
        "workload": {
            k: params[k]
            for k in (
                "random_input_len",
                "random_output_len",
                "max_concurrency",
                "cache_ratio",
                "input_length_mode",
                "input_len",
                "output_len",
                "max_model_len",
                "dataset_name",
                "workload_type",
                "scenario",
            )
            if k in params and isinstance(params[k], (str, int, float))
        },
        "mixed_profiles": best.get("mixed_profiles") or [],
        "repetitions": best.get("repetitions"),
    }


async def capacities(db, litellm, run_id=None, offset=0):
    stmt = (
        select(CustomBenchmarkRun, CustomModelDeployment)
        .join(CustomModelDeployment, CustomBenchmarkRun.deployment_id == CustomModelDeployment.id)
        .where(CustomBenchmarkRun.status == "succeeded", CustomBenchmarkRun.result.has_key("capacity_search"))
        .order_by(CustomBenchmarkRun.created_at.desc(), CustomBenchmarkRun.id.desc())
    )
    if run_id:
        stmt = stmt.where(CustomBenchmarkRun.id == run_id)
    raw = (await db.execute(stmt.offset(offset).limit(101))).all()
    if not raw:
        return [], None
    try:
        connections = await litellm.get_model_info()
    except Exception as exc:
        raise HTTPException(502, "Could not verify the current proxy connections") from exc
    items = [entry for run, dep in raw[:100] if (entry := eligible_capacity(run, dep, connections))]
    return items, offset + 100 if len(raw) > 100 else None


@router.get("/baselines")
async def baselines(
    offset: int = Query(0, ge=0),
    _admin=Depends(require_super_user),
    db=Depends(get_db),
    litellm=Depends(get_litellm_client),
):
    items, next_offset = await capacities(db, litellm, offset=offset)
    return {"items": items, "next_offset": next_offset}


class Simulation(BaseModel):
    run_id: UUID
    consumers: list[Consumer] = Field(min_length=1, max_length=64)
    reserve_percent: float = Field(default=20, ge=0, lt=100)
    days: int = Field(default=7, ge=1, le=90)
    on_date: date = Field(default_factory=date.today)

    @model_validator(mode="after")
    def unique(self):
        slots = {(c.scope, c.subject, c.model) for c in self.consumers}
        if len(slots) != len(self.consumers):
            raise ValueError("Duplicate consumer")
        return self


async def disjoint_consumers(consumers, proxy):
    # Checking historical team membership cannot prove that an all-keys user forecast is disjoint from a team.
    # The personal-key scope guarantees disjoint accounting even when a user belongs to several teams.
    by_model = {}
    for c in consumers:
        by_model.setdefault(c.model, []).append(c)
    for group in by_model.values():
        teams = {c.subject for c in group if c.scope == "team"}
        users = {c.subject for c in group if c.scope == "user"}
        personal = {c.subject for c in group if c.scope == "personal"}
        overlap = (teams and users) or (users & personal)
        for key in (c.subject for c in group if c.scope == "key"):
            row = await key_record(proxy, key)
            if not row:
                raise HTTPException(404, "Unknown key")
            overlap = (
                overlap
                or row["team_id"] in teams
                or row["user_id"] in users
                or (not row["team_id"] and row["user_id"] in personal)
            )
        if overlap:
            raise HTTPException(
                422, "Overlapping consumers for this alias. Combine team demand with personal-key demand."
            )


async def token_accounting(proxy):
    value = (
        await proxy.execute(
            text(
                """SELECT param_value->>'token_rate_limit_type' FROM "LiteLLM_Config"
                WHERE param_name='general_settings'"""
            )
        )
    ).scalar()
    # An absent DB value can be configured in the proxy's mounted YAML.
    if value in {"input", "output", "total"}:
        return value
    return settings.litellm_token_rate_limit_type


@router.post("/simulate")
async def simulate(
    body: Simulation,
    _admin=Depends(require_super_user),
    db=Depends(get_db),
    proxy=Depends(get_litellm_db),
    litellm=Depends(get_litellm_client),
):
    items, _ = await capacities(db, litellm, body.run_id)
    if not items:
        raise HTTPException(409, "The selected run has no confirmed capacity for this live deployment")
    benchmark = items[0]
    await disjoint_consumers(body.consumers, proxy)
    demands = []
    for target in body.consumers:
        if target.model not in benchmark["aliases"]:
            raise HTTPException(422, "This alias does not route exclusively to the measured deployment")
        if target.source == "submitted":
            rows = await forecasts(db, target, body.on_date)
            demand = rows[0]["rates"] if rows else None
        else:
            demand = (await observed(proxy, target, body.days))[target.source]
        if demand is None or any(demand[k] is None for k in DIMENSIONS):
            raise HTTPException(422, f"No complete {target.source} demand for {target.subject} / {target.model}")
        demands.append(demand)
    allocation = split_capacity(benchmark["rates"], demands, body.reserve_percent)
    accounting = await token_accounting(proxy)
    recs = allocation.pop("recommendations")
    return {
        **allocation,
        "basis": workload_basis(benchmark, body.consumers, demands),
        "profile_ratio_tolerance": 0.25,
        "baseline": benchmark,
        "accounting": accounting,
        "consumers": [
            {
                **c.model_dump(),
                "demand": d,
                "recommended": r,
                "counted_tpm": counted_tpm(r, accounting),
                "current_limits": await live_limits(proxy, c),
            }
            for c, d, r in zip(body.consumers, demands, recs)
        ],
    }


class ModelLimitPatch(DemandScope):
    rpm: int | None = Field(ge=1, le=2**31 - 1)
    tpm: int | None = Field(ge=1, le=2**31 - 1)
    acknowledge_key_override: bool = False
    expected_rpm: int | None
    expected_tpm: int | None


@router.patch("/limits")
async def save_model_limits(
    body: ModelLimitPatch,
    _admin=Depends(require_super_user),
    db=Depends(get_db),
    proxy=Depends(get_litellm_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    if body.scope not in {"team", "key"}:
        raise HTTPException(422, "Only a per-model team or key limit can be changed here")
    # Serialize portal writers across aliases: LiteLLM replaces each metric's entire map.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": json.dumps(["model_limits", body.scope, body.subject])},
    )
    if body.scope == "key":
        row = await key_record(proxy, body.subject)
        if not row:
            raise HTTPException(404, "Key unavailable")
        limits = effective_model_limits(row["metadata"], row["inherited"])
    else:
        row = (
            await proxy.execute(
                text('SELECT metadata FROM "LiteLLM_TeamTable" WHERE team_id=:id'), {"id": body.subject}
            )
        ).first()
        if not row:
            raise HTTPException(404, "Team unavailable")
        limits = effective_model_limits(row[0], {})
    changes = {}
    for metric, expected, value in (("rpm", body.expected_rpm, body.rpm), ("tpm", body.expected_tpm, body.tpm)):
        field = f"model_{metric}_limit"
        current = dict(limits[field] or {})
        if current.get(body.model) != expected:
            raise HTTPException(409, "The model limit changed. Reload before saving")
        if value != expected:
            if body.scope == "key" and limits[f"model_{metric}_inherited"]:
                raise HTTPException(409, f"This key inherits the team {metric.upper()} map. Edit the team limit.")
            if (
                body.scope == "key"
                and row["team_id"]
                and not (row["metadata"] or {}).get(field)
                and not body.acknowledge_key_override
            ):
                raise HTTPException(409, "Confirm the new key override for this metric before saving")
            if value is None:
                current.pop(body.model, None)
            else:
                current[body.model] = value
            changes[field] = current
    if changes:
        update = litellm.update_team if body.scope == "team" else litellm.update_key
        try:
            await update(body.subject, **changes)
        except Exception as exc:
            raise HTTPException(502, "LiteLLM could not confirm the limit update. Refresh before retrying") from exc
    return {"scope": body.scope, "subject": body.subject, "model": body.model, "rpm": body.rpm, "tpm": body.tpm}
