"""Orchestration contract tests with real SQL records and controlled external runtimes.

SQLite keeps this suite independent of Docker/GPU availability; PostgreSQL migration
and locking are also covered by the opt-in integration test described in the runbook.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api import recipe_validations as api
from app.api.model_deployments import require_validation_editable
from app.api.serving_recipes import RecipeBody
from app.db.base import CustomBase
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_recipe_validation import (
    CustomValidationAttempt,
    CustomValidationCandidate,
)
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser, GlobalRole
from app.jobs.reconcile_recipe_validations import measurement_plan, tick
from app.services import recipe_validations as service
from app.services import validation_runtime as runtime
from app.services.validation_protocol import (
    CandidateAdd,
    ValidationConditions,
    ValidationCreate,
    assess,
    classify_diagnostics,
    rank,
)


@compiles(JSONB, "sqlite")
def jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


class TestSession:
    __test__ = False

    def __init__(self, sync):
        self.sync = sync

    def add(self, obj):
        self.sync.add(obj)

    async def execute(self, *args, **kwargs):
        return self.sync.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self.sync.scalar(*args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return self.sync.scalars(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return self.sync.get(*args, **kwargs)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()

    def expire_all(self):
        self.sync.expire_all()

    async def refresh(self, obj):
        self.sync.refresh(obj)


@pytest.fixture
async def db():
    url = os.environ.get("VALIDATION_TEST_DATABASE_URL")
    if url:
        assert make_url(url).database == "recipe_validation_test", "Use the isolated validation test database"
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as session:
                yield session
            await transaction.rollback()
        await engine.dispose()
        return
    engine = create_engine("sqlite://")
    names = {
        "custom_recipe_validation",
        "custom_validation_candidate",
        "custom_validation_attempt",
        "custom_model_deployment",
        "custom_benchmark_run",
        "custom_serving_recipe",
        "custom_llmd_stack",
        "custom_k8s_cluster",
        "custom_gpu_profile",
        "custom_portal_settings",
        "custom_storage",
        "custom_storage_binding",
    }
    CustomBase.metadata.create_all(
        engine, tables=[t for name, t in CustomBase.metadata.tables.items() if name in names]
    )
    with Session(engine, expire_on_commit=False) as session:
        yield TestSession(session)
    engine.dispose()


@pytest.fixture
def user():
    return CustomUser(user_id="validation-admin", global_role=GlobalRole.SUPER_USER)


@pytest.fixture
def protocol():
    return ValidationConditions(
        version=2, ingress_host="model.example.test", performance={"num_prompts": 2, "max_concurrency": 1}
    ).model_dump(mode="json")


async def create_job(db, user, protocol, *, count=2):
    cluster = CustomK8sCluster(
        id=uuid4(), name="non-default", context="test", namespace="serving-b", kubeconfig_encrypted="unused"
    )
    db.add(cluster)
    recipes = []
    for i in range(count):
        recipe = CustomServingRecipe(
            id=uuid4(),
            **RecipeBody(
                name=f"recipe-{i}",
                hf_repo_id="example/model",
                image="registry/engine:v1",
                model_path="/models/model",
                gpu_count=2,
                engine_args={"tensor-parallel-size": 2},
                env={"API_KEY": "private-test-value"},
            ).model_dump(),
        )
        db.add(recipe)
        recipes.append(recipe)
    await db.flush()
    body = ValidationCreate(
        request_key="test-create",
        name="Validation",
        model_name="test-alias",
        cluster_id=str(cluster.id),
        candidates=[{"recipe_id": str(r.id)} for r in recipes],
        conditions=protocol,
    )
    result = await api.create_validation(body, user, db)
    return await service.get_job(db, result["id"]), body, recipes


def run_result(protocol, throughput=100, quality=None):
    return {
        "metrics": {
            "completed": protocol["performance"]["num_prompts"],
            "failed": 0,
            "output_throughput": throughput,
            "p99_ttft_ms": 25,
            "request_throughput": 2,
            **({"results": quality} if quality else {}),
        }
    }


def test_assessment_uses_medians_and_never_ranks_partial_results(protocol):
    runs = [
        SimpleNamespace(id=uuid4(), kind="performance", params={}, status="succeeded", result=run_result(protocol, v))
        for v in (90, 300, 100)
    ]
    result = assess(runs, protocol, 2)
    assert result["passed"] and result["metrics"]["output_throughput"] == 100
    assert result["metrics"]["throughput_per_gpu"] == 50
    runs[-1].result["metrics"]["completed"] = 1
    assert not assess(runs, protocol, 2)["passed"]
    assert not assess(runs[:2], protocol, 2)["passed"]
    runs[-1].result["metrics"]["output_throughput"] = float("nan")
    assert not assess(runs, protocol, 2)["passed"]
    candidates = [
        SimpleNamespace(position=i, assessment={"passed": i != 0, "metrics": {"output_throughput": v}})
        for i, v in enumerate((900, 100, 100))
    ]
    assert [c.position for c in rank(candidates, "output_throughput")] == [1, 2]


def test_legacy_alternative_quality_and_gpu_comparability(protocol):
    protocol["version"] = 1
    snaps = [{"recipe": {"model_path": "a"}, "gpu_count": 2}, {"recipe": {"model_path": "b"}, "gpu_count": 4}]
    with pytest.raises(HTTPException, match="quality"):
        service.check_comparability(snaps, ValidationConditions.model_validate(protocol))
    protocol.update(accuracy={"tasks": "mmlu"}, quality_gates=[{"task": "mmlu", "minimum": 0.5}])
    with pytest.raises(HTTPException, match="GPU"):
        service.check_comparability(snaps, ValidationConditions.model_validate(protocol))
    protocol["selection_metric"] = "request_throughput"
    service.check_comparability(snaps, ValidationConditions.model_validate(protocol))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CUDA out of memory", "gpu_oom"),
        ("OOMKilled", "memory_limit"),
        ("FailedScheduling", "scheduling"),
        ("startup probe failed", "probe_restart"),
        ("ImagePullBackOff", "image_unavailable"),
    ],
)
def test_diagnosis_categories(text, expected):
    assert classify_diagnostics({"logs": text}) == expected


async def test_creation_pins_scope_recipe_and_is_idempotent(db, user, protocol):
    job, body, recipes = await create_job(db, user, protocol)
    replay = await api.create_validation(body, user, db)
    assert replay["id"] == str(job.id) and len(replay["candidates"]) == 2
    assert job.namespace == "serving-b" and job.cluster_id == UUID(body.cluster_id)
    candidate = await db.get(CustomValidationCandidate, UUID(replay["candidates"][0]["id"]))
    snap = service.unpack(candidate)
    assert snap["deployment"]["namespace"] == "serving-b"
    assert snap["deployment"]["cluster_id"] == body.cluster_id
    assert "private-test-value" not in json.dumps(replay)
    assert "private-test-value" not in candidate.snapshot_encrypted
    recipes[0].image = "registry/engine:mutated"
    await db.flush()
    assert service.unpack(candidate)["deployment"]["image"] == "registry/engine:v1"
    changed = body.model_copy(update={"name": "different"})
    with pytest.raises(HTTPException) as error:
        await api.create_validation(changed, user, db)
    assert error.value.status_code == 409


async def test_failure_cleanup_restore_and_publication_use_single_measurement_records(db, user, protocol, monkeypatch):
    job, _, _ = await create_job(db, user, protocol)
    clock = datetime.now(UTC)
    active_gpu = set()
    applied = []
    published = []
    cleanup_calls = {}
    assets = {"0": {"weights_sha256": "weights", "image": "registry/engine@sha256:" + "a" * 64}}

    async def apply(db, j, attempt, dep):
        assert j.cluster_id == job.cluster_id and dep.namespace == job.namespace
        assert not active_gpu or active_gpu == {attempt.id}
        active_gpu.add(attempt.id)
        applied.append(attempt.id)
        return {}

    async def observe(db, j, attempt, dep, candidate):
        failure = candidate.position == 0
        return {"ready": not failure, "pools": {}}, {
            "aggregated": {
                "pods": [
                    {
                        "scheduled": True,
                        "containers": [
                            {"image_id": "image", "current_log": "CUDA out of memory" if failure else "ready"}
                        ],
                    }
                ]
            }
        }

    async def measure(db, j, attempt, dep, run):
        run.status = "succeeded"
        run.result = run_result(j.conditions)
        if run.label.endswith(": smoke"):
            run.result["metrics"]["completed"] = 1

    async def clean(db, j, attempt, dep):
        # Simulates asynchronous foreground deletion, including one terminating GPU Pod.
        cleanup_calls[attempt.id] = cleanup_calls.get(attempt.id, 0) + 1
        if cleanup_calls[attempt.id] == 1:
            return False
        active_gpu.discard(attempt.id)
        return True

    async def publish(db, j, attempt, dep):
        assert attempt.purpose == "final"
        assert attempt.state["assessment"]["passed"]
        published.append(attempt.id)
        return {"ok": True, "model_id": str(runtime.stable_id(attempt, "litellm"))}

    monkeypatch.setattr(runtime, "preflight", AsyncMock(return_value=assets))
    monkeypatch.setattr(runtime, "apply", apply)
    monkeypatch.setattr(runtime, "collect_jobs", AsyncMock(return_value={}))
    monkeypatch.setattr(runtime, "observe", observe)
    monkeypatch.setattr(runtime, "benchmark", measure)
    monkeypatch.setattr(runtime, "cleanup", clean)
    monkeypatch.setattr(runtime, "publish", publish)
    for _ in range(100):
        await tick(db, job, current=clock)
        await db.flush()
        # Reload from committed SQL state to model losing all in-memory worker state.
        await db.commit()
        job_id = job.id
        db.expire_all()
        job = await service.get_job(db, job_id)
        clock += timedelta(seconds=1)
        if job.status == "succeeded":
            break
    assert job.status == "succeeded"
    detail = await service.detail(db, job)
    assert [c["status"] for c in detail["candidates"]] == ["failed", "selected"]
    assert detail["candidates"][0]["attempts"][0]["error_code"] == "gpu_oom"
    assert len(detail["candidates"][1]["attempts"]) == 2  # comparison then restoration/revalidation
    runs = list((await db.scalars(select(CustomBenchmarkRun))).all())
    assert len(runs) == 8  # smoke + three repetitions, each of two successful attempts
    assert len({r.id for r in runs}) == 8 and len(published) == 1
    assert all(r.cluster_id == job.cluster_id and r.k8s_namespace == "serving-b" for r in runs)
    assert len(applied) == 3
    await tick(db, job, current=clock)
    assert len(published) == 1


async def test_cancellation_waits_for_cleanup_and_blocks_child_mutations(db, user, protocol, monkeypatch):
    job, _, _ = await create_job(db, user, protocol, count=1)
    await tick(db, job)
    await db.flush()
    await tick(db, job)  # Persist child identity, before external work.
    await db.flush()
    attempt = await db.scalar(select(CustomValidationAttempt))
    dep = await db.get(CustomModelDeployment, attempt.deployment_id)
    with pytest.raises(HTTPException) as error:
        require_validation_editable(dep)
    assert error.value.status_code == 409
    monkeypatch.setattr(runtime, "observe", AsyncMock(return_value=({}, {"events": ["saved"]})))
    monkeypatch.setattr(runtime, "collect_jobs", AsyncMock(return_value={}))
    cleanup = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(runtime, "cleanup", cleanup)
    await api.cancel_validation(str(job.id), user, db)
    for _ in range(4):
        await tick(db, job)
        await db.flush()
    assert job.status == "cancelled" and attempt.diagnostics == {"events": ["saved"], "jobs": {}}
    assert cleanup.await_count == 2


async def test_corrected_candidate_preserves_secrets_and_shared_recipe(db, user, protocol):
    job, _, recipes = await create_job(db, user, protocol, count=1)
    candidate = await db.scalar(select(CustomValidationCandidate))
    job.status = "awaiting_input"
    body = CandidateAdd(
        request_key="fix",
        source_candidate_id=str(candidate.id),
        patch={"name": "fixed", "engine_args": {"max-model-len": 4096}},
    )
    result = await api.add_candidate(str(job.id), body, user, db)
    assert result["status"] == "running" and len(result["candidates"]) == 2
    again = await api.add_candidate(str(job.id), body, user, db)
    assert len(again["candidates"]) == 2
    added = await db.get(CustomValidationCandidate, UUID(result["candidates"][-1]["id"]))
    assert service.unpack(added)["recipe"]["env"]["API_KEY"] == "private-test-value"
    assert recipes[0].engine_args == {"tensor-parallel-size": 2}
    assert job.conditions == protocol


def test_private_manifests_have_no_ingress_and_large_startup_budget(protocol):
    dep = CustomModelDeployment(
        id=uuid4(),
        model_name="validation-isolated",
        namespace="b",
        image="engine:v1",
        model_path="/models/m",
        gpu_count=4,
        replicas=1,
        engine="vllm",
        serving_mode="aggregated",
        ingress_host="public.test",
        ingress_path="/",
        ingress_class="nginx",
        storages=[],
        gpu_resource_key="nvidia.com/gpu",
        validation_id=uuid4(),
        validation_attempt_id=uuid4(),
    )
    docs = runtime.manifests(dep, protocol["time_limits"])
    assert not any(d["kind"] == "Ingress" for d in docs)
    workload = next(d for d in docs if d["kind"] == "Deployment")
    assert workload["spec"]["strategy"] == {"type": "Recreate"}
    assert workload["spec"]["progressDeadlineSeconds"] > 7200
    probe = workload["spec"]["template"]["spec"]["containers"][0]["startupProbe"]
    assert probe["failureThreshold"] * probe["periodSeconds"] > 7200


def test_warmup_and_cache_conditions_are_separate(protocol):
    protocol["prefix_len"] = 768
    plan = measurement_plan(protocol)
    assert len(plan) == 4
    assert [p[2]["seed"] for p in plan[:3]] == [0, 1, 2]
    assert plan[-1][2]["random_input_len"] == 1024 and plan[-1][2]["prefix_len"] == 768
    assert "smoke" not in [p[0] for p in plan]


async def test_permissions_reject_non_admin(client_for_user, regular_user, protocol):
    async with client_for_user(regular_user) as client:
        response = await client.get("/api/admin/recipe-validations")
    assert response.status_code == 403


async def prepared_attempt(db, user, protocol):
    job, _, _ = await create_job(db, user, protocol, count=1)
    await tick(db, job)
    await db.flush()
    await tick(db, job)
    await db.flush()
    attempt = await db.scalar(select(CustomValidationAttempt))
    dep = await db.get(CustomModelDeployment, attempt.deployment_id)
    candidate = await db.get(CustomValidationCandidate, attempt.candidate_id)
    return job, candidate, attempt, dep


async def test_asset_jobs_are_cpu_only_private_and_preserve_storage(db, user, protocol):
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    protocol["airgap"] = True
    dep.pd_config = {
        "prefill": {"replicas": 1, "gpu_count": 2},
        "decode": {"replicas": 1, "gpu_count": 4},
        "nixl_port": 5600,
    }
    dep.serving_mode = "pd"
    jobs = runtime.asset_jobs(dep, attempt, protocol)
    assert len(jobs) == 2
    for name, manifest, credentials, index in jobs:
        pod = manifest["spec"]["template"]["spec"]
        assert not pod.get("initContainers")
        assert len(pod["containers"]) == 1
        container = pod["containers"][0]
        assert "nvidia.com/gpu" not in json.dumps(container["resources"])
        assert "private-test-value" not in json.dumps(manifest)
        assert "pip install" not in json.dumps(manifest)
        assert credentials.manifest()
        assert "HF_HUB_OFFLINE" in {e["name"] for e in container["env"]}
    docs = runtime.manifests(
        dep, protocol["time_limits"], assets={"0": {"node_name": "gpu-a"}, "1": {"node_name": "gpu-b"}}
    )
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert len(deployments) == 2 and not any(d["kind"] == "Ingress" for d in docs)
    for d, node in zip(deployments, ["gpu-a", "gpu-b"]):
        affinity = d["spec"]["template"]["spec"]["affinity"]
        assert affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
            "matchFields"
        ][0]["values"] == [node]


async def test_immutable_pins_override_every_revision_alias_and_pd_extra(db, user, protocol):
    from app.services.pd_serving import pool_row
    from app.services.serving_engines import engine_flags
    from app.services.validation_protocol import effective_flag

    _, _, _, dep = await prepared_attempt(db, user, protocol)
    dep.engine_args = {"revision": "old", "tokenizer-revision": "old-tokenizer"}
    dep.vllm_extra_args = ["--revision=mutable", "--tokenizer_revision", "mutable-tokenizer"]
    dep.serving_mode = "pd"
    dep.pd_config = {"prefill": {"pd_extras": {"args": ["--revision", "mutable-last"]}}, "decode": {}}
    assets = {
        str(i): {
            "image": f"registry/engine@sha256:{i}",
            "revision": f"pinned-{i}",
            "tokenizer_revision": "tokenizer-pin",
        }
        for i in range(2)
    }
    runtime.apply_pins(dep, assets)
    for i, role in enumerate(("prefill", "decode")):
        flags = engine_flags(pool_row(dep, role))
        assert effective_flag(flags, "--revision") == f"pinned-{i}"
        assert effective_flag(flags, "--tokenizer-revision") == "tokenizer-pin"
        assert "mutable" not in " ".join(flags)


async def test_existing_job_repairs_secret_after_interruption_without_recreate(db, user, protocol, monkeypatch):
    from kubernetes_asyncio.client.exceptions import ApiException

    from app.jobs import reconcile_benchmarks

    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    run = await runtime.prepare_benchmark(db, job, candidate, attempt, dep, "performance-1", protocol["performance"])
    k8s = SimpleNamespace(
        read_job_status=AsyncMock(return_value={"run_id": str(run.id)}),
        ensure_benchmark_job_secret=AsyncMock(),
        create_job=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "k8s_for_cluster", AsyncMock(return_value=k8s))
    monkeypatch.setattr(reconcile_benchmarks, "_drive_job", AsyncMock())
    await runtime.benchmark(db, job, attempt, dep, run)
    assert k8s.create_job.await_count == 0
    assert k8s.ensure_benchmark_job_secret.await_count == 1
    manifest = k8s.ensure_benchmark_job_secret.call_args.args[1]
    assert "private-test-value" not in json.dumps(manifest)
    env = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    assert any(e["name"] == "BENCH_VALIDATION_WARMUP" and e["value"] == "1" for e in env)
    assert any(e["name"] == "BENCH_URL" and ":80" in e.get("value", "") for e in env)
    k8s.read_job_status.side_effect = ApiException(status=404)
    run.status = "running"
    await runtime.benchmark(db, job, attempt, dep, run)
    assert run.status == "failed" and "disappeared" in run.error_message
    assert k8s.create_job.await_count == 0  # Never silently replace a lost in-flight measurement.


async def test_publish_reuses_own_litellm_id_and_never_replaces_another_registration(db, user, protocol, monkeypatch):
    from app.clients.litellm import LiteLLMClient
    from app.jobs import reconcile_deployments

    job, _, attempt, dep = await prepared_attempt(db, user, protocol)
    dep.engine_args = {"api-key": "structured-key"}
    expected = str(runtime.stable_id(attempt, "litellm"))
    k8s = SimpleNamespace(create_or_patch=AsyncMock())
    monkeypatch.setattr(runtime, "k8s_for_cluster", AsyncMock(return_value=k8s))
    monkeypatch.setattr(runtime, "ensure_route_available", AsyncMock())
    monkeypatch.setattr(runtime, "observe", AsyncMock(return_value=({"ready": True}, {})))
    info = AsyncMock(
        side_effect=[
            [{"model_info": {"id": "someone-else"}, "model_name": job.model_name}],
            [{"model_info": {"id": expected}}],
        ]
    )
    create = AsyncMock(return_value={"model_info": {"id": expected}})
    smoke = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(LiteLLMClient, "get_model_info", info)
    monkeypatch.setattr(LiteLLMClient, "create_model", create)
    monkeypatch.setattr(LiteLLMClient, "smoke_chat", smoke)
    monkeypatch.setattr(reconcile_deployments, "_ensure_catalog", AsyncMock())
    await runtime.publish(db, job, attempt, dep)
    await runtime.publish(db, job, attempt, dep)
    assert create.await_count == 1
    assert create.call_args.kwargs["model_id"] == expected
    assert create.call_args.kwargs["api_key"] == "structured-key"
    assert smoke.call_args.args == (expected,)
    assert dep.validation_state == "published" and dep.litellm_model_id == expected


async def test_final_asset_change_is_rejected_before_gpu_allocation(db, user, protocol, monkeypatch):
    job, candidate, attempt, dep = await prepared_attempt(db, user, protocol)
    attempt.purpose = "final"
    monkeypatch.setattr(runtime, "preflight", AsyncMock(return_value={"0": {"weights_sha256": "changed"}}))
    apply = AsyncMock()
    monkeypatch.setattr(runtime, "apply", apply)
    await tick(db, job)
    assert attempt.error_code == "asset_changed" and attempt.status == "collecting"
    assert apply.await_count == 0


async def test_stage_timeout_collects_before_cleanup_and_total_timeout_finishes_failed(db, user, protocol, monkeypatch):
    job, _, attempt, _ = await prepared_attempt(db, user, protocol)
    attempt.status = "starting"
    attempt.stage_started_at = datetime.now(UTC) - timedelta(hours=3)
    await tick(db, job)
    assert attempt.error_code == "starting_timeout" and attempt.status == "collecting"
    job.started_at = datetime.now(UTC) - timedelta(days=2)
    monkeypatch.setattr(runtime, "observe", AsyncMock(return_value=({}, {})))
    monkeypatch.setattr(runtime, "collect_jobs", AsyncMock(return_value={"failed-job": {"events": ["evidence"]}}))
    clean = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(runtime, "cleanup", clean)
    for _ in range(4):
        await tick(db, job)
        await db.flush()
    assert job.status == "failed"
    assert attempt.diagnostics["jobs"]["failed-job"]["events"] == ["evidence"]
    assert clean.await_count == 2


async def test_llm_analysis_does_not_echo_source_credentials(db, user, protocol):
    from app.services.validation_protocol import AttemptAnalysis

    job, _, attempt, _ = await prepared_attempt(db, user, protocol)
    await api.explain_attempt(
        str(job.id),
        str(attempt.id),
        AttemptAnalysis(
            explanation="Error with private-test-value in logs", proposed_patch={"engine_args": {"max-model-len": 4096}}
        ),
        user,
        db,
    )
    assert "private-test-value" not in json.dumps(attempt.analysis)
    assert attempt.analysis["proposed_patch"]["engine_args"]["max-model-len"] == 4096


def test_cache_partial_result_cannot_pass(protocol):
    protocol["version"] = 1
    protocol["prefix_len"] = 768
    runs = [
        SimpleNamespace(id=uuid4(), kind="performance", params={}, status="succeeded", result=run_result(protocol))
        for _ in range(3)
    ]
    runs.append(
        SimpleNamespace(
            id=uuid4(),
            kind="performance",
            params={"experiment": "prefix_cache"},
            status="succeeded",
            result={"phases": {"individual": {"completed": 2}, "shared": {"completed": 1}}},
        )
    )
    assert not assess(runs, protocol, 2)["passed"]


def test_serialized_timestamps_always_include_timezone():
    assert service._json(datetime(2026, 1, 1)).endswith("+00:00")


async def test_existing_ingress_collision_is_rejected_before_publication(monkeypatch):
    from unittest.mock import MagicMock

    existing = SimpleNamespace(
        metadata=SimpleNamespace(namespace="production", name="old-route", annotations={}),
        spec=SimpleNamespace(
            ingress_class_name="nginx",
            rules=[SimpleNamespace(host="model.example.test", http=SimpleNamespace(paths=[SimpleNamespace(path="/")]))],
        ),
    )
    scope = MagicMock()
    scope.__aenter__ = AsyncMock(return_value=object())
    scope.__aexit__ = AsyncMock(return_value=False)
    k8s = SimpleNamespace(_api_client=AsyncMock(return_value=scope))
    networking = SimpleNamespace(
        list_ingress_for_all_namespaces=AsyncMock(return_value=SimpleNamespace(items=[existing]))
    )
    monkeypatch.setattr(runtime.client, "NetworkingV1Api", lambda _: networking)
    docs = [
        {
            "metadata": {"name": "new-route"},
            "spec": {
                "ingressClassName": "nginx",
                "rules": [{"host": "model.example.test", "http": {"paths": [{"path": "/v1"}]}}],
            },
        }
    ]
    with pytest.raises(ValueError, match="overlaps"):
        await runtime.ensure_route_available(k8s, "validation", docs)
    existing.metadata.name, existing.metadata.namespace = "new-route", "validation"
    await runtime.ensure_route_available(k8s, "validation", docs)  # Retrying our own publication is allowed.


async def test_asset_placement_excludes_nodes_too_small_for_the_gpu_pool(monkeypatch):
    from unittest.mock import MagicMock

    def node(name, gpus):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, labels={"gpu-type": "h200"}),
            spec=SimpleNamespace(unschedulable=False),
            status=SimpleNamespace(allocatable={"nvidia.com/gpu": str(gpus)}),
        )

    core = SimpleNamespace(
        list_node=AsyncMock(return_value=SimpleNamespace(items=[node("small", 4), node("large", 8)]))
    )
    monkeypatch.setattr(runtime.client, "CoreV1Api", lambda _: core)
    scope = MagicMock()
    scope.__aenter__ = AsyncMock(return_value=object())
    scope.__aexit__ = AsyncMock(return_value=False)
    k8s = SimpleNamespace(_api_client=AsyncMock(return_value=scope))
    dep = SimpleNamespace(serving_mode="aggregated", gpu_count=8, replicas=1, gpu_resource_key="nvidia.com/gpu")
    manifest = {"spec": {"template": {"spec": {"nodeSelector": {"gpu-type": "h200"}}}}}
    await runtime.asset_placement(k8s, dep, 0, manifest, {})
    assert "large" in json.dumps(manifest) and "small" not in json.dumps(manifest)
    dep.gpu_count = 16
    with pytest.raises(ValueError, match="No schedulable node"):
        await runtime.asset_placement(k8s, dep, 0, manifest, {})


async def test_mcp_uses_the_same_create_and_resume_endpoints(protocol, monkeypatch):
    from app.mcp import tools

    run = AsyncMock(return_value={"id": "same-durable-job"})
    monkeypatch.setattr(tools, "_run", run)
    body = ValidationCreate(
        request_key="shared-client-key",
        name="Shared",
        model_name="alias",
        candidates=[{"recipe_id": str(uuid4())}],
        conditions=protocol,
    )
    assert await tools.start_recipe_validation(body) == {"id": "same-durable-job"}
    assert run.call_args.args == (api.create_validation,)
    assert run.call_args.kwargs["body"].request_key == "shared-client-key"
    patch = CandidateAdd(request_key="patch", source_candidate_id=str(uuid4()), patch={"name": "fixed"})
    await tools.add_recipe_validation_candidate("same-durable-job", patch)
    assert run.call_args.args == (api.add_candidate,)
    assert run.call_args.kwargs == {"job_id": "same-durable-job", "body": patch}


@pytest.mark.parametrize("version", [1, 2])
async def test_weight_changes_keep_legacy_quality_gate_only(db, user, protocol, monkeypatch, version):
    job, candidate, attempt, _ = await prepared_attempt(db, user, protocol)
    job.conditions = {**job.conditions, "version": version}
    attempt.state = {"assets": {"0": {"weights_sha256": "original"}}, "outcome": "passed"}
    attempt.finished_at = datetime.now(UTC)
    candidate.status = "passed"
    job.status = "awaiting_input"
    await api.add_candidate(
        str(job.id),
        CandidateAdd(
            request_key="different-bytes", source_candidate_id=str(candidate.id), patch={"name": "other-local-weights"}
        ),
        user,
        db,
    )
    await tick(db, job)
    await db.flush()
    await tick(db, job)
    await db.flush()
    monkeypatch.setattr(runtime, "preflight", AsyncMock(return_value={"0": {"weights_sha256": "different", "image": "engine@sha256:abc"}}))
    await tick(db, job)
    active = await db.scalar(select(CustomValidationAttempt).where(CustomValidationAttempt.finished_at.is_(None)))
    if version == 1:
        assert active.error_code == "quality_floor_required" and active.status == "collecting"
    else:
        assert active.error_code is None and active.status == "applying"


@pytest.mark.parametrize("reject_winner", [False, True])
async def test_mcp_search_creates_drafts_repairs_then_improves_and_saves_winner(
    db, user, protocol, monkeypatch, reject_winner
):
    from app.mcp import tools
    from app.services.validation_protocol import SearchFinish

    protocol["search"] = {"target_model": "example/model", "max_candidates": 4}
    job, body, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    await db.flush()
    assert job.phase == "awaiting_planner"
    preview = await api.preview_validation(body, user, db)
    assert preview["candidates"] == [] and preview["max_gpu_usage"] == 8

    async def run_route(route, **kwargs):
        return await route(**kwargs, user=user, db=db)

    monkeypatch.setattr(tools, "_run", run_route)
    assets = {"0": {"weights_sha256": "weights", "image": "registry/engine@sha256:" + "a" * 64}}
    monkeypatch.setattr(runtime, "preflight", AsyncMock(return_value=assets))
    monkeypatch.setattr(runtime, "apply", AsyncMock(return_value={}))
    monkeypatch.setattr(runtime, "collect_jobs", AsyncMock(return_value={}))
    monkeypatch.setattr(runtime, "cleanup", AsyncMock(return_value=True))
    publish = AsyncMock(return_value={"model_id": "published"})
    monkeypatch.setattr(runtime, "publish", publish)

    async def observe(db, j, attempt, dep, candidate):
        failed = candidate.position == 0 or (reject_winner and candidate.position == 2 and attempt.purpose == "final")
        return {"ready": not failed, "pools": {}}, {
            "aggregated": {
                "pods": [
                    {
                        "scheduled": True,
                        "containers": [
                            {
                                "image_id": "image",
                                "current_log": "CUDA out of memory" if failed else "ready",
                            }
                        ],
                    }
                ]
            }
        }

    async def measure(db, j, attempt, dep, run):
        c = await db.get(CustomValidationCandidate, attempt.candidate_id)
        run.status = "succeeded"
        run.result = run_result(j.conditions, throughput=100 + c.position * 50)
        if run.label.endswith(": smoke"):
            run.result["metrics"]["completed"] = 1

    monkeypatch.setattr(runtime, "observe", observe)
    monkeypatch.setattr(runtime, "benchmark", measure)

    async def advance():
        nonlocal job
        for _ in range(60):
            await tick(db, job)
            await db.commit()
            job_id = job.id
            db.expire_all()
            job = await service.get_job(db, job_id)
            if job.status in {"awaiting_input", "succeeded", "failed"}:
                break

    draft = RecipeBody(
        name="generated",
        hf_repo_id="example/model",
        model_path="/models/model",
        image="registry/engine:v1",
        gpu_count=2,
        engine_args={"max-model-len": 4096, "tensor-parallel-size": 2},
        env={"API_KEY": "kept-private"},
    ).model_dump(mode="json")
    first = CandidateAdd(
        request_key="baseline",
        recipe=draft,
        expected_candidate_count=0,
        hypothesis="Establish a baseline using the available image and two GPUs",
    )
    plan = await tools.preview_recipe_validation_candidate(str(job.id), first)
    assert plan["diff"]["changed"] > 0
    await tools.add_recipe_validation_candidate(str(job.id), first)
    await advance()
    context = await tools.get_recipe_search_context(str(job.id))
    assert context["candidates"][0]["attempts"][0]["error_code"] == "gpu_oom"
    assert context["search_progress"]["next_action"] == "propose_candidate"
    parent = context["candidates"][0]["id"]
    for count, memory in [(1, 0.8), (2, 0.85)]:
        candidate = CandidateAdd(
            request_key=f"change-{count}",
            source_candidate_id=parent,
            patch={"name": f"improvement-{count}", "engine_args": {"gpu-memory-utilization": memory}},
            expected_candidate_count=count,
            hypothesis="Tune memory allocation while preserving context",
        )
        await tools.add_recipe_validation_candidate(str(job.id), candidate)
        await advance()
        context = await tools.get_recipe_search_context(str(job.id))
        assert context["status"] == "awaiting_input"
        assert context["search_progress"]["best_value"] == 100 + count * 50
        parent = context["candidates"][-1]["id"]
        assert publish.await_count == 0  # Passing a trial must not publish before exploration closes.
    assert len((await db.scalars(select(CustomServingRecipe))).all()) == 0
    finish = SearchFinish(expected_candidate_count=3, reason="Measured improvement; no further in-scope hypothesis")
    await tools.finish_recipe_search(str(job.id), finish)
    await tools.finish_recipe_search(str(job.id), finish)  # Retry remains one final attempt.
    await advance()
    assert job.status == "succeeded" and publish.await_count == 1
    detail = await service.detail(db, job)
    winner = next(c for c in detail["candidates"] if c["status"] == "selected")
    assert winner["status"] == "selected" and len(winner["attempts"]) == 2
    assert all(a["state"]["assessment"]["passed"] for a in winner["attempts"])
    recipe = await db.get(CustomServingRecipe, UUID(job.search_state["saved_recipe_id"]))
    assert recipe.image == assets["0"]["image"] and recipe.env["API_KEY"] == "kept-private"
    assert recipe.engine_args["gpu-memory-utilization"] == (0.8 if reject_winner else 0.85)
    assert len((await db.scalars(select(CustomServingRecipe))).all()) == 1
    assert "kept-private" not in json.dumps(detail)
    assert job.cluster_id == UUID(body.cluster_id) and job.namespace == "serving-b"


async def test_search_rejects_stale_duplicate_and_out_of_scope_candidates(db, user, protocol):
    protocol["search"] = {"target_model": "example/model", "max_candidates": 2}
    job, _, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    draft = RecipeBody(
        name="baseline", model_path="example/model", image="engine:v1", gpu_count=2, engine_args={"max-model-len": 4096}
    ).model_dump(mode="json")
    first = CandidateAdd(request_key="first", recipe=draft, expected_candidate_count=0, hypothesis="Baseline")
    await api.add_candidate(str(job.id), first, user, db)
    job.status = "awaiting_input"
    again = await api.add_candidate(str(job.id), first, user, db)
    assert len(again["candidates"]) == 1
    parent = again["candidates"][0]["id"]
    candidate = await db.get(CustomValidationCandidate, UUID(parent))
    candidate.status = "passed"
    candidate.assessment = {"passed": True, "metrics": {"output_throughput": 100}}
    for change, expected, status in [
        ({"engine_args": {"max-num-seqs": 10}}, 0, 409),
        ({"name": "renamed"}, 1, 409),
        ({"model_path": "unauthorized/model"}, 1, 400),
        ({"engine_args": {"max-model-len": 1024}}, 1, 400),
        ({"gpu_count": 20}, 1, 400),
        ({"performance": {"num_prompts": 1}}, 1, 400),
    ]:
        with pytest.raises(HTTPException) as error:
            await api.add_candidate(
                str(job.id),
                CandidateAdd(
                    request_key=str(uuid4()),
                    source_candidate_id=parent,
                    patch=change,
                    expected_candidate_count=expected,
                    hypothesis="test",
                ),
                user,
                db,
            )
        assert error.value.status_code == status
    fixed = CandidateAdd(
        request_key="next",
        source_candidate_id=parent,
        expected_candidate_count=1,
        patch={"engine_args": {"max-num-seqs": 16}},
        hypothesis="Test smaller batch",
    )
    await api.add_candidate(str(job.id), fixed, user, db)
    job.status = "awaiting_input"
    with pytest.raises(HTTPException, match="candidate_limit"):
        await api.add_candidate(
            str(job.id), fixed.model_copy(update={"request_key": "third", "expected_candidate_count": 2}), user, db
        )


def test_search_patience_uses_meaningful_cumulative_gain_and_server_metric(protocol):
    from app.services.validation_protocol import RecipeSearch
    from app.services.validation_search import progress

    protocol["search"] = RecipeSearch(target_model="m", patience=2, min_improvement_percent=5).model_dump()
    job = SimpleNamespace(conditions=protocol, search_state=None, started_at=datetime.now(UTC), status="awaiting_input")
    candidates = [
        SimpleNamespace(
            id=uuid4(),
            position=i,
            status="passed",
            assessment={"passed": True, "metrics": {"output_throughput": value}},
        )
        for i, value in enumerate([100, 103, 106, 105, 104])
    ]
    assert progress(job, candidates[:3])["stale_candidates"] == 0
    assert progress(job, candidates)["stop_reason"] == "no_improvement"
    assert progress(job, candidates)["best_value"] == 106
    protocol["selection_metric"] = "p99_ttft_ms"
    for c, value in zip(candidates, [100, 97, 94, 95, 96]):
        c.assessment["metrics"] = {"p99_ttft_ms": value}
    assert progress(job, candidates)["best_value"] == 94
    assert progress(job, candidates)["stop_reason"] == "no_improvement"


async def test_search_time_limit_closes_disconnected_job_and_preserves_best(db, user, protocol, monkeypatch):
    protocol["search"] = {"target_model": "example/model", "max_search_seconds": 60}
    job, _, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    start = job.started_at
    await tick(db, job, current=start + timedelta(seconds=61))
    assert job.status == "failed" and job.search_state["closed_reason"] == "search_time_limit"


async def test_search_candidate_limit_selects_best_without_a_connected_client(db, user, protocol):
    protocol["search"] = {"target_model": "example/model", "max_candidates": 1}
    job, _, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    draft = RecipeBody(
        name="candidate",
        model_path="example/model",
        image="engine:v1",
        gpu_count=2,
        engine_args={"max-model-len": 4096},
    ).model_dump(mode="json")
    await api.add_candidate(
        str(job.id),
        CandidateAdd(request_key="first", recipe=draft, expected_candidate_count=0, hypothesis="Baseline"),
        user,
        db,
    )
    candidate = await db.scalar(select(CustomValidationCandidate))
    candidate.status = "passed"
    candidate.assessment = {"passed": True, "metrics": {"output_throughput": 100}}
    job.status = "awaiting_input"
    await tick(db, job)
    await db.flush()
    attempt = await db.scalar(select(CustomValidationAttempt))
    assert attempt.purpose == "final" and attempt.candidate_id == candidate.id
    assert job.search_state["closed_reason"] == "candidate_limit"


async def test_search_deadline_collects_and_cleans_active_trial_before_selecting(db, user, protocol, monkeypatch):
    protocol["search"] = {"target_model": "example/model", "max_search_seconds": 60}
    job, _, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    draft = RecipeBody(
        name="trial", model_path="example/model", image="engine:v1", gpu_count=2, engine_args={"max-model-len": 4096}
    ).model_dump(mode="json")
    await api.add_candidate(
        str(job.id),
        CandidateAdd(request_key="trial", recipe=draft, expected_candidate_count=0, hypothesis="Baseline"),
        user,
        db,
    )
    await tick(db, job)
    await db.flush()
    attempt = await db.scalar(select(CustomValidationAttempt))
    attempt.status = "starting"
    cleanup = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(runtime, "cleanup", cleanup)
    for i in range(4):
        await tick(db, job, current=job.started_at + timedelta(seconds=61 + i))
        await db.flush()
    assert attempt.error_code == "search_time_limit" and attempt.finished_at
    assert cleanup.await_count == 2
    await tick(db, job, current=job.started_at + timedelta(seconds=66))
    assert job.status == "failed"


async def test_invalid_draft_returns_validation_error_without_input_values(db, user, protocol):
    protocol["search"] = {"target_model": "example/model"}
    job, _, _ = await create_job(db, user, protocol, count=0)
    await tick(db, job)
    with pytest.raises(HTTPException) as error:
        await api.add_candidate(
            str(job.id),
            CandidateAdd(
                request_key="invalid",
                expected_candidate_count=0,
                hypothesis="Baseline",
                recipe={"name": "bad", "env": {"API_KEY": "never-return-this"}},
            ),
            user,
            db,
        )
    assert error.value.status_code == 422
    assert "never-return-this" not in str(error.value.detail)
    assert not (await db.scalars(select(CustomValidationCandidate))).all()


def test_new_protocol_rejects_accuracy_and_keeps_agentic_counts(protocol):
    from app.services.benchmark_workload import PerformanceWorkload
    from app.jobs.reconcile_recipe_validations import measurement_plan
    from pydantic import ValidationError

    for change in ({"accuracy": {"tasks": "mmlu"}}, {"prefix_len": 100}, {"selection_metric": "p99_ttft_ms"}):
        with pytest.raises(ValidationError):
            ValidationConditions.model_validate({**protocol, **change})
    protocol["performance"] = PerformanceWorkload(workload_version=2, workload_type="agentic_replay", num_sessions=2).model_dump()
    plan = measurement_plan(protocol)
    assert len(plan) == 3 and all(kind == "performance" for _, kind, _ in plan)
    runs = [SimpleNamespace(id=uuid4(), kind=kind, params=params, status="succeeded", result={"metrics": {
        "completed": 6, "failed": 0, "skipped": 0, "completed_sessions": 2,
        "output_throughput": 100 + i * 10, "p99_ttft_ms": 50, "request_throughput": 2,
    }}) for i, (_, kind, params) in enumerate(plan)]
    assert assess(runs, protocol, 2)["passed"]
    assert assess(runs, protocol, 2)["metrics"]["output_throughput"] == 110
    runs[0].result["metrics"]["completed_sessions"] = 1
    assert not assess(runs, protocol, 2)["passed"]


def test_new_alternatives_need_comparable_tokens_and_gpu_but_not_quality(protocol):
    conditions = ValidationConditions.model_validate(protocol)
    snaps = [{"recipe": {"model_path": "original"}, "gpu_count": 2}, {"recipe": {"model_path": "quantized"}, "gpu_count": 4}]
    with pytest.raises(HTTPException, match="GPU"):
        service.check_comparability(snaps, conditions)
    conditions.selection_metric = "throughput_per_gpu"
    with pytest.raises(HTTPException, match="tokenizer"):
        service.check_comparability(snaps, conditions)
    conditions.tokenizer = "/models/common-tokenizer"
    service.check_comparability(snaps, conditions)
