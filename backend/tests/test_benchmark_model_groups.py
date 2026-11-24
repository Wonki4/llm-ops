"""Query real records: complete model groups, matching filters and stable paging."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.session import get_db
from app.main import app


@compiles(JSONB, "sqlite")
def jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
async def history(admin_client):
    engine = create_engine("sqlite://")
    CustomBenchmarkRun.__table__.create(engine)
    with Session(engine) as session:
        now = datetime(2026, 11, 24, tzinfo=UTC)
        for i in range(505):
            session.add(CustomBenchmarkRun(
                id=uuid4(), model_name="Qwen/same-model", tool="vllm_serving", kind="performance",
                status="succeeded" if i % 2 else "failed", params={}, created_by="test",
                created_at=now, result={"large_result": "must not appear in group response"},
            ))
        for model in ["other/same-model", "/first/same-model", "/second/same-model", "Qwen/Same-Model"]:
            session.add(CustomBenchmarkRun(
                id=uuid4(), model_name=model, tool="vllm_serving", kind="performance",
                status="succeeded", params={}, created_by="test", created_at=now - timedelta(days=1),
            ))
        for kind, params, status in [
            ("performance", {"experiment": "prefix_cache"}, "succeeded"),
            ("performance", {"experiment": "prefix_cache"}, "running"),
            ("accuracy", {}, "succeeded"),
        ]:
            session.add(CustomBenchmarkRun(
                id=uuid4(), model_name="Qwen/same-model", tool="vllm_serving", kind=kind,
                status=status, params=params, created_by="test", created_at=now,
            ))
        session.commit()

        async def execute(statement):
            return session.execute(statement)

        app.dependency_overrides[get_db] = lambda: SimpleNamespace(execute=execute)
        yield admin_client
    engine.dispose()


async def test_groups_include_old_models_beyond_first_500_runs(history):
    response = await history.get("/api/benchmarks/model-groups")
    assert response.status_code == 200, response.text
    groups = {g["model_name"]: g for g in response.json()["models"]}
    assert len(groups) == 5
    qwen = groups["Qwen/same-model"]
    assert qwen["run_count"] == 508
    assert qwen["succeeded_count"] == 254
    assert qwen["failed_count"] == 253
    assert qwen["active_count"] == 1
    assert set(qwen) == {"model_name", "run_count", "succeeded_count", "failed_count", "active_count", "latest_created_at"}
    assert all(g["run_count"] == 1 for name, g in groups.items() if name != "Qwen/same-model")
    assert "large_result" not in response.text


@pytest.mark.parametrize("experiment,status,expected", [
    ("performance", None, 505), ("performance", "failed", 253),
    ("prefix_cache", None, 2), ("prefix_cache", "running", 1), ("accuracy", None, 1),
])
async def test_group_counts_match_all_model_pages_and_filters(history, experiment, status, expected):
    filters = {"experiment": experiment}
    if status:
        filters["status_filter"] = status
    groups = (await history.get("/api/benchmarks/model-groups", params=filters)).json()["models"]
    assert next(g for g in groups if g["model_name"] == "Qwen/same-model")["run_count"] == expected
    ids = []
    for offset in range(0, expected, 25):
        response = await history.get("/api/benchmarks", params={
            **filters, "model_name": "Qwen/same-model", "limit": 25, "offset": offset,
        })
        assert response.status_code == 200, response.text
        rows = response.json()["runs"]
        assert all(r["model_name"] == "Qwen/same-model" for r in rows)
        ids.extend(r["id"] for r in rows)
    assert len(ids) == len(set(ids)) == expected
    assert ids == sorted(ids, reverse=True)


async def test_group_endpoint_is_admin_only(user_client, mock_db):
    response = await user_client.get("/api/benchmarks/model-groups")
    assert response.status_code == 403
    mock_db.execute.assert_not_awaited()


@pytest.mark.parametrize("params", [{"limit": 0}, {"offset": -1}])
async def test_invalid_page_is_rejected(admin_client, mock_db, params):
    response = await admin_client.get("/api/benchmarks", params=params)
    assert response.status_code == 422
    mock_db.execute.assert_not_awaited()
