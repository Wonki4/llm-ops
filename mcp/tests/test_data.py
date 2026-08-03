import datetime as dt

from app.data import list_visible_teams, member_budget_usage


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
    """Returns queued results in the order execute() is called."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        return self._results.pop(0)


async def test_list_visible_teams_drops_strict_hidden():
    litellm = FakeSession([FakeResult(rows=[
        {"team_id": "t1", "team_alias": "Alpha"},
        {"team_id": "t2", "team_alias": "Beta"},
    ])])
    portal = FakeSession([FakeResult(scalar='["t2"]')])
    teams = await list_visible_teams(portal, litellm, "E1")
    assert teams == [{"team_id": "t1", "team_alias": "Alpha"}]


async def test_list_visible_teams_no_hidden_setting():
    litellm = FakeSession([FakeResult(rows=[{"team_id": "t1", "team_alias": "Alpha"}])])
    portal = FakeSession([FakeResult(scalar=None)])
    assert await list_visible_teams(portal, litellm, "E1") == [{"team_id": "t1", "team_alias": "Alpha"}]


async def test_member_budget_dedicated_row():
    litellm = FakeSession([
        FakeResult(one={"budget_id": "b-ded", "spend": 7.0}),
        FakeResult(one={"team_alias": "Alpha", "metadata": {"team_member_budget_id": "b-def"}}),
        FakeResult(one={"max_budget": 20.0, "budget_duration": "30d", "budget_reset_at": None}),
    ])
    out = await member_budget_usage(litellm, "t1", "E1")
    assert out["max_budget"] == 20.0 and out["spend"] == 7.0 and out["remaining"] == 13.0
    assert out["team_alias"] == "Alpha" and out["reset_at"] is None


async def test_member_budget_falls_back_to_team_default():
    litellm = FakeSession([
        FakeResult(one={"budget_id": None, "spend": 2.0}),
        FakeResult(one={"team_alias": "Alpha", "metadata": {"team_member_budget_id": "b-def"}}),
        FakeResult(one={
            "max_budget": 10.0, "budget_duration": "30d",
            "budget_reset_at": dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        }),
    ])
    out = await member_budget_usage(litellm, "t1", "E1")
    assert out["max_budget"] == 10.0 and out["remaining"] == 8.0
    assert out["reset_at"].startswith("2026-09-01")


async def test_member_budget_unlimited_when_no_budget():
    litellm = FakeSession([
        FakeResult(one={"budget_id": None, "spend": 5.0}),
        FakeResult(one={"team_alias": "Alpha", "metadata": {}}),
    ])
    out = await member_budget_usage(litellm, "t1", "E1")
    assert out["max_budget"] is None and out["remaining"] is None and out["spend"] == 5.0


async def test_non_member_returns_none():
    litellm = FakeSession([FakeResult(one=None)])
    assert await member_budget_usage(litellm, "t1", "E1") is None
