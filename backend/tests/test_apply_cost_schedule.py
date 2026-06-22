"""Unit tests for the cost-schedule job.

Focus: the job must not try to edit LiteLLM models that are config-defined
(`db_model=False`). LiteLLM rejects `/model/update` for those with a 400
("Can't edit model. Model in config."), which previously broke every pass —
apply *and* revert — and spammed the worker log every interval.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.custom_model_catalog import CustomModelCatalog
from app.jobs import apply_cost_schedule as job


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeSession:
    def __init__(self, rules, catalogs):
        # first execute() -> rules, second -> catalogs (matches job order)
        self.execute = AsyncMock(side_effect=[_Result(rules), _Result(catalogs)])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def _catalog(name: str) -> CustomModelCatalog:
    return CustomModelCatalog(
        model_name=name,
        default_input_cost_per_token=1e-06,
        default_output_cost_per_token=2e-06,
    )


def _deployment(name: str, mid: str, *, db_model: bool) -> dict:
    return {
        "model_name": name,
        "model_info": {"id": mid, "db_model": db_model, "input_cost_per_token": 9e-06},
        "litellm_params": {},
    }


@pytest.mark.asyncio
async def test_config_defined_model_is_skipped_not_updated(monkeypatch):
    """A config model (db_model=False) must be skipped, never sent to /model/update."""
    # No rules anywhere -> revert target is the catalog default for every model.
    catalogs = [_catalog("cfg-model"), _catalog("db-model")]
    monkeypatch.setattr(
        job, "async_session_factory", lambda: _FakeSession([], catalogs)
    )

    fake = MagicMock()
    fake.get_model_info = AsyncMock(
        return_value=[
            _deployment("cfg-model", "cfg1", db_model=False),
            _deployment("db-model", "db1", db_model=True),
        ]
    )
    fake.update_model = AsyncMock(return_value={})
    monkeypatch.setattr(job, "LiteLLMClient", MagicMock(return_value=fake))

    result = await job.apply_cost_schedule()

    updated_ids = [c.args[0] for c in fake.update_model.call_args_list]
    assert "cfg1" not in updated_ids, "config-defined model must not be updated (LiteLLM 400s)"
    assert updated_ids == ["db1"], "only the DB-stored model should be updated"
    assert result["errors"] == 0


def _null_default_catalog(name: str) -> CustomModelCatalog:
    return CustomModelCatalog(
        model_name=name,
        default_input_cost_per_token=None,
        default_output_cost_per_token=None,
    )


def _deployment_with_override(name: str, mid: str, cost: float) -> dict:
    return {
        "model_name": name,
        "model_info": {"id": mid, "db_model": True, "input_cost_per_token": cost},
        "litellm_params": {"input_cost_per_token": cost, "output_cost_per_token": cost},
    }


@pytest.mark.asyncio
async def test_null_default_flags_instead_of_pushing_none(monkeypatch):
    """No rule + null default + an active override -> flag missing_default, don't
    push None. LiteLLM ignores a null cost on /model/update, so pushing None is a
    no-op that only churns reloads; the price can't be reverted without a default.
    """
    monkeypatch.setattr(
        job, "async_session_factory", lambda: _FakeSession([], [_null_default_catalog("m")])
    )
    fake = MagicMock()
    # The model currently carries a rule-applied override (2e-06).
    fake.get_model_info = AsyncMock(return_value=[_deployment_with_override("m", "m1", 2e-06)])
    fake.update_model = AsyncMock(return_value={})
    monkeypatch.setattr(job, "LiteLLMClient", MagicMock(return_value=fake))

    result = await job.apply_cost_schedule()

    fake.update_model.assert_not_awaited()
    assert result["missing_default"] == 1
    assert result["deployments_updated"] == 0


@pytest.mark.asyncio
async def test_null_default_is_idempotent_when_already_cleared(monkeypatch):
    """No rule + null default + no override left -> nothing to do (no reload churn)."""
    monkeypatch.setattr(
        job, "async_session_factory", lambda: _FakeSession([], [_null_default_catalog("m")])
    )
    fake = MagicMock()
    # No override in litellm_params; base price lives in model_info only.
    fake.get_model_info = AsyncMock(
        return_value=[
            {
                "model_name": "m",
                "model_info": {"id": "m1", "db_model": True, "input_cost_per_token": 9e-06},
                "litellm_params": {},
            }
        ]
    )
    fake.update_model = AsyncMock(return_value={})
    monkeypatch.setattr(job, "LiteLLMClient", MagicMock(return_value=fake))

    await job.apply_cost_schedule()

    fake.update_model.assert_not_awaited()
