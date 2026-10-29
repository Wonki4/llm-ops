"""Overlay a serving recipe's fields onto an existing deployment ("레시피에서 다시 적용").

A recipe owns the *serving* configuration; the deployment owns its instance
identity (model name, LiteLLM name, cluster, namespace, replicas, ingress,
GPU type, router). Re-applying overwrites only the former, so the rollout
keeps the Service, the LiteLLM entry and the replica count it already has.

Pure functions, no I/O.
"""

from __future__ import annotations

import copy
import types

from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving

# The deployment columns a recipe sets (everything else is instance-owned).
RECIPE_FIELDS: tuple[str, ...] = (
    "image",
    "model_path",
    "engine",
    "engine_args",
    "vllm_extra_args",
    "env",
    "gpu_count",
    "gpu_resource_key",
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
    "node_selector",
    "tolerations",
    "storages",
    "probes",
    "runtime",
    "pd_config",
)

_ENTRY_KEYS = ("storage_id", "mount_path", "read_only")


def _entry(e: dict) -> dict:
    """A storage entry as the recipe spells it (the deployment adds resolution keys)."""
    return {k: e.get(k) for k in _ENTRY_KEYS}


def recipe_values(recipe, dep) -> dict:
    """The recipe's value for every ``RECIPE_FIELDS`` entry, ready for ``_apply_update``.

    ``pd_config`` keeps the deployment's per-pool ``replicas`` (the deploy dialog
    sets those at launch; the recipe has no say). The recipe object is not mutated.
    """
    values = {field: copy.deepcopy(getattr(recipe, field, None)) for field in RECIPE_FIELDS}
    values["storages"] = [_entry(e) for e in (getattr(recipe, "storages", None) or [])]
    pd = values.get("pd_config")
    if pd is not None:
        current = dict(getattr(dep, "pd_config", None) or {})
        for role in pd_serving.ROLES:
            replicas = (current.get(role) or {}).get("replicas")
            if replicas is not None:
                pd[role] = {**(pd.get(role) or {}), "replicas": replicas}
    return values


def _norm(field: str, value):
    if value is None or value == {} or value == []:
        return None
    if field == "storages":
        return sorted((str(e.get("storage_id")), e.get("mount_path"), bool(e.get("read_only", True))) for e in value)
    return value


def changes(dep, values: dict) -> list[dict]:
    """``[{"field", "current", "proposed"}]`` for the fields whose normalised values differ."""
    out = []
    for field in RECIPE_FIELDS:
        current = getattr(dep, field, None)
        proposed = values.get(field)
        if _norm(field, current) != _norm(field, proposed):
            if field == "storages":
                current = [_entry(e) for e in (current or [])]
            out.append({"field": field, "current": current, "proposed": proposed})
    return out


def proposed_row(dep, values: dict) -> types.SimpleNamespace:
    """The deployment as it would be after the overlay, for the rollout / registration checks.

    ``storages`` stays the deployment's resolved list: the checks do not read it and
    the recipe's unresolved entries cannot render volumes.
    """
    attrs = {c.name: getattr(dep, c.name, None) for c in CustomModelDeployment.__table__.columns}
    attrs.update({k: v for k, v in values.items() if k != "storages"})
    return types.SimpleNamespace(**attrs)
