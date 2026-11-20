import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.db.models.custom_recipe_validation import (
    CustomRecipeValidation,
    CustomValidationAttempt,
    CustomValidationCandidate,
)
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import recipe_validations as service
from app.services.benchmark_credentials import public_snapshot
from app.services.validation_protocol import (
    AttemptAnalysis,
    CandidateAdd,
    SearchFinish,
    ValidationConditions,
    ValidationCreate,
    digest,
)

router = APIRouter(prefix="/api/admin/recipe-validations", tags=["recipe-validations"])


@router.get("/search/guide")
async def search_guide(user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)):
    from pathlib import Path

    from app.api.serving_recipes import RecipeBody
    from app.services.serving_engines import DEFAULT_IMAGES

    path = Path(__file__).parents[1] / "mcp/skills/optimize-serving-recipe/SKILL.md"
    return {
        "instructions": path.read_text(),
        "engine_images": {
            engine: {"default_reference": image, "availability_verified": False}
            for engine, image in DEFAULT_IMAGES.items()
        },
        "recipe_schema": RecipeBody.model_json_schema(),
        "start_schema": ValidationCreate.model_json_schema(),
        "candidate_schema": CandidateAdd.model_json_schema(),
    }


async def _candidate_draft(job, body, user, db):
    from app.api.serving_recipes import RecipeBody, _serialize
    from app.db.models.custom_serving_recipe import CustomServingRecipe

    source = None
    recipe_id = body.recipe_id
    if body.source_candidate_id:
        source = await db.get(CustomValidationCandidate, uuid.UUID(body.source_candidate_id))
        if not source or source.validation_id != job.id:
            raise HTTPException(404, "Candidate not found in this validation")
        original, recipe_id = service.unpack(source)["recipe"], source.recipe_id
    elif body.recipe is not None:
        if set(body.recipe) - set(RecipeBody.model_fields):
            raise HTTPException(400, "Unknown recipe fields")
        original = body.recipe
    else:
        recipe = await db.get(CustomServingRecipe, uuid.UUID(body.recipe_id))
        if not recipe:
            raise HTTPException(404, "Recipe not found")
        original = RecipeBody.model_validate(_serialize(recipe)).model_dump(mode="json")
    if set(body.patch) - set(RecipeBody.model_fields):
        raise HTTPException(400, "Only recipe fields can be changed; evaluation conditions are immutable")
    draft = service.merge_patch(original, body.patch)
    protocol = ValidationConditions.model_validate(job.conditions)
    snap = await service.snapshot(db, recipe_id, protocol, job.cluster_id, job.namespace, user, recipe=draft)
    return original, snap, source, uuid.UUID(str(recipe_id)) if recipe_id else None


