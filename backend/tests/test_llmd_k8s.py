"""Application-CR operations on K8sClient (CustomObjectsApi)."""

from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient

GVK = dict(group="argoproj.io", version="v1alpha1", plural="applications")


def _client_with(co):
    fake_api = MagicMock()
    fake_api.close = AsyncMock()
    return fake_api, patch.object(K8sClient, "_api_client", AsyncMock(return_value=fake_api)), \
        patch("app.clients.k8s.client.CustomObjectsApi", return_value=co)


async def test_apply_application_creates_when_absent():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    co.create_namespaced_custom_object = AsyncMock()
    co.patch_namespaced_custom_object = AsyncMock()
    _api, p_api, p_co = _client_with(co)
    manifest = {"metadata": {"name": "llmd-x"}, "spec": {}}
    with p_api, p_co:
        await K8sClient().apply_application("argocd", manifest)
    co.create_namespaced_custom_object.assert_awaited_once()
    co.patch_namespaced_custom_object.assert_not_called()
    kwargs = co.create_namespaced_custom_object.await_args.kwargs
    assert kwargs["namespace"] == "argocd" and kwargs["body"] == manifest
    assert kwargs["group"] == "argoproj.io" and kwargs["plural"] == "applications"


async def test_apply_application_patches_when_present():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(return_value={"metadata": {"name": "llmd-x"}})
    co.create_namespaced_custom_object = AsyncMock()
    co.patch_namespaced_custom_object = AsyncMock()
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        await K8sClient().apply_application("argocd", {"metadata": {"name": "llmd-x"}, "spec": {}})
    co.patch_namespaced_custom_object.assert_awaited_once()
    co.create_namespaced_custom_object.assert_not_called()


async def test_get_application_returns_none_on_404():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        assert await K8sClient().get_application("argocd", "llmd-x") is None


async def test_get_application_returns_object():
    co = MagicMock()
    co.get_namespaced_custom_object = AsyncMock(return_value={"status": {"sync": {"status": "Synced"}}})
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        obj = await K8sClient().get_application("argocd", "llmd-x")
    assert obj["status"]["sync"]["status"] == "Synced"


async def test_delete_application_swallows_404():
    co = MagicMock()
    co.delete_namespaced_custom_object = AsyncMock(side_effect=ApiException(status=404))
    _api, p_api, p_co = _client_with(co)
    with p_api, p_co:
        await K8sClient().delete_application("argocd", "llmd-x")  # no raise
    co.delete_namespaced_custom_object.assert_awaited_once()
