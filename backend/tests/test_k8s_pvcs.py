# backend/tests/test_k8s_pvcs.py
"""K8sClient.list_pvcs."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.k8s import K8sClient


def _pvc(name, phase, capacity="100Gi", sc="nfs-client", modes=("ReadWriteMany",)):
    p = MagicMock()
    p.metadata.name = name
    p.status.phase = phase
    p.status.capacity = {"storage": capacity}
    p.spec.storage_class_name = sc
    p.spec.access_modes = list(modes)
    return p


async def test_list_pvcs_sorted_with_phase_and_capacity():
    fake_api = MagicMock(close=AsyncMock())
    core = MagicMock()
    core.list_namespaced_persistent_volume_claim = AsyncMock(
        return_value=MagicMock(items=[_pvc("zeta", "Pending", None), _pvc("alpha", "Bound")])
    )
    with (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)),
        patch("app.clients.k8s.client.CoreV1Api", return_value=core),
    ):
        out = await K8sClient().list_pvcs("serving")
    core.list_namespaced_persistent_volume_claim.assert_awaited_once_with(namespace="serving")
    assert [p["name"] for p in out] == ["alpha", "zeta"]
    assert out[0] == {
        "name": "alpha", "phase": "Bound", "capacity": "100Gi", "storage_class": "nfs-client",
        "access_modes": ["ReadWriteMany"],
    }
    assert out[1]["capacity"] is None
    fake_api.close.assert_awaited()
