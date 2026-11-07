"""create_or_patch: an existing Deployment is replaced (so dropped fields go away); Services stay patched."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.clients.k8s import K8sClient
from app.services.model_deployment_manifests import LABEL_MODEL, LABEL_OWNER, OWNER

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
        return await k8s.create_or_patch("ns", manifests)


async def test_existing_deployment_is_replaced_not_patched():
    apps, core, api_client = _apis(deployment_exists=True)
    applied = apps.replace_namespaced_deployment.return_value
    applied.metadata.uid = "live-uid"
    applied.metadata.generation = 4
    rollout = await _apply(apps, core, api_client, [DEP, SVC])
    assert rollout == {"m-deployment": {"uid": "live-uid", "generation": 4}}
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


async def test_scaling_patches_only_replica_count():
    apps, _, api_client = _apis(deployment_exists=True)
    k8s = K8sClient()
    with (
        patch.object(K8sClient, "_api_client", AsyncMock(return_value=api_client)),
        patch("app.clients.k8s.client.AppsV1Api", return_value=apps),
    ):
        await k8s.scale_deployment("ns", "m-prefill-deployment", 0)
    apps.patch_namespaced_deployment.assert_awaited_once_with(
        name="m-prefill-deployment", namespace="ns", body={"spec": {"replicas": 0}},
        _content_type="application/merge-patch+json",
    )
    api_client.close.assert_awaited_once()


@pytest.mark.parametrize("kind", ["Deployment", "Service", "Ingress"])
async def test_conflicting_serving_resource_is_never_overwritten(kind):
    # Both model names render the same Kubernetes stem, but select different workloads.
    labels = {LABEL_OWNER: OWNER, LABEL_MODEL: "a.b"}
    manifest = {"kind": kind, "metadata": {"name": "a-b", "labels": labels}}
    live = SimpleNamespace(metadata=SimpleNamespace(labels={**labels, LABEL_MODEL: "a-b"}, resource_version="7"))
    create, update = AsyncMock(), AsyncMock()
    with pytest.raises(ApiException) as conflict:
        await K8sClient._upsert(AsyncMock(return_value=live), create, update, "a-b", "ns", manifest)
    assert conflict.value.status == 409
    create.assert_not_awaited()
    update.assert_not_awaited()

    live.metadata.labels = labels
    await K8sClient._upsert(AsyncMock(return_value=live), create, update, "a-b", "ns", manifest)
    assert update.await_args.kwargs["body"]["metadata"]["resourceVersion"] == "7"
