# backend/tests/_storage.py
"""Shared builders for storage tests."""

import types
import uuid

SID = "11111111-1111-1111-1111-111111111111"
SID2 = "22222222-2222-2222-2222-222222222222"
CLUSTER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def binding(**kw):
    base = dict(
        id=uuid.uuid4(), storage_id=uuid.UUID(SID), cluster_id=None, namespace="default", type="pvc",
        claim_name="models-pvc", nfs_server=None, nfs_path=None, sub_path=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def legacy_entry(claim: str, mount: str, *, storage_id: str = SID, namespace: str = "default") -> dict:
    """A deployment entry exactly as migration 052 writes it (renders like the old pvc_* columns)."""
    return {
        "storage_id": storage_id,
        "mount_path": mount,
        "read_only": False,
        "volume_name": "model-weights",
        "resolved": {
            "binding_id": "44444444-4444-4444-4444-444444444444", "type": "pvc", "claim_name": claim,
            "namespace": namespace, "nfs_server": None, "nfs_path": None, "sub_path": None,
        },
    }
