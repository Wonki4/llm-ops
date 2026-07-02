"""Admin usage endpoints: token breakdown (input/output + cache-read)."""

import pytest
from httpx import AsyncClient


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_admin_usage_rows_and_totals_include_breakdown(admin_client: AsyncClient, mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        sql = str(statement)
        if "LiteLLM_TeamTable" in sql:
            return _FakeMappingsResult([{"team_id": "team-1", "team_alias": "Alpha"}])
        if "custom_users" in sql:
            return _FakeMappingsResult([])
        return _FakeMappingsResult(
            [
                {
                    "team_id": "team-1",
                    "user_id": "user002",
                    "total_tokens": 165000,
                    "input_tokens": 120000,
                    "output_tokens": 45000,
                    "cache_read_tokens": 30000,
                    "api_requests": 10,
                    "spend": 1.5,
                }
            ]
        )

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get("/api/admin/usage?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = body["rows"][0]
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (120000, 45000, 30000)
    totals = body["totals"]
    assert (totals["input_tokens"], totals["output_tokens"], totals["cache_read_tokens"]) == (120000, 45000, 30000)


@pytest.mark.asyncio
async def test_admin_usage_daily_includes_breakdown(admin_client: AsyncClient, mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    async def fake_execute(statement, params=None):
        return _FakeMappingsResult(
            [
                {
                    "date": "2026-07-01",
                    "total_tokens": 165000,
                    "input_tokens": 120000,
                    "output_tokens": 45000,
                    "cache_read_tokens": 30000,
                    "api_requests": 10,
                    "spend": 1.5,
                }
            ]
        )

    mock_db.execute = fake_execute
    app.dependency_overrides[get_litellm_db] = lambda: mock_db

    resp = await admin_client.get("/api/admin/usage/daily?start_date=2026-07-01&end_date=2026-07-02")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    day = body["days"][0]
    assert (day["input_tokens"], day["output_tokens"], day["cache_read_tokens"]) == (120000, 45000, 30000)
    totals = body["totals"]
    assert (totals["input_tokens"], totals["output_tokens"], totals["cache_read_tokens"]) == (120000, 45000, 30000)
