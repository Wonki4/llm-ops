"""Per-user personal-key grants: the model scope LiteLLM enforces for team-less keys.

A user's grant lives on the LiteLLM user row (``LiteLLM_UserTable.models``):

* ``[]`` or ``["no-default-models"]`` → *none*: no personal access (LiteLLM
  treats ``[]`` as "everything", so the portal never writes it and reads it
  as "never granted");
* ``["all-proxy-models"]`` → *all*;
* anything else → *custom*: model names and/or model access-group names,
  which LiteLLM expands at request time.

Pure helpers, no I/O.
"""

from __future__ import annotations

from typing import Literal

NO_DEFAULT_MODELS = "no-default-models"
ALL_PROXY_MODELS = "all-proxy-models"
PERSONAL_KEY_TAG = "personal-beta"
PERSONAL_KEY_TYPE = "personal"

GrantState = Literal["none", "all", "custom"]


def grant_state(models: list[str] | None) -> GrantState:
    names = [m for m in (models or []) if m]
    if not names or names == [NO_DEFAULT_MODELS] or all(m == NO_DEFAULT_MODELS for m in names):
        return "none"
    if ALL_PROXY_MODELS in names:
        return "all"
    return "custom"


def has_grant(models: list[str] | None) -> bool:
    return grant_state(models) != "none"


def grant_entries(models: list[str] | None) -> list[str]:
    """The custom entries (model / group names) without sentinels."""
    return [m for m in (models or []) if m and m not in (NO_DEFAULT_MODELS, ALL_PROXY_MODELS)]


def models_for(state: GrantState, selection: list[str] | None = None) -> list[str]:
    """What to write to the user row for a desired state."""
    if state == "none":
        return [NO_DEFAULT_MODELS]
    if state == "all":
        return [ALL_PROXY_MODELS]
    names = list(dict.fromkeys(m.strip() for m in (selection or []) if m and m.strip()))
    if not names:
        raise ValueError("custom access needs at least one model or access group")
    return names


def validate_selection(selection: list[str], *, model_names: set[str], access_groups: set[str]) -> None:
    unknown = [m for m in selection if m not in model_names and m not in access_groups]
    if unknown:
        raise ValueError(f"unknown models or access groups: {', '.join(sorted(set(unknown)))}")


def expand_grant(grant: list[str] | None, *, access_groups: dict[str, list[str]]) -> set[str] | None:
    """Model names the grant reaches (None = every model, for ``all``)."""
    state = grant_state(grant)
    if state == "none":
        return set()
    if state == "all":
        return None
    out: set[str] = set()
    for entry in grant_entries(grant):
        members = access_groups.get(entry)
        if members is not None:
            out.update(members)
            out.add(entry)  # a key may name the group itself
        else:
            out.add(entry)
    return out


def key_models_allowed(
    key_models: list[str] | None, grant: list[str] | None, *, access_groups: dict[str, list[str]]
) -> list[str]:
    """Entries of a key's ``models`` list that fall outside the user's grant ([] = fine)."""
    reach = expand_grant(grant, access_groups=access_groups)
    if reach is None:
        return []
    return [m for m in (key_models or []) if m not in reach]


def access_groups_from_model_info(rows: list[dict]) -> dict[str, list[str]]:
    """``{group: [model_name, ...]}`` from LiteLLM ``/model/info`` rows."""
    out: dict[str, list[str]] = {}
    for row in rows or []:
        name = row.get("model_name")
        groups = (row.get("model_info") or {}).get("access_groups") or []
        if not name:
            continue
        for g in groups:
            out.setdefault(str(g), [])
            if name not in out[str(g)]:
                out[str(g)].append(name)
    return out
