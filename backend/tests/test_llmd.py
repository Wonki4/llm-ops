"""llm-d stack API — ArgoCD CRD provisioning."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes_asyncio.client.exceptions import ApiException

from app.api.llmd import _application_for, _argo_status, _k8s_error_message, _serialize, _values_for


def test_dump_values_yaml_preserves_literal_blocks():
    # A multi-line string value (e.g. an embedded envoy.yaml config) must render
    # as a YAML `|` literal block, not a quoted string with escaped \n — the
    # editor readback was mangled by plain safe_dump.
    import yaml

    from app.api.llmd import _dump_values_yaml

    data = {"router": {"proxy": {"cfg": "admin:\n  port: 19000\nlisteners: []\n"}}}
    out = _dump_values_yaml(data)
    assert "cfg: |" in out           # literal block style
    assert "\\n" not in out          # no escaped newlines
    assert yaml.safe_load(out) == data  # lossless round-trip


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
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
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
    # The Ingress is upserted to the target cluster in the stack's namespace.
    fake_target.create_or_patch.assert_awaited_once()
    ing_ns, manifests = fake_target.create_or_patch.await_args.args
    assert ing_ns == "team-a"
    ing = manifests[0]
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "llmd-demo-ingress"
    assert ing["spec"]["rules"][0]["host"] == "llmd-demo.llm-d.local"
    assert ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "llmd-demo-epp"


async def test_create_stack_argocd_rbac_denied_502(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_none_result())
    fake_k8s = MagicMock()
    fake_k8s.apply_application = AsyncMock(side_effect=ApiException(status=403, reason="Forbidden"))
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=MagicMock())):
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
    fake_target = MagicMock()
    fake_target.create_or_patch = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argo-central", "https://gpu-cluster:6443")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
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


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(), name="s", target_model_name="qwen", cluster_id=None,
        namespace="team-a", argo_app_name="llmd-s", helm_values={}, values_snapshot={},
        chart_repo=None, chart_name=None, chart_version=None,
        epp_registry=None, epp_repository=None, epp_tag=None,
        ingress_host=None, ingress_class=None,
        created_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_application_uses_stack_chart_override_when_set():
    stack = _stack(chart_repo="oci://mirror.internal/charts", chart_name="llmd", chart_version="1.2.3")
    app = _application_for(stack, "argocd", "https://kubernetes.default.svc")
    src = app["spec"]["source"]
    assert src["repoURL"] == "mirror.internal/charts"  # oci:// stripped for ArgoCD 3.x
    assert src["chart"] == "llmd"
    assert src["targetRevision"] == "1.2.3"


def test_application_falls_back_to_settings_when_override_null():
    from app.config import settings

    app = _application_for(_stack(), "argocd", "https://kubernetes.default.svc")
    src = app["spec"]["source"]
    assert src["repoURL"] == settings.llmd_chart_repo.removeprefix("oci://")
    assert src["chart"] == settings.llmd_chart_name
    assert src["targetRevision"] == settings.llmd_chart_version


def test_values_use_stack_epp_override_when_set():
    stack = _stack(epp_registry="mirror.internal", epp_repository="llm-d/epp", epp_tag="v9")
    img = _values_for(stack)["router"]["epp"]["image"]
    assert img == {"registry": "mirror.internal", "repository": "llm-d/epp", "tag": "v9"}


def test_values_fall_back_to_settings_epp_when_null():
    from app.config import settings

    img = _values_for(_stack())["router"]["epp"]["image"]
    assert img["registry"] == settings.llmd_epp_image_registry
    assert img["repository"] == settings.llmd_epp_image_repository
    assert img["tag"] == settings.llmd_epp_image_tag


def test_serialize_reports_effective_and_overrides():
    from app.config import settings

    over = _serialize(_stack(chart_repo="oci://mirror/x"), {"sync_status": "Synced"})
    assert over["chart_repo"] == "oci://mirror/x"                 # effective = override
    assert over["chart_overrides"]["chart_repo"] == "oci://mirror/x"
    assert over["chart_overrides"]["chart_name"] is None
    base = _serialize(_stack(), {"sync_status": "Synced"})
    assert base["chart_repo"] == settings.llmd_chart_repo         # effective = default
    assert base["chart_overrides"]["chart_repo"] is None
    assert base["epp_image"] == (
        f"{settings.llmd_epp_image_registry}/{settings.llmd_epp_image_repository}:{settings.llmd_epp_image_tag}"
    )


async def test_chart_defaults_endpoint(client_for_user, super_user, mock_db):
    from app.config import settings

    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/llmd-stacks/chart-defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["chart_repo"] == settings.llmd_chart_repo
    assert body["epp_registry"] == settings.llmd_epp_image_registry
    assert body["epp_tag"] == settings.llmd_epp_image_tag


def _result_with(stack):
    r = MagicMock()
    r.scalar_one_or_none.return_value = stack
    return r


async def test_delete_stack_removes_ingress(client_for_user, super_user, mock_db):
    stack = _stack(name="demo", namespace="team-a", argo_app_name="llmd-demo", cluster_id=None)
    mock_db.execute = AsyncMock(return_value=_result_with(stack))
    fake_k8s = MagicMock()
    fake_k8s.delete_application = AsyncMock()
    fake_target = MagicMock()
    fake_target.delete = AsyncMock()
    with patch(
        "app.api.llmd.argocd_placement_for",
        AsyncMock(return_value=(fake_k8s, "argocd", "https://kubernetes.default.svc")),
    ), patch("app.api.llmd.k8s_for_cluster", AsyncMock(return_value=fake_target)):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/admin/llmd-stacks/{stack.id}")
    assert resp.status_code == 200
    fake_k8s.delete_application.assert_awaited_once()
    fake_target.delete.assert_awaited_once_with("team-a", {"ingress": "llmd-demo-ingress"})


def test_serialize_reports_ingress_host():
    over = _serialize(_stack(argo_app_name="llmd-demo"), {"sync_status": "Synced"})
    # No per-stack override -> effective host is {app}.{global domain}, class is
    # the global default, and the raw overrides are null.
    assert over["ingress_host"] == "llmd-demo.llm-d.local"
    assert over["ingress_overrides"] == {"ingress_host": None, "ingress_class": None}


def test_serialize_reports_ingress_overrides_when_set():
    over = _serialize(
        _stack(argo_app_name="llmd-demo", ingress_host="my.corp.internal", ingress_class="nginx"),
        {"sync_status": "Synced"},
    )
    assert over["ingress_host"] == "my.corp.internal"  # override wins over {app}.{domain}
    assert over["ingress_class"] == "nginx"
    assert over["ingress_overrides"] == {"ingress_host": "my.corp.internal", "ingress_class": "nginx"}


def test_values_ingress_resolvers_prefer_override():
    from app.api.llmd import _ingress_class, _ingress_host
    from app.config import settings

    over = _stack(argo_app_name="llmd-demo", ingress_host="host.x", ingress_class="cls")
    assert _ingress_host(over) == "host.x"
    assert _ingress_class(over) == "cls"
    # NULL override -> computed host + global class default
    base = _stack(argo_app_name="llmd-demo")
    assert _ingress_host(base) == "llmd-demo.llm-d.local"
    assert _ingress_class(base) == settings.llmd_ingress_class
