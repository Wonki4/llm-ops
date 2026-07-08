"""argocd_placement_for — where a stack's Application CR goes."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.clusters import LOCAL_DEST_SERVER, argocd_placement_for

HOST_ID = uuid.uuid4()
TARGET_ID = uuid.uuid4()


def _row(**kw):
    base = dict(
        id=TARGET_ID,
        context="ctx",
        kubeconfig_encrypted="enc",
        argocd_namespace="argocd",
        argocd_host_cluster_id=None,
        argocd_dest_server=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _db_returning(rows_by_id):
    """Fake async db: execute() resolves scalar_one_or_none by the id the
    select's ``.where(CustomK8sCluster.id == <id>)`` clause binds."""

    async def execute(stmt):
        wanted = stmt.whereclause.right.value
        r = MagicMock()
        r.scalar_one_or_none.return_value = rows_by_id.get(wanted)
        return r

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    return db


def _patched():
    fake_client = MagicMock(name="K8sClient")
    p_client = patch("app.services.clusters.K8sClient", return_value=fake_client)
    p_crypto = patch("app.services.clusters.crypto.decrypt", return_value="apiVersion: v1")
    p_yaml = patch("app.services.clusters.yaml.safe_load", return_value={"kind": "Config"})
    return fake_client, p_client, p_crypto, p_yaml


async def test_null_cluster_all_local_defaults():
    fake_client, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml, patch("app.config.settings") as s:
        s.argocd_namespace = "argocd-global"
        k8s, ns, dest = await argocd_placement_for(_db_returning({}), None)
    assert k8s is fake_client
    assert ns == "argocd-global"
    assert dest == LOCAL_DEST_SERVER


async def test_self_managed_cluster_uses_own_row():
    target = _row(argocd_namespace="argo-sys")
    fake_client, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml:
        k8s, ns, dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert k8s is fake_client
    assert ns == "argo-sys"
    assert dest == LOCAL_DEST_SERVER


async def test_dest_server_from_target_row():
    target = _row(argocd_dest_server="https://10.9.9.9:6443")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml:
        _k8s, _ns, dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert dest == "https://10.9.9.9:6443"


async def test_host_cluster_supplies_client_and_namespace():
    host = _row(id=HOST_ID, context="host-ctx", argocd_namespace="argo-central")
    target = _row(argocd_host_cluster_id=HOST_ID, argocd_dest_server="https://t:6443")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client as client_cls, p_crypto, p_yaml:
        _k8s, ns, dest = await argocd_placement_for(
            _db_returning({TARGET_ID: target, HOST_ID: host}), str(TARGET_ID)
        )
    assert ns == "argo-central"
    assert dest == "https://t:6443"
    assert client_cls.call_args.kwargs["context"] == "host-ctx"


async def test_dangling_host_falls_back_to_target_itself():
    target = _row(argocd_host_cluster_id=HOST_ID, argocd_namespace="argo-t")
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client as client_cls, p_crypto, p_yaml:
        _k8s, ns, _dest = await argocd_placement_for(_db_returning({TARGET_ID: target}), TARGET_ID)
    assert ns == "argo-t"
    assert client_cls.call_args.kwargs["context"] == "ctx"


async def test_unknown_cluster_id_falls_back_to_defaults():
    _c, p_client, p_crypto, p_yaml = _patched()
    with p_client, p_crypto, p_yaml, patch("app.config.settings") as s:
        s.argocd_namespace = "argocd"
        _k8s, ns, dest = await argocd_placement_for(_db_returning({}), uuid.uuid4())
    assert ns == "argocd"
    assert dest == LOCAL_DEST_SERVER
