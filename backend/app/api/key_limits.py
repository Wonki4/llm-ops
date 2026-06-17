"""Helpers for resolving a key's effective per-model rate limits.

Kept in a dependency-free module so both `keys.py` and `teams.py` can import it
without creating a circular import (`keys` already imports from `teams`).
"""


def effective_model_limits(
    key_metadata: dict | None, team_metadata: dict | None
) -> dict:
    """Resolve a key's effective per-model TPM/RPM limits.

    A key's own `model_tpm_limit`/`model_rpm_limit` (LiteLLM stores these in the
    key metadata) fully OVERRIDE the team value for that metric — LiteLLM does
    not merge them per-model. When the key has no override, the team-level limit
    (LiteLLM_TeamTable.metadata) applies at runtime, so we surface it as the
    effective value and flag it as inherited for display.
    """
    key_metadata = key_metadata or {}
    team_metadata = team_metadata or {}
    own_tpm = key_metadata.get("model_tpm_limit") or None
    own_rpm = key_metadata.get("model_rpm_limit") or None
    eff_tpm = own_tpm or team_metadata.get("model_tpm_limit") or None
    eff_rpm = own_rpm or team_metadata.get("model_rpm_limit") or None
    return {
        "model_tpm_limit": eff_tpm,
        "model_rpm_limit": eff_rpm,
        "model_tpm_inherited": bool(eff_tpm) and not own_tpm,
        "model_rpm_inherited": bool(eff_rpm) and not own_rpm,
    }
