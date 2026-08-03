import pytest

from app.tools import NotVisibleError, list_my_teams_logic, team_budget_usage_logic


class FakeResult:
    def __init__(self, *, rows=None, one=None, scalar=None):
        self._rows = rows or []
        self._one = one
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._one

    def scalar(self):
        return self._scalar


class FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        return self._results.pop(0)


async def test_list_my_teams_wraps_visible_set():
    litellm = FakeSession([FakeResult(rows=[{"team_id": "t1", "team_alias": "Alpha"}])])
    portal = FakeSession([FakeResult(scalar=None)])
    assert await list_my_teams_logic(portal, litellm, "E1") == {
        "teams": [{"team_id": "t1", "team_alias": "Alpha"}]
    }


async def test_team_budget_usage_for_member():
    # list_visible_teams -> litellm(teams) + portal(strict); then
    # member_budget_usage -> litellm(membership, team, budget).
    litellm = FakeSession([
        FakeResult(rows=[{"team_id": "t1", "team_alias": "Alpha"}]),
        FakeResult(one={"budget_id": "b1", "spend": 3.0}),
        FakeResult(one={"team_alias": "Alpha", "metadata": {}}),
        FakeResult(one={"max_budget": 10.0, "budget_duration": "30d", "budget_reset_at": None}),
    ])
    portal = FakeSession([FakeResult(scalar=None)])
    out = await team_budget_usage_logic(portal, litellm, "E1", "t1")
    assert out["team_id"] == "t1" and out["remaining"] == 7.0


async def test_team_budget_usage_rejects_non_visible_team():
    litellm = FakeSession([FakeResult(rows=[{"team_id": "t1", "team_alias": "Alpha"}])])
    portal = FakeSession([FakeResult(scalar=None)])
    with pytest.raises(NotVisibleError):
        await team_budget_usage_logic(portal, litellm, "E1", "t2")


async def test_team_budget_usage_rejects_strict_hidden_team():
    litellm = FakeSession([FakeResult(rows=[{"team_id": "t1", "team_alias": "Alpha"}])])
    portal = FakeSession([FakeResult(scalar='["t1"]')])  # t1 strictly hidden -> not visible
    with pytest.raises(NotVisibleError):
        await team_budget_usage_logic(portal, litellm, "E1", "t1")
