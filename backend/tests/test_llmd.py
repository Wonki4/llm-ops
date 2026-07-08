"""llm-d stack API — ArgoCD CRD provisioning."""

from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.api.llmd import _argo_status, _k8s_error_message


def test_argo_status_from_cr_object():
    obj = {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy", "message": "ok"}}}
    assert _argo_status(obj) == {"sync_status": "Synced", "health_status": "Healthy", "status_message": "ok"}


def test_argo_status_unknown_when_absent():
    assert _argo_status(None)["sync_status"] == "Unknown"


def test_k8s_error_message_403_hint():
    msg = _k8s_error_message(ApiException(status=403, reason="Forbidden"))
    assert "RBAC" in msg or "permission" in msg.lower()


async def test_create_stack_applies_application(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "cluster_id": None,
                "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 201
    fake_k8s.apply_application.assert_awaited_once()
    ns, manifest = fake_k8s.apply_application.await_args.args
    assert ns == "argocd"
    assert manifest["metadata"]["namespace"] == "argocd"
    assert manifest["metadata"]["name"] == "llmd-demo"


async def test_create_stack_argocd_rbac_denied_502(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock(side_effect=ApiException(status=403, reason="Forbidden"))
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 502


async def test_create_stack_destination_server_from_placement(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock()
    fake_k8s.get_application = AsyncMock(return_value=None)
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argo-central", "https://gpu-cluster:6443")),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/llmd-stacks", json={
                "name": "demo", "target_model_name": "qwen", "cluster_id": None,
                "namespace": "team-a", "values_yaml": "",
            })
    assert resp.status_code == 201
    ns, manifest = fake_k8s.apply_application.await_args.args
    assert ns == "argo-central"
    assert manifest["metadata"]["namespace"] == "argo-central"
    assert manifest["spec"]["destination"]["server"] == "https://gpu-cluster:6443"


def _none_result():
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    r.scalars.return_value.all.return_value = []
    return r
