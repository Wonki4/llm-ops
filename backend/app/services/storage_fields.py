"""Request-model helpers shared by the recipe and deployment APIs."""

from __future__ import annotations

from typing import Any

from app.services import pd_serving
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


# A PVC with only these modes attaches to one node (RWO) or one pod (RWOP).
_SINGLE_ATTACH = {"ReadWriteOnce", "ReadWriteOncePod"}


def _claim_users(dep) -> dict[str, dict]:
    """claim → {"pods": total replicas mounting it, "write": any read-write mount} (P/D: both pools)."""
    if pd_serving.is_pd(dep):
        pools = [pd_serving.pool_row(dep, role) for role in ROLES]
    else:
        pools = [dep]
    out: dict[str, dict] = {}
    for pool in pools:
        replicas = int(getattr(pool, "replicas", None) or 0)
        for e in getattr(pool, "storages", None) or []:
            r = e.get("resolved") or {}
            if r.get("type") != "pvc" or not r.get("claim_name"):
                continue
            use = out.setdefault(r["claim_name"], {"pods": 0, "write": False})
            use["pods"] += replicas
            use["write"] |= not e.get("read_only")
    return out


def pvc_access_warnings(dep, pvcs: dict[str, dict]) -> list[str]:
    """Access-mode mismatches against live PVCs (``pvc_index`` rows):

    - ``pvc_single_node:CLAIM`` — RWO and more than one pod mounts it (P/D pools, replicas > 1):
      pods on another node stay in ContainerCreating (Multi-Attach error).
    - ``pvc_single_pod:CLAIM`` — RWOP and more than one pod mounts it: only one pod can start.
    - ``pvc_read_only:CLAIM`` — only ReadOnlyMany but mounted read-write.
    """
    out = []
    for claim, use in sorted(_claim_users(dep).items()):
        modes = set((pvcs.get(claim) or {}).get("access_modes") or [])
        if not modes:
            continue
        if use["write"]:
            modes.discard("ReadOnlyMany")  # a read-write mount can't use it
            if not modes:
                out.append(f"pvc_read_only:{claim}")
                continue
        if use["pods"] > 1 and modes <= _SINGLE_ATTACH:
            out.append(f"pvc_single_pod:{claim}" if modes == {"ReadWriteOncePod"} else f"pvc_single_node:{claim}")
    return out
