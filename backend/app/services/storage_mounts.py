# backend/app/services/storage_mounts.py
"""Storage lists on recipes/deployments: validation, binding pick, volume rendering.

An entry is ``{"storage_id", "mount_path", "read_only"}``. Deployments add
``"volume_name"`` and ``"resolved"`` (the binding snapshot taken at deploy
time, see storage_store.resolve_entries); only resolved entries render.
Pure functions — no DB, no K8s.
"""

from __future__ import annotations

import posixpath
import re
import uuid

# pd_serving must not import this module at load time (its pool validator imports
# validate_entries lazily) — otherwise the two modules would import each other.
from app.services.pd_serving import ROLES

LEGACY_VOLUME_NAME = "model-weights"
STORAGE_KINDS = ("weights", "cache", "data", "other")
# Volume names the manifest builders add themselves (model_deployment_manifests: /dev/shm).
_RESERVED_VOLUME_NAMES = frozenset({"shm"})
_RESERVED_MOUNTS = ("/dev/shm",)
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")


def validate_storage_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name):
        raise ValueError("storage name must match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ (max 50 chars)")
    return name


def default_volume_name(storage_name: str) -> str:
    return f"st-{storage_name}"


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _mount_path(raw) -> str:
    if not isinstance(raw, str) or not raw.strip().startswith("/"):
        raise ValueError(f"mount_path must be an absolute path: {raw!r}")
    path = posixpath.normpath(raw.strip())
    if path == "/":
        raise ValueError("mount_path cannot be /")
    for reserved in _RESERVED_MOUNTS:
        if _overlaps(path, reserved):
            raise ValueError(f"mount_path {path} collides with the reserved {reserved}")
    return path


def validate_entries(raw: list | None) -> list[dict]:
    """Normalise a storages list; resolution keys are dropped (the server recomputes them)."""
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            raise ValueError("each storage must be an object {storage_id, mount_path, read_only}")
        try:
            sid = str(uuid.UUID(str(item.get("storage_id"))))
        except ValueError:
            raise ValueError(f"invalid storage_id: {item.get('storage_id')!r}")
        if any(e["storage_id"] == sid for e in out):
            raise ValueError(f"storage {sid} is listed twice")
        path = _mount_path(item.get("mount_path"))
        for e in out:
            if _overlaps(path, e["mount_path"]):
                raise ValueError(f"mount paths overlap: {e['mount_path']} and {path}")
        out.append({"storage_id": sid, "mount_path": path, "read_only": bool(item.get("read_only", True))})
    return out


def model_path_warnings(model_path: str | None, entries: list[dict]) -> list[str]:
    """Warn when an absolute model path is not under any mounted storage (HF repo ids pass)."""
    if not model_path or not model_path.startswith("/"):
        return []
    for e in entries:
        mount = e["mount_path"]
        if model_path == mount or model_path.startswith(mount.rstrip("/") + "/"):
            return []
    return ["model_path_outside_storages"]


def pick_binding(bindings: list, storage_id: str, cluster_id: uuid.UUID | None, namespace: str):
    """Exact (cluster, namespace) binding, else the cluster-wide one, else None."""
    exact = wide = None
    for b in bindings:
        if str(b.storage_id) != str(storage_id) or b.cluster_id != cluster_id:
            continue
        if b.namespace == namespace:
            exact = b
        elif b.namespace is None:
            wide = b
    return exact or wide


def snapshot(binding) -> dict:
    return {
        "binding_id": str(binding.id),
        "type": binding.type,
        "claim_name": binding.claim_name,
        "namespace": binding.namespace,
        "nfs_server": binding.nfs_server,
        "nfs_path": binding.nfs_path,
        "sub_path": binding.sub_path,
    }


def stale_entries(entries: list[dict], bindings: list, cluster_id: uuid.UUID | None, namespace: str) -> list[str]:
    """Storage ids whose current binding differs from the entry's snapshot (deleted, edited or shadowed)."""
    stale: list[str] = []
    for e in entries:
        sid = str(e["storage_id"])
        b = pick_binding(bindings, sid, cluster_id, namespace)
        if (b is None or snapshot(b) != e.get("resolved")) and sid not in stale:
            stale.append(sid)
    return stale


def check_volume_names(entries: list[dict]) -> None:
    """Rendered volume names must be unique and must not shadow a reserved one (``shm``)."""
    seen: set[str] = set()
    for e in entries:
        name = e.get("volume_name") or LEGACY_VOLUME_NAME
        if name in _RESERVED_VOLUME_NAMES:
            raise ValueError(f"volume name {name!r} is reserved")
        if name in seen:
            raise ValueError(f"volume name {name!r} is used twice")
        seen.add(name)


def to_volumes(entries: list[dict] | None, *, force_read_only: bool = False) -> tuple[list[dict], list[dict]]:
    """(volumes, volumeMounts) for resolved entries. ``readOnly`` keys appear only when read-only,
    so a migrated read-write entry renders exactly like the old pvc_* columns."""
    volumes: list[dict] = []
    mounts: list[dict] = []
    for e in entries or []:
        r = e.get("resolved")
        if not r:
            continue
        name = e.get("volume_name") or LEGACY_VOLUME_NAME
        ro = force_read_only or bool(e.get("read_only"))
        if r["type"] == "pvc":
            src: dict = {"claimName": r["claim_name"]}
            key = "persistentVolumeClaim"
        else:
            src = {"server": r["nfs_server"], "path": r["nfs_path"]}
            key = "nfs"
        if ro:
            src["readOnly"] = True
        volumes.append({"name": name, key: src})
        mount: dict = {"name": name, "mountPath": e["mount_path"]}
        if ro:
            mount["readOnly"] = True
        if r.get("sub_path"):
            mount["subPath"] = r["sub_path"]
        mounts.append(mount)
    return volumes, mounts


def all_entries(row) -> list[dict]:
    """The base storages list, then each P/D pool's own list (row, request body or namespace)."""
    entries = list(getattr(row, "storages", None) or [])
    cfg = getattr(row, "pd_config", None) or {}
    for role in ROLES:
        entries += list((cfg.get(role) or {}).get("storages") or [])
    return entries


def entry_storage_ids(row) -> set[str]:
    """Every storage a recipe/deployment references, P/D pool overrides included."""
    return {str(e.get("storage_id")) for e in all_entries(row)}


def _weights(volume: dict, mount_path: str) -> tuple[list, list]:
    return [{"name": LEGACY_VOLUME_NAME, **volume}], [
        {"name": LEGACY_VOLUME_NAME, "mountPath": mount_path, "readOnly": True}
    ]


def legacy_pvc_volumes(claim: str | None, mount_path: str | None) -> tuple[list, list]:
    """Read-only weights PVC from a pre-052 benchmark snapshot (``pvc_name`` / ``pvc_mount_path``)."""
    if not claim or not mount_path:
        return [], []
    return _weights({"persistentVolumeClaim": {"claimName": claim, "readOnly": True}}, mount_path)


def nfs_override_volumes(params: dict | None) -> tuple[list, list] | None:
    """A benchmark run's explicit NFS override (``params.nfs_*``), or None when not given."""
    p = params or {}
    server, path, mount = p.get("nfs_server"), p.get("nfs_path"), p.get("nfs_mount_path")
    if not (server and path and mount):
        return None
    return _weights({"nfs": {"server": server, "path": path, "readOnly": True}}, mount)
