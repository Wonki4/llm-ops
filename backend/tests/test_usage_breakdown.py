"""User → key → model-group usage: key resolution (live + deleted), attribution,
nesting, and the team / admin endpoints built on it."""

import pytest
from httpx import AsyncClient

from app.services.usage_breakdown import KeyInfo, attributed_user, key_info_from_row, nest_by_key

_SUM_FIELDS = ("total_tokens", "input_tokens", "output_tokens", "cache_read_tokens", "api_requests", "spend")


def _usage(api_key, user_id, model, spend, requests=1):
    return {
        "api_key": api_key,
        "user_id": user_id,
        "model": model,
        "total_tokens": 100 * requests,
        "input_tokens": 80 * requests,
        "output_tokens": 20 * requests,
        "cache_read_tokens": 10 * requests,
        "api_requests": requests,
        "spend": spend,
    }


def _key(token, user_id, team_id, alias=None, **meta):
    return {
        "token": token,
        "user_id": user_id,
        "team_id": team_id,
        "key_alias": None,
        "key_name": f"sk-...{token[-4:]}",
        "metadata": {"display_alias": alias, **meta} if alias or meta else {},
    }


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeLiteLLMDB:
    """Routes the usage/key queries and applies their WHERE filters in Python."""

    def __init__(self, *, live=(), deleted=(), usage=(), teams=(), team_row=None):
        self.live, self.deleted, self.usage = list(live), list(deleted), list(usage)
        self.teams = list(teams)
        self.team_row = team_row  # admins/members for get_team_access
        self.statements: list[tuple[str, dict]] = []

    @staticmethod
    def _keys(rows, params):
        out = rows
        if "team_id" in params:
            out = [r for r in out if r["team_id"] == params["team_id"]]
        if "user_id" in params:
            out = [r for r in out if (r["user_id"] or "") == params["user_id"]]
        if "tokens" in params:
            out = [r for r in out if r["token"] in params["tokens"]]
        return out

    def _usage_rows(self, sql, params):
        rows = self.usage
        if "tokens" in params and "user_id" in params:
            rows = [r for r in rows if r["api_key"] in params["tokens"] or (r["user_id"] or "") == params["user_id"]]
        elif "tokens" in params:
            rows = [r for r in rows if r["api_key"] in params["tokens"]]
        if "AS bucket" in sql or "AS date" in sql:
            return []  # time series: not under test here
        if "AS model" in sql:
            return rows
        grouped: dict[tuple, dict] = {}  # GROUP BY api_key, user_id
        for r in rows:
            acc = grouped.setdefault(
                (r["api_key"], r["user_id"]),
                {"api_key": r["api_key"], "user_id": r["user_id"], **{f: 0 for f in _SUM_FIELDS}},
            )
            for f in _SUM_FIELDS:
                acc[f] += r[f]
        return list(grouped.values())

    async def execute(self, statement, params=None):
        sql, params = str(statement), params or {}
        self.statements.append((sql, params))
        if "LiteLLM_DeletedVerificationToken" in sql:
            return _Result(self._keys(self.deleted, params))
        if "LiteLLM_VerificationToken" in sql:
            return _Result(self._keys(self.live, params))
        if "admins, members" in sql:
            return _Result([self.team_row] if self.team_row else [])
        if "LiteLLM_UserTable" in sql:
            return _Result([])
        if "LiteLLM_TeamTable" in sql:
            ids = params.get("ids")
            return _Result([t for t in self.teams if ids is None or t["team_id"] in ids])
        if "custom_users" in sql:
            return _Result([])
        if "LiteLLM_DailyUserSpend" in sql:
            return _Result(self._usage_rows(sql, params))
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def use_litellm_db(mock_db):
    from app.db.session import get_litellm_db
    from app.main import app

    def _install(fake: FakeLiteLLMDB) -> FakeLiteLLMDB:
        app.dependency_overrides[get_litellm_db] = lambda: fake
        mock_db.execute = fake.execute  # portal DB: custom_users lookups
        return fake

    return _install


# ─── Pure helpers ───────────────────────────────────────────────


def test_key_alias_prefers_portal_alias_then_litellm_names():
    assert key_info_from_row(_key("tok-1", "u", "t", alias="laptop"), deleted=False).alias == "laptop"
    row = {**_key("tok-2", "u", "t"), "key_alias": "litellm-alias"}
    assert key_info_from_row(row, deleted=False).alias == "litellm-alias"
    assert key_info_from_row(_key("tok-3333", "u", "t"), deleted=False).alias == "sk-...3333"


def test_key_info_reads_json_text_metadata_and_flags():
    row = {**_key("tok-1", "u", None), "metadata": '{"display_alias": "x", "issued_via": "gateway_sso"}'}
    info = key_info_from_row(row, deleted=True)
    assert (info.alias, info.sso, info.personal, info.deleted) == ("x", True, True, True)


