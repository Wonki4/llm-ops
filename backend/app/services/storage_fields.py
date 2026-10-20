"""Request-model helpers shared by the recipe and deployment APIs."""

from __future__ import annotations

from typing import Any

from app.services.pd_serving import ROLES
from app.services.storage_mounts import model_path_warnings

LEGACY_PVC_HINT = "pvc_name/pvc_mount_path were replaced by storages: [{storage_id, mount_path, read_only}]"


def reject_legacy_pvc(data: Any) -> Any:
    """``mode="before"`` validator body: refuse the removed fields (also inside P/D pools)."""
    if not isinstance(data, dict):
        return data
    pools = [data] + [p for r in ROLES if isinstance(p := (data.get("pd_config") or {}).get(r), dict)]
    for block in pools:
        if block.get("pvc_name") is not None or block.get("pvc_mount_path") is not None:
            raise ValueError(LEGACY_PVC_HINT)
        block.pop("pvc_name", None)
        block.pop("pvc_mount_path", None)
    return data


def recipe_warnings(model_path: str | None, storages: list[dict], pd_config: dict | None) -> list[str]:
    """``model_path_outside_storages`` for the base row or any pool, once."""
    out = set(model_path_warnings(model_path, storages))
    for role in ROLES:
        pool = (pd_config or {}).get(role) or {}
        pool_storages = pool.get("storages")  # None inherits the base list; [] means no storages
        entries = storages if pool_storages is None else pool_storages
        out |= set(model_path_warnings(pool.get("model_path") or model_path, entries))
    return sorted(out)
