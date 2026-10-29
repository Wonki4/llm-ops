"""create_or_patch: an existing Deployment is replaced (so dropped fields go away); Services stay patched."""

from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient

DEP = {
    "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "m-deployment", "namespace": "ns"}, "spec": {},
}
SVC = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "m-service", "namespace": "ns"}, "spec": {}}


def _apis(deployment_exists: bool):
    apps = MagicMock()
    if deployment_exists:
        apps.read_namespaced_deployment = AsyncMock(return_value=object())
    else:
        apps.read_namespaced_deployment = AsyncMock(side_effect=ApiException(status=404, reason="nope"))
    apps.replace_namespaced_deployment = AsyncMock()
    apps.patch_namespaced_deployment = AsyncMock()
    apps.create_namespaced_deployment = AsyncMock()
    core = MagicMock()
    core.read_namespaced_service = AsyncMock(return_value=object())
    core.replace_namespaced_service = AsyncMock()
    core.patch_namespaced_service = AsyncMock()
    core.create_namespaced_service = AsyncMock()
    api_client = MagicMock()
    api_client.close = AsyncMock()
    return apps, core, api_client


async def _apply(apps, core, api_client, manifests):
    k8s = K8sClient()
    with (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=api_client)),
        patch("app.clients.k8s.client.AppsV1Api", return_value=apps),
        patch("app.clients.k8s.client.CoreV1Api", return_value=core),
        patch("app.clients.k8s.client.NetworkingV1Api", return_value=MagicMock()),
    ):
        await k8s.create_or_patch("ns", manifests)


async def test_existing_deployment_is_replaced_not_patched():
    apps, core, api_client = _apis(deployment_exists=True)
    await _apply(apps, core, api_client, [DEP, SVC])
    apps.replace_namespaced_deployment.assert_awaited_once_with(name="m-deployment", namespace="ns", body=DEP)
    apps.patch_namespaced_deployment.assert_not_awaited()
    apps.create_namespaced_deployment.assert_not_awaited()
    # Services keep the merge patch: clusterIP is immutable and a replace would have to carry it.
    core.patch_namespaced_service.assert_awaited_once_with(name="m-service", namespace="ns", body=SVC)
    core.replace_namespaced_service.assert_not_awaited()


async def test_missing_deployment_is_created():
    apps, core, api_client = _apis(deployment_exists=False)
    await _apply(apps, core, api_client, [DEP])
    apps.create_namespaced_deployment.assert_awaited_once_with(namespace="ns", body=DEP)
    apps.replace_namespaced_deployment.assert_not_awaited()
    apps.patch_namespaced_deployment.assert_not_awaited()