def test_attribution_follows_key_owner_then_row_user():
    info = KeyInfo("tok", "OWNER", "t", None, sso=False, deleted=True)
    assert attributed_user("ROWUSER", info) == "OWNER"
    assert attributed_user("ROWUSER", None) == "ROWUSER"
    assert attributed_user(None, None) == ""


def test_nest_by_key_orders_keys_and_models_by_spend_and_marks_state():
    directory = {
        "a": KeyInfo("a", "U", "t1", "small", sso=False, deleted=False),
        "b": KeyInfo("b", "U", "t1", "big", sso=True, deleted=True),
    }
    rows = [
        _usage("a", "U", "gpt", 0.1),
        _usage("b", "U", "gpt", 0.5),
        _usage("b", "U", "glm", 1.0, requests=2),
        _usage("zzz", "U", None, 0.2),
    ]
    keys = nest_by_key(rows, directory, {"t1": "Alpha"})
    assert [k["api_key"] for k in keys] == ["b", "zzz", "a"]
    big = keys[0]
    assert (big["alias"], big["team_alias"], big["state"], big["sso"]) == ("big", "Alpha", "deleted", True)
    assert big["spend"] == pytest.approx(1.5) and big["api_requests"] == 3
    assert [m["model"] for m in big["models"]] == ["glm", "gpt"]
    unknown = keys[1]
    assert (unknown["state"], unknown["alias"], unknown["models"][0]["model"]) == ("unknown", None, "(unknown)")


# ─── Admin endpoints ────────────────────────────────────────────


def _admin_fixture():
    return FakeLiteLLMDB(
        live=[_key("live-1", "USER1", "t1", alias="main"), _key("live-2", "USER1", "t2", alias="side")],
        deleted=[_key("gone-1", "USER1", "t1", alias="old")],
        usage=[
            _usage("live-1", "USER1", "gpt", 1.0),
            _usage("live-2", "USER1", "glm", 0.5),
            _usage("gone-1", "USER1", "gpt", 0.25),
            _usage("lost-1", "USER2", "gpt", 0.125),  # key in neither table
            _usage("lost-2", None, "gpt", 0.0625),  # no user at all
        ],
        teams=[{"team_id": "t1", "team_alias": "Alpha"}, {"team_id": "t2", "team_alias": "Beta"}],
    )


