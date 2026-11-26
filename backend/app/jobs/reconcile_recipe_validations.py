"""One durable transition at a time; external resources have deterministic identities.

The row lock serializes workers, cancellation and candidate additions. Plans/IDs are
committed before external writes; retrying an interrupted transition is idempotent.
Never advance to another candidate until its predecessor's Pods are actually gone.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_recipe_validation import (
    CustomRecipeValidation,
    CustomValidationAttempt,
    CustomValidationCandidate,
)
from app.db.session import async_session_factory
from app.services import validation_runtime as runtime
from app.services import validation_search as search
from app.services.recipe_validations import TERMINAL, save_search_winner, unpack
from app.services.validation_protocol import assess, classify_diagnostics, rank

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC)


def elapsed(start, current):
    return (current - start.replace(tzinfo=start.tzinfo or UTC)).total_seconds()


def stage(attempt, name, current):
    attempt.status, attempt.stage_started_at = name, current


def fail(attempt, code, message, current):
    attempt.error_code, attempt.error_message = code, message[:2000]
    attempt.state = {**attempt.state, "outcome": "failed"}
    if attempt.state.get("applied_at") and "startup_seconds" not in attempt.state:
        attempt.state = {
            **attempt.state,
            "startup_seconds": elapsed(datetime.fromisoformat(attempt.state["applied_at"]), current),
        }
    stage(attempt, "collecting", current)


def measurement_plan(conditions):
    performance = conditions["performance"]
    plan = [
        (
            f"performance-{i + 1}",
            "performance",
            {
                **performance,
                "seed": performance.get("seed", 0) + i,
                **({"tokenizer": conditions["tokenizer"]} if conditions.get("tokenizer") else {}),
            },
        )
        for i in range(conditions["repeats"])
    ]
    if conditions.get("capacity_search"):
        options = {**conditions["capacity_search"], "repeats": conditions["repeats"]}
        plan = [("capacity-search", "performance", {**plan[0][2], "capacity_search": options})]
    if conditions.get("prefix_len"):
        plan.append(
            (
                "prefix_cache",
                "performance",
                {
                    "experiment": "prefix_cache",
                    "prefix_len": conditions["prefix_len"],
                    **{
                        k: performance[k]
                        for k in ("random_input_len", "random_output_len", "num_prompts", "max_concurrency", "seed")
                    },
                },
            )
        )
    if conditions.get("accuracy"):
        plan.append(("accuracy", "accuracy", conditions["accuracy"]))
    return plan


def runtime_images(evidence):
    return {
        role: sorted(
            {c["image_id"] for p in (info.get("pods") or []) for c in p.get("containers", []) if c.get("image_id")}
        )
        for role, info in evidence.items()
    }


async def tick(db, job, *, current=None):
    current = current or now()
    if job.status in TERMINAL:
        return
    candidates = list(
        (
            await db.scalars(
                select(CustomValidationCandidate)
                .where(CustomValidationCandidate.validation_id == job.id)
                .order_by(CustomValidationCandidate.position)
            )
        ).all()
    )
    attempts = list(
        (
            await db.scalars(
                select(CustomValidationAttempt)
                .where(CustomValidationAttempt.validation_id == job.id)
                .order_by(CustomValidationAttempt.started_at)
            )
        ).all()
    )
    active = next((a for a in attempts if not a.finished_at), None)
    job.started_at = job.started_at or current
    timed_out = elapsed(job.started_at, current) >= job.conditions["time_limits"]["total"]
    if timed_out:
        job.status, job.message = "cancelling", "Total validation deadline exceeded; releasing resources"
    if job.status == "cancelling":
        if not active:
            job.status, job.phase, job.finished_at = ("failed" if timed_out else "cancelled"), "finished", current
            return
        if active.status not in ("collecting", "cleaning"):
            fail(
                active,
                "total_timeout" if timed_out else "cancelled",
                job.message or "Cancelled by administrator",
                current,
            )
    searching = job.conditions.get("search")
    if searching and job.status != "cancelling":
        progress = search.progress(job, candidates, current=current)
        if progress["remaining_search_seconds"] == 0 and not progress["closed"]:
            search.close(job, "search_time_limit", current)
            for queued in candidates:
                if queued.status == "queued":
                    queued.status = "skipped"
            if active and active.purpose == "compare" and active.status not in ("collecting", "cleaning"):
                fail(
                    active,
                    "search_time_limit",
                    "Search deadline reached; preserving the best completed candidate",
                    current,
                )
    if not active:
        queued = next((c for c in candidates if c.status == "queued"), None)
        if searching and not queued:
            progress = search.progress(job, candidates, current=current)
            if progress["stop_reason"]:
                search.close(job, progress["stop_reason"], current)
            else:
                job.status, job.phase, job.message = (
                    "awaiting_input",
                    "awaiting_planner",
                    "MCP client can propose the first recipe, improve a measured candidate, or finish the search.",
                )
                return
        elif job.status == "awaiting_input":
            return
        candidate = queued
        purpose = "compare"
        if not candidate:
            rejected = {a.candidate_id for a in attempts if a.purpose == "final" and a.finished_at}
            eligible = [c for c in rank(candidates, job.conditions["selection_metric"]) if c.id not in rejected]
            if not eligible:
                if searching and (job.search_state or {}).get("closed_reason"):
                    job.status, job.phase, job.finished_at = "failed", "finished", current
                    job.message = (
                        "Search ended without a candidate that passed final validation; diagnostics are retained."
                    )
                    return
                job.status, job.phase, job.message = (
                    "awaiting_input",
                    "awaiting_input",
                    "No eligible candidate remains. Inspect diagnostics and add a corrected candidate.",
                )
                return
            candidate, purpose = eligible[0], "final"
            job.selected_candidate_id = candidate.id
        active = CustomValidationAttempt(
            id=uuid.uuid4(),
            validation_id=job.id,
            candidate_id=candidate.id,
            number=1 + sum(a.candidate_id == candidate.id for a in attempts),
            purpose=purpose,
            status="preflight",
            state={},
            started_at=current,
            stage_started_at=current,
        )
        db.add(active)
        candidate.status = "validating" if purpose == "compare" else "revalidating"
        job.status, job.phase, job.message = "running", "preflight", None
        return
    candidate = next(c for c in candidates if c.id == active.candidate_id)
    dep = await db.get(CustomModelDeployment, active.deployment_id) if active.deployment_id else None
    job.phase = active.status
    previous = next(
        (
            a
            for a in attempts
            if a.candidate_id == candidate.id and a.purpose == "compare" and a.state.get("outcome") == "passed"
        ),
        None,
    )
    limits = job.conditions["time_limits"]
    timeout = {
        "preflight": limits["assets"],
        "applying": limits["scheduling"],
        "scheduling": limits["scheduling"],
        "starting": limits["startup"],
        "smoke": limits["measurement"],
        "measuring": limits["measurement"],
        "publishing": limits["measurement"],
    }.get(active.status)
    if timeout and elapsed(active.stage_started_at, current) > timeout:
        fail(active, f"{active.status}_timeout", f"{active.status} exceeded its {timeout}s time limit", current)
        return
    if not dep and active.status == "preflight":
        dep = await runtime.prepare(db, job, candidate, active)
        if active.purpose == "final" and previous:
            runtime.apply_pins(dep, previous.state["assets"])
            active.state = {**active.state, "restore_assets": previous.state["assets"]}
        return  # Persist deployment/router identities before any external action.
    if active.status == "preflight":
        assets = await runtime.preflight(db, job, candidate, active, dep)
        if assets is None:
            return
        if active.purpose == "final" and (
            not previous or not runtime.restored_assets_match(assets, previous.state.get("assets"))
        ):
            fail(active, "asset_changed", "The restored candidate's assets differ from the measured snapshot", current)
            return
        reference = next((a.state["assets"] for a in attempts if a.id != active.id and a.state.get("assets")), None)
        if reference:
            measured_model = assets.get("1", assets.get("0", {})).get("weights_sha256")
            reference_model = reference.get("1", reference.get("0", {})).get("weights_sha256")
            if job.conditions.get("version", 1) == 1 and measured_model != reference_model and not job.conditions.get("quality_gates"):
                fail(
                    active,
                    "quality_floor_required",
                    "Different model assets require a new validation with predefined quality floors",
                    current,
                )
                return
            keys = ("weights_sha256", "image")
            if assets.get("accuracy") and any(
                assets["accuracy"].get(k) != reference.get("accuracy", {}).get(k) for k in keys
            ):
                fail(active, "evaluation_changed", "Evaluation image or datasets differ between candidates", current)
                return
            if job.conditions.get("tokenizer"):

                def workload_tokenizer(values):
                    return values.get("1", values.get("0", {})).get("tokenizer_sha256")

                if workload_tokenizer(assets) != workload_tokenizer(reference):
                    fail(
                        active, "tokenizer_changed", "The common workload tokenizer changed between candidates", current
                    )
                    return
        if job.conditions["selection_metric"] in ("output_throughput", "throughput_per_gpu"):
            tokenizer_hashes = {
                value.get("server_tokenizer_sha256")
                for trial in [assets, *[a.state.get("assets", {}) for a in attempts]]
                for key, value in trial.items()
                if key in ("0", "1")
                if value.get("server_tokenizer_sha256")
            }
            if len(tokenizer_hashes) > 1:
                fail(
                    active,
                    "incomparable_tokenizers",
                    "Server tokenizers differ; create a new validation using request throughput or TTFT.",
                    current,
                )
                return
        runtime.apply_pins(dep, assets)
        active.state = {**active.state, "assets": assets}
        stage(active, "applying", current)
    elif active.status == "applying":
        rollout = await runtime.apply(db, job, active, dep)
        active.state = {**active.state, "rollout": rollout, "applied_at": current.isoformat()}
        stage(active, "scheduling", current)
    elif active.status in ("scheduling", "starting"):
        status, evidence = await runtime.observe(db, job, active, dep, candidate)
        active.diagnostics = evidence
        dep.status = "Ready" if status["ready"] else "Pending"
        dep.ready_replicas = sum(p.get("ready", 0) for p in status["pools"].values())
        dep.last_synced_at = current
        reason = classify_diagnostics(evidence)
        job.message = reason
        if any(p.get("status") == "Superseded" for p in status["pools"].values()):
            fail(active, "superseded", "A validation workload was changed outside its parent job", current)
        elif reason in ("gpu_oom", "memory_limit", "configuration_error", "restarting", "probe_restart"):
            fail(active, reason, "Candidate failed during startup; diagnostic evidence is retained", current)
        elif status["ready"]:
            images = runtime_images(evidence)
            if active.purpose == "final" and previous and images != previous.state.get("runtime_images"):
                fail(
                    active,
                    "image_changed",
                    "A restored engine/router image differs from the measured instance",
                    current,
                )
                return
            active.state = {
                **active.state,
                "runtime_images": images,
                "startup_seconds": elapsed(datetime.fromisoformat(active.state["applied_at"]), current),
            }
            stage(active, "smoke", current)
        elif active.status == "scheduling" and all(
            info.get("pods") and all(p.get("scheduled") for p in info["pods"])
            for role, info in evidence.items()
            if role != "router"
        ):
            stage(active, "starting", current)
    elif active.status in ("smoke", "measuring"):
        if active.status == "smoke":
            todo = [
                (
                    "smoke",
                    "performance",
                    {
                        "random_input_len": 16,
                        "random_output_len": 8,
                        "num_prompts": 1,
                        "max_concurrency": 1,
                        "seed": 973517,
                    },
                )
            ]
        else:
            todo = measurement_plan(job.conditions)
        completed = []
        for key, kind, params in todo:
            rid = runtime.stable_id(active, key)
            run = await db.get(CustomBenchmarkRun, rid)
            if not run:
                await runtime.prepare_benchmark(db, job, candidate, active, dep, key, params, kind)
                active.state = {**active.state, "measurements": {**active.state.get("measurements", {}), key: str(rid)}}
                active.stage_started_at = current  # Each repetition has its own measurement deadline.
                return
            if run.status not in ("succeeded", "failed", "cancelled"):
                await runtime.benchmark(db, job, active, dep, run)
                return
            if run.status != "succeeded":
                fail(active, "measurement_failed", f"{key}: {run.error_message or run.status}", current)
                return
            completed.append(run)
        if active.status == "smoke":
            if (completed[0].result or {}).get("metrics", {}).get("completed") != 1:
                fail(active, "smoke_failed", "Candidate did not produce a complete smoke response", current)
            else:
                stage(active, "measuring", current)
        else:
            assessment = assess(completed, job.conditions, candidate.gpu_count)
            active.state = {**active.state, "assessment": assessment}
            if active.purpose == "compare":
                candidate.assessment = assessment
            if not assessment["passed"]:
                fail(active, "requirements_failed", "; ".join(assessment["errors"]), current)
            elif active.purpose == "final":
                active.state = {**active.state, "registration_started": True}
                stage(active, "publishing", current)
            else:
                active.state = {**active.state, "outcome": "passed"}
                stage(active, "collecting", current)
    elif active.status == "publishing":
        smoke = await runtime.publish(db, job, active, dep)
        if searching:
            await save_search_winner(db, job, candidate, dep)
        active.state = {**active.state, "outcome": "passed", "publication": smoke}
        active.status, active.finished_at = "succeeded", current
        candidate.status = "selected"
        job.status, job.phase, job.finished_at = "succeeded", "published", current
        job.message = (
            f"Selected by {job.conditions['selection_metric']}; restored, revalidated and published. "
            "Existing LiteLLM access policies apply."
        )
    elif active.status == "collecting":
        if dep:
            try:
                _, active.diagnostics = await runtime.observe(db, job, active, dep, candidate)
            except Exception as exc:
                active.diagnostics = {**(active.diagnostics or {}), "collection_error": type(exc).__name__}
            try:
                evidence = await runtime.collect_jobs(db, job, active, dep, candidate)
                active.diagnostics = {**(active.diagnostics or {}), "jobs": evidence}
            except Exception as exc:
                active.diagnostics = {**(active.diagnostics or {}), "job_collection_error": type(exc).__name__}
            active.state = {
                **active.state,
                "effective_manifests": runtime.scrub(
                    runtime.manifests(dep, limits, assets=active.state.get("assets")), unpack(candidate)
                ),
            }
        stage(active, "cleaning", current)
    elif active.status == "cleaning":
        if await runtime.cleanup(db, job, active, dep):
            active.status = "succeeded" if active.state.get("outcome") == "passed" else "failed"
            active.finished_at = current
            candidate.status = (
                "passed" if (candidate.assessment or {}).get("passed") and active.purpose == "compare" else "failed"
            )
            job.phase = "queued"


async def reconcile_once():
    async with async_session_factory() as db:
        ids = list(
            (
                await db.scalars(
                    select(CustomRecipeValidation.id)
                    .where(CustomRecipeValidation.status.not_in(TERMINAL))
                    .order_by(CustomRecipeValidation.created_at)
                )
            ).all()
        )
    for job_id in ids:
        async with async_session_factory() as db:
            job = await db.scalar(
                select(CustomRecipeValidation)
                .where(CustomRecipeValidation.id == job_id)
                .with_for_update(skip_locked=True)
            )
            if not job:
                continue
            try:
                await tick(db, job)
                await db.commit()
            except Exception as exc:
                # Roll back DB mutations; retries retain committed identities and resources.
                await db.rollback()
                logger.warning("Validation %s transition failed (%s)", job_id, type(exc).__name__)
                job = await db.scalar(
                    select(CustomRecipeValidation).where(CustomRecipeValidation.id == job_id).with_for_update()
                )
                active = await db.scalar(
                    select(CustomValidationAttempt).where(
                        CustomValidationAttempt.validation_id == job_id, CustomValidationAttempt.finished_at.is_(None)
                    )
                )
                if active:
                    candidate = await db.get(CustomValidationCandidate, active.candidate_id)
                    safe = runtime.scrub(str(exc), unpack(candidate))
                    if isinstance(exc, ValueError) and active.status not in ("collecting", "cleaning"):
                        fail(active, "validation_error", safe, now())
                    else:
                        job.message = f"Retrying {active.status}: {safe[:500]}"
                await db.commit()


async def reconcile_loop(interval_seconds=10):
    while True:
        try:
            await reconcile_once()
        except Exception:
            logger.exception("Recipe validation reconciliation failed")
        await asyncio.sleep(interval_seconds)