@router.post("/{job_id}/candidate-preview")
async def preview_candidate(
    job_id: str, body: CandidateAdd, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    from app.services.manifest_diff import dump, side_by_side

    job = await service.get_job(db, job_id)
    original, snap, _, _ = await _candidate_draft(job, body, user, db)
    candidates = (
        await db.scalars(select(CustomValidationCandidate).where(CustomValidationCandidate.validation_id == job.id))
    ).all()
    service.check_comparability(
        [service.unpack(c) for c in candidates] + [snap], ValidationConditions.model_validate(job.conditions)
    )
    rows = side_by_side(
        dump(public_snapshot(original if body.recipe is None else {})), dump(public_snapshot(snap["recipe"]))
    )
    return {
        "snapshot": public_snapshot(snap),
        "diff": {
            "kind": "Recipe",
            "name": snap["recipe"]["name"],
            "changed": sum(r["op"] != "equal" for r in rows),
            "rows": rows,
        },
    }


async def _snapshots(body, user, db):
    cid, namespace = await service.scope(db, body.cluster_id, body.namespace)
    snapshots = [
        await service.snapshot(db, c.recipe_id, body.conditions, cid, namespace, user, recipe=c.recipe)
        for c in body.candidates
    ]
    service.check_comparability(snapshots, body.conditions)
    if body.conditions.search:
        from app.services.validation_search import fingerprint

        hashes = [fingerprint(s["recipe"]) for s in snapshots]
        if len(hashes) != len(set(hashes)):
            raise HTTPException(409, "Duplicate recipe configurations in search")
    return cid, namespace, snapshots


@router.post("/preview")
async def preview_validation(
    body: ValidationCreate, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    cid, ns, snaps = await _snapshots(body, user, db)
    return {
        "cluster_id": str(cid) if cid else None,
        "namespace": ns,
        "max_gpu_usage": max((s["gpu_count"] for s in snaps), default=body.conditions.gpu_limit),
        "candidates": [public_snapshot(s) for s in snaps],
        "conditions": body.conditions.model_dump(mode="json"),
    }


@router.post("", status_code=201)
async def create_validation(
    body: ValidationCreate, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    request_hash = digest(body.model_dump(mode="json", exclude={"request_key"}))
    existing = await db.scalar(
        select(CustomRecipeValidation).where(
            CustomRecipeValidation.created_by == user.user_id, CustomRecipeValidation.request_key == body.request_key
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key already used with different conditions")
        return await service.detail(db, existing)
    cid, ns, snapshots = await _snapshots(body, user, db)
    job_id = uuid.uuid4()
    inserted = await db.scalar(
        insert(CustomRecipeValidation)
        .values(
            id=job_id,
            name=body.name,
            model_name=body.model_name,
            cluster_id=cid,
            namespace=ns,
            conditions=body.conditions.model_dump(mode="json"),
            request_key=body.request_key,
            request_hash=request_hash,
            status="queued",
            phase="queued",
            created_by=user.user_id,
        )
        .on_conflict_do_nothing(index_elements=["created_by", "request_key"])
        .returning(CustomRecipeValidation.id)
    )
    if not inserted:
        existing = await db.scalar(
            select(CustomRecipeValidation).where(
                CustomRecipeValidation.created_by == user.user_id,
                CustomRecipeValidation.request_key == body.request_key,
            )
        )
        if existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key already used with different conditions")
        return await service.detail(db, existing)
    job = await service.get_job(db, job_id)
    for i, (candidate, snap) in enumerate(zip(body.candidates, snapshots)):
        db.add(
            service.new_candidate(
                job, snap, i, f"initial-{i}", recipe_id=uuid.UUID(candidate.recipe_id) if candidate.recipe_id else None
            )
        )
    await db.flush()
    return await service.detail(db, job)


@router.get("")
async def list_validations(
    cluster_id: str | None = None,
    namespace: str | None = None,
    status: str | None = None,
    q: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(CustomRecipeValidation)
    if cluster_id is not None:
        try:
            cid = uuid.UUID(cluster_id) if cluster_id else None
        except ValueError:
            raise HTTPException(400, "Invalid cluster ID")
        query = query.where(CustomRecipeValidation.cluster_id == cid)
    if namespace:
        query = query.where(CustomRecipeValidation.namespace == namespace)
    if status:
        query = query.where(CustomRecipeValidation.status == status)
    if q:
        query = query.where(
            CustomRecipeValidation.model_name.ilike(f"%{q}%") | CustomRecipeValidation.name.ilike(f"%{q}%")
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    column = {
        k: getattr(CustomRecipeValidation, k) for k in ("created_at", "name", "model_name", "status", "namespace")
    }.get(sort)
    if column is None or direction not in ("asc", "desc"):
        raise HTTPException(400, "Invalid sort")
    jobs = (
        await db.scalars(
            query.order_by(column.desc() if direction == "desc" else column.asc(), CustomRecipeValidation.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    ids = [j.id for j in jobs]
    candidates = (
        (
            await db.scalars(select(CustomValidationCandidate).where(CustomValidationCandidate.validation_id.in_(ids)))
        ).all()
        if ids
        else []
    )
    return {
        "total": total,
        "items": [
            {
                **service.serialize_job(j),
                "candidate_count": sum(c.validation_id == j.id for c in candidates),
                "completed_count": sum(
                    c.validation_id == j.id and c.status in ("passed", "failed", "selected", "revalidating")
                    for c in candidates
                ),
                "selected_candidate": next((c.name for c in candidates if c.id == j.selected_candidate_id), None),
            }
            for j in jobs
        ],
    }


@router.get("/{job_id}")
async def get_validation(
    job_id: str, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    return await service.detail(db, await service.get_job(db, job_id))


@router.post("/{job_id}/cancel")
async def cancel_validation(
    job_id: str, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    job = await service.get_job(db, job_id, lock=True)
    if job.status not in service.TERMINAL:
        job.status, job.message = "cancelling", "Cancellation requested; retaining diagnostics and releasing resources"
    await db.flush()
    return await service.detail(db, job)


@router.post("/{job_id}/candidates")
async def add_candidate(
    job_id: str, body: CandidateAdd, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    job = await service.get_job(db, job_id, lock=True)
    existing = await db.scalar(
        select(CustomValidationCandidate).where(
            CustomValidationCandidate.validation_id == job.id, CustomValidationCandidate.request_key == body.request_key
        )
    )
    if existing:
        if service.unpack(existing).get("add_request_hash") != digest(body.model_dump(mode="json")):
            raise HTTPException(409, "Candidate idempotency key already used")
        return await service.detail(db, job)
    if job.status != "awaiting_input":
        raise HTTPException(409, "Add candidates while the validation is waiting for administrator input")
    from app.services.validation_runtime import scrub
    from app.services.validation_search import check_submission, fingerprint

    candidates = (
        await db.scalars(select(CustomValidationCandidate).where(CustomValidationCandidate.validation_id == job.id))
    ).all()
    check_submission(job, candidates, body.expected_candidate_count)
    _, snap, source, recipe_id = await _candidate_draft(job, body, user, db)
    protocol = ValidationConditions.model_validate(job.conditions)
    service.check_comparability([service.unpack(c) for c in candidates] + [snap], protocol)
    if protocol.search:
        if not body.hypothesis or not body.hypothesis.strip():
            raise HTTPException(400, "Explain the baseline or expected improvement in hypothesis")
        if any(fingerprint(service.unpack(c)["recipe"]) == fingerprint(snap["recipe"]) for c in candidates):
            raise HTTPException(409, "This recipe configuration was already tried; inspect its existing result")
    snap["add_request_hash"] = digest(body.model_dump(mode="json"))
    snap["hypothesis"] = scrub(body.hypothesis, snap) if body.hypothesis else None
    db.add(
        service.new_candidate(
            job,
            snap,
            max((c.position for c in candidates), default=-1) + 1,
            body.request_key,
            recipe_id=recipe_id,
            parent_id=source.id if source else None,
        )
    )
    # Resume explicitly through this action; the original total deadline still applies.
    job.status, job.phase, job.message = "running", "queued", None
    await db.flush()
    return await service.detail(db, job)


@router.put("/{job_id}/attempts/{attempt_id}/analysis")
async def explain_attempt(
    job_id: str,
    attempt_id: str,
    body: AttemptAnalysis,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
):
    job = await service.get_job(db, job_id, lock=True)
    try:
        attempt = await db.get(CustomValidationAttempt, uuid.UUID(attempt_id))
    except ValueError:
        raise HTTPException(404, "Attempt not found")
    if not attempt or attempt.validation_id != job.id:
        raise HTTPException(404, "Attempt not found")
    from app.api.serving_recipes import RecipeBody

    if set(body.proposed_patch) - set(RecipeBody.model_fields):
        raise HTTPException(400, "Proposals can change only recipe fields")
    from app.services.validation_runtime import scrub

    candidate = await db.get(CustomValidationCandidate, attempt.candidate_id)
    attempt.analysis = {
        **scrub(public_snapshot(body.model_dump()), service.unpack(candidate)),
        "author": user.user_id,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await db.flush()
    return await service.detail(db, job)


@router.post("/{job_id}/finish-search")
async def finish_search(
    job_id: str, body: SearchFinish, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    from app.services.validation_runtime import scrub
    from app.services.validation_search import close

    job = await service.get_job(db, job_id, lock=True)
    if not job.conditions.get("search"):
        raise HTTPException(409, "This job is a fixed recipe comparison")
    if (job.search_state or {}).get("closed_reason"):
        return await service.detail(db, job)
    candidates = (
        await db.scalars(select(CustomValidationCandidate).where(CustomValidationCandidate.validation_id == job.id))
    ).all()
    if body.expected_candidate_count != len(candidates) or job.status != "awaiting_input":
        raise HTTPException(
            409, "Read current search context; finish only after all candidate measurements and cleanup"
        )
    reason = body.reason
    for candidate in candidates:
        reason = scrub(reason, service.unpack(candidate))
    close(job, "client_finished", datetime.now(UTC))
    job.search_state = {**job.search_state, "explanation": reason}
    job.status, job.phase, job.message = "running", "selecting", None
    await db.flush()
    return await service.detail(db, job)


@router.get("/{job_id}/search-context")
async def search_context(
    job_id: str, user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)
):
    from app.services.validation_search import progress

    job = await service.get_job(db, job_id)
    if not job.conditions.get("search"):
        raise HTTPException(409, "This job is a fixed recipe comparison")
    candidates = (
        await db.scalars(
            select(CustomValidationCandidate)
            .where(CustomValidationCandidate.validation_id == job.id)
            .order_by(CustomValidationCandidate.position)
        )
    ).all()
    attempts = (
        await db.scalars(
            select(CustomValidationAttempt)
            .where(CustomValidationAttempt.validation_id == job.id)
            .order_by(CustomValidationAttempt.started_at)
        )
    ).all()
    # Polling does not load large benchmark results, manifests or retained logs.
    return {
        **service.serialize_job(job),
        "search_progress": progress(job, candidates),
        "candidates": [
            {
                "id": str(c.id),
                "name": c.name,
                "status": c.status,
                "position": c.position,
                "parent_candidate_id": str(c.parent_candidate_id) if c.parent_candidate_id else None,
                "assessment": c.assessment,
                "hypothesis": service.unpack(c).get("hypothesis"),
                "attempts": [
                    {
                        "id": str(a.id),
                        "purpose": a.purpose,
                        "status": a.status,
                        "error_code": a.error_code,
                        "error_message": a.error_message,
                    }
                    for a in attempts
                    if a.candidate_id == c.id
                ],
            }
            for c in candidates
        ],
        "portal_path": f"/admin/benchmarks/validations/{job.id}",
    }