@pytest.mark.asyncio
async def test_admin_usage_is_per_user_and_keeps_deleted_and_unknown_keys(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    resp = await admin_client.get("/api/admin/usage?start_date=2026-07-01&end_date=2026-07-31")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = {r["user_id"]: r for r in body["rows"]}
    assert set(rows) == {"USER1", "USER2", None}
    assert rows["USER1"]["key_count"] == 3
    assert rows["USER1"]["spend"] == pytest.approx(1.75)  # deleted key included
    assert body["totals"]["spend"] == pytest.approx(1.9375)  # nothing dropped
    assert "team_id" not in rows["USER1"]


@pytest.mark.asyncio
async def test_admin_usage_team_filter_counts_only_that_teams_keys(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    resp = await admin_client.get("/api/admin/usage?start_date=2026-07-01&end_date=2026-07-31&team_id=t1")
    body = resp.json()
    assert [(r["user_id"], r["key_count"]) for r in body["rows"]] == [("USER1", 2)]
    assert body["totals"]["spend"] == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_admin_usage_sort_by_key_count(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    resp = await admin_client.get(
        "/api/admin/usage?start_date=2026-07-01&end_date=2026-07-31&sort_by=key_count&sort_dir=desc"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"][0]["user_id"] == "USER1"
    bad = await admin_client.get("/api/admin/usage?start_date=2026-07-01&end_date=2026-07-31&sort_by=team")
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_admin_by_key_nests_models_with_team_and_state(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    resp = await admin_client.get("/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31&user_id=USER1")
    assert resp.status_code == 200, resp.text
    keys = resp.json()["keys"]
    assert [(k["alias"], k["team_alias"], k["state"]) for k in keys] == [
        ("main", "Alpha", "active"),
        ("side", "Beta", "active"),
        ("old", "Alpha", "deleted"),
    ]
    assert keys[0]["models"][0]["model"] == "gpt"


@pytest.mark.asyncio
async def test_admin_by_key_unknown_and_no_user_buckets(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    user2 = await admin_client.get("/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31&user_id=USER2")
    assert [(k["api_key"], k["state"]) for k in user2.json()["keys"]] == [("lost-1", "unknown")]

    nobody = await admin_client.get("/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31")
    body = nobody.json()
    assert body["user_id"] is None
    assert [k["api_key"] for k in body["keys"]] == ["lost-2"]


@pytest.mark.asyncio
async def test_admin_by_key_skips_rows_whose_key_belongs_to_someone_else(admin_client: AsyncClient, use_litellm_db):
    fake = _admin_fixture()
    # LiteLLM stamped USER2 on a row, but the key is (now) USER1's.
    fake.usage.append(_usage("live-1", "USER2", "gpt", 9.0))
    use_litellm_db(fake)

    resp = await admin_client.get("/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31&user_id=USER2")
    assert [k["api_key"] for k in resp.json()["keys"]] == ["lost-1"]


@pytest.mark.asyncio
async def test_admin_by_key_team_filter(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())

    resp = await admin_client.get(
        "/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31&user_id=USER1&team_id=t2"
    )
    assert [k["alias"] for k in resp.json()["keys"]] == ["side"]


@pytest.mark.asyncio
async def test_admin_by_key_requires_super_user(user_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())
    resp = await user_client.get("/api/admin/usage/by-key?start_date=2026-07-01&end_date=2026-07-31&user_id=USER1")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_daily_team_filter_includes_deleted_keys(admin_client: AsyncClient, use_litellm_db):
    fake = use_litellm_db(_admin_fixture())

    resp = await admin_client.get("/api/admin/usage/daily?start_date=2026-07-01&end_date=2026-07-31&team_id=t1")
    assert resp.status_code == 200, resp.text
    daily_sql, daily_params = fake.statements[-1]
    assert "JOIN" not in daily_sql
    assert sorted(daily_params["tokens"]) == ["gone-1", "live-1"]


@pytest.mark.asyncio
async def test_admin_daily_unknown_team_is_empty(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_admin_fixture())
    resp = await admin_client.get("/api/admin/usage/daily?start_date=2026-07-01&end_date=2026-07-31&team_id=nope")
    assert resp.json() == {
        "days": [],
        "totals": {
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "api_requests": 0,
            "spend": 0.0,
        },
    }


# ─── Team endpoints ─────────────────────────────────────────────


def _team_fixture():
    return FakeLiteLLMDB(
        live=[
            _key("mine-1", "user001", "team-1", alias="laptop"),
            _key("mine-2", "user001", "team-1", alias="server"),
            _key("other-1", "user002", "team-1", alias="theirs"),
            _key("elsewhere", "user001", "team-2", alias="other team"),
        ],
        deleted=[_key("mine-old", "user001", "team-1", alias="retired")],
        usage=[
            _usage("mine-1", "user001", "gpt", 1.0),
            _usage("mine-2", "user001", "glm", 0.5),
            _usage("mine-old", "user001", "gpt", 0.25),
            _usage("other-1", "user002", "gpt", 2.0),
            _usage("elsewhere", "user001", "gpt", 4.0),
        ],
        team_row={"admins": [], "members": ["user001", "user002"]},
    )


@pytest.mark.asyncio
async def test_team_usage_counts_deleted_team_keys(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_team_fixture())

    resp = await admin_client.get("/api/teams/team-1/usage?start_date=2026-07-01&end_date=2026-07-31")
    assert resp.status_code == 200, resp.text
    members = {m["user_id"]: m["spend"] for m in resp.json()["members"]}
    assert members == {"user001": pytest.approx(1.75), "user002": pytest.approx(2.0)}


@pytest.mark.asyncio
async def test_team_member_by_key_lists_only_this_teams_keys(user_client: AsyncClient, use_litellm_db):
    use_litellm_db(_team_fixture())

    resp = await user_client.get(
        "/api/teams/team-1/usage/user001/by-key?start_date=2026-07-01&end_date=2026-07-31"
    )
    assert resp.status_code == 200, resp.text
    keys = resp.json()["keys"]
    assert [(k["alias"], k["state"]) for k in keys] == [
        ("laptop", "active"),
        ("server", "active"),
        ("retired", "deleted"),
    ]
    assert keys[1]["models"] == [
        {
            "model": "glm",
            "total_tokens": 100,
            "input_tokens": 80,
            "output_tokens": 20,
            "cache_read_tokens": 10,
            "api_requests": 1,
            "spend": 0.5,
        }
    ]


@pytest.mark.asyncio
async def test_team_member_by_key_others_forbidden_for_members(user_client: AsyncClient, use_litellm_db):
    use_litellm_db(_team_fixture())

    resp = await user_client.get(
        "/api/teams/team-1/usage/user002/by-key?start_date=2026-07-01&end_date=2026-07-31"
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_team_member_by_key_no_keys_is_empty(admin_client: AsyncClient, use_litellm_db):
    use_litellm_db(_team_fixture())

    resp = await admin_client.get(
        "/api/teams/team-1/usage/nobody/by-key?start_date=2026-07-01&end_date=2026-07-31"
    )
    assert resp.json() == {"user_id": "nobody", "keys": []}
