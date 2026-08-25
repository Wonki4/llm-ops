"""Budget-request limits — policy enforcement in create_budget_request (mock_db).

These assert the security-critical rejections. Enforcement runs BEFORE the
duplicate-pending check and the team-alias read, so each test only needs the
policy SELECT (the first portal-db execute) to return the team's settings; the
request is rejected with 400 before any further DB call.
"""

from unittest.mock import AsyncMock, MagicMock


def _policy_db(team_id, max_amount=None, allowed_days=None):
    """A portal-db `execute` whose first call returns the policy SELECT rows."""
    rows = []
    if max_amount is not None:
        rows.append({"key": f"team:{team_id}:budget_request_max_amount", "value": str(max_amount)})
    if allowed_days is not None:
        rows.append({"key": f"team:{team_id}:budget_request_allowed_days", "value": ",".join(allowed_days)})
    res = MagicMock()
    res.mappings.return_value = rows  # _get_budget_request_policy iterates .mappings()
    return AsyncMock(return_value=res)


async def test_amount_over_cap_rejected(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", max_amount=20)
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 50, "requested_duration_days": 30,
        })
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"].lower()


async def test_period_not_in_allowlist_rejected(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", allowed_days=["7", "30"])
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 10, "requested_duration_days": 90,
        })
    assert resp.status_code == 400


async def test_permanent_disallowed_when_not_in_list(client_for_user, super_user, mock_db):
    mock_db.execute = _policy_db("team-1", allowed_days=["30"])
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 10, "requested_duration_days": None,
        })
    assert resp.status_code == 400


async def test_amount_within_cap_and_allowed_period_passes_policy(client_for_user, super_user, mock_db):
    """A request inside the cap + allowed period clears enforcement and succeeds
    end-to-end. Mocked like test_team_requests.py's
    test_create_budget_request_stores_duration: db.execute gets a two-step
    side_effect (policy read, then duplicate-check) and litellm_db is
    overridden separately for the team-alias read."""
    from app.db.session import get_litellm_db
    from app.main import app

    policy_res = MagicMock()
    policy_res.mappings.return_value = [
        {"key": "team:team-1:budget_request_max_amount", "value": "20"},
        {"key": "team:team-1:budget_request_allowed_days", "value": "30"},
    ]
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None  # no pending duplicate
    mock_db.execute = AsyncMock(side_effect=[policy_res, dup_res])

    litellm_res = MagicMock()
    litellm_res.mappings.return_value.first.return_value = {"team_alias": "Alpha Team"}
    litellm_db = AsyncMock()
    litellm_db.execute = AsyncMock(return_value=litellm_res)
    app.dependency_overrides[get_litellm_db] = lambda: litellm_db

    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 20, "requested_duration_days": 30,
        })
    assert resp.status_code == 201, resp.text


async def test_no_policy_configured_allows_any_amount_and_period(client_for_user, super_user, mock_db):
    """A team with no configured policy (no rows in custom_portal_settings) is
    unrestricted — any positive amount/period passes enforcement."""
    from app.db.session import get_litellm_db
    from app.main import app

    policy_res = MagicMock()
    policy_res.mappings.return_value = []  # no policy rows configured
    dup_res = MagicMock()
    dup_res.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[policy_res, dup_res])

    litellm_res = MagicMock()
    litellm_res.mappings.return_value.first.return_value = {"team_alias": "Alpha Team"}
    litellm_db = AsyncMock()
    litellm_db.execute = AsyncMock(return_value=litellm_res)
    app.dependency_overrides[get_litellm_db] = lambda: litellm_db

    async with client_for_user(super_user) as client:
        resp = await client.post("/api/team-requests/budget", json={
            "team_id": "team-1", "requested_budget": 100000, "requested_duration_days": None,
        })
    assert resp.status_code == 201, resp.text
