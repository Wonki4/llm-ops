"""update_team_settings must not touch the team's default member budget /
metadata unless the member-budget fields actually change.

Regression: the frontend sends every default_member_* field on every settings
save, so a description-only (or tpm-only) save used to forward team_member_budget
(often null) to LiteLLM's /team/update. That makes LiteLLM rewrite
metadata.team_member_budget_id and re-upsert the shared budget row — releasing
the team's default member budget. The guard forwards ONLY fields that actually
changed vs the current values, and forwards nothing when the member-budget group
is unchanged.
"""

from app.api.teams import _changed_member_budget_kwargs

CURRENT = {"budget": 20.0, "tpm": 1000, "rpm": 60}


def test_no_kwargs_when_member_values_unchanged():
    # Description changed but all member-budget values resent unchanged → no
    # kwargs → no /team/update → team metadata + budget row untouched.
    updates = {
        "description": "new desc",
        "default_member_budget": 20.0,
        "default_member_tpm_limit": 1000,
        "default_member_rpm_limit": 60,
    }
    assert _changed_member_budget_kwargs(updates, CURRENT) == {}


def test_only_the_changed_field_is_forwarded():
    updates = {
        "default_member_budget": 20.0,  # unchanged
        "default_member_tpm_limit": 2000,  # changed
        "default_member_rpm_limit": 60,  # unchanged
    }
    assert _changed_member_budget_kwargs(updates, CURRENT) == {"team_member_tpm_limit": 2000}


def test_budget_change_is_forwarded():
    assert _changed_member_budget_kwargs({"default_member_budget": 30.0}, CURRENT) == {
        "team_member_budget": 30.0
    }


def test_first_time_set_from_unset():
    empty = {"budget": None, "tpm": None, "rpm": None}
    updates = {"default_member_budget": 20.0, "default_member_tpm_limit": 1000}
    assert _changed_member_budget_kwargs(updates, empty) == {
        "team_member_budget": 20.0,
        "team_member_tpm_limit": 1000,
    }


def test_absent_field_never_forwarded():
    # A field not present in the payload is never forwarded (nothing to compare).
    assert _changed_member_budget_kwargs({"description": "x"}, CURRENT) == {}


def test_explicit_clear_is_forwarded():
    # Genuinely clearing a previously-set budget (20 -> null) still forwards the
    # intent (None != 20.0).
    assert _changed_member_budget_kwargs({"default_member_budget": None}, CURRENT) == {
        "team_member_budget": None
    }
