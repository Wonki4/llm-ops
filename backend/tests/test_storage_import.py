"""Recipe import: PVC volumes map to registered storages."""

from app.services.recipe_import import attach_storages, build_recipe_draft
from tests._storage import SID


def _spec(volumes, mounts):
    return {
        "container": {"image": "vllm/vllm-openai:v0.9", "command": ["vllm", "serve", "/models/llama"], "args": [],
                      "env": [], "resources": {}, "volume_mounts": mounts},
        "volumes": volumes, "replicas": 1, "node_selector": None, "tolerations": None,
    }


def test_draft_collects_every_pvc_mount():
    spec = _spec(
        [{"name": "w", "persistentVolumeClaim": {"claimName": "models-pvc"}},
         {"name": "c", "persistentVolumeClaim": {"claimName": "cache-pvc"}}],
        [{"name": "w", "mountPath": "/models", "readOnly": True}, {"name": "c", "mountPath": "/cache"}],
    )
    out = build_recipe_draft(spec)
    assert out["draft"]["pvc_mounts"] == [
        {"claim_name": "models-pvc", "mount_path": "/models", "read_only": True},
        {"claim_name": "cache-pvc", "mount_path": "/cache", "read_only": False},
    ]
    assert "pvc_name" not in out["draft"]
    assert all(w["code"] != "multi_pvc" for w in out["warnings"])


def test_attach_storages_matches_and_warns():
    draft = {"pvc_mounts": [{"claim_name": "models-pvc", "mount_path": "/models", "read_only": True},
                            {"claim_name": "cache-pvc", "mount_path": "/cache", "read_only": False}]}
    warnings: list[dict] = []
    attach_storages(draft, warnings, {"models-pvc": SID})
    assert draft == {"storages": [{"storage_id": SID, "mount_path": "/models", "read_only": True}]}
    assert [(w["code"], w.get("detail")) for w in warnings] == [("unregistered_pvc", "cache-pvc")]
