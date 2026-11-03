"""Common pod labels and annotations: fixed keys from portal settings, values fixed or filled from the deployment."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from app.services import common_labels
from app.services.llmd_links import portal_server
from app.services.model_deployment_manifests import LABEL_MODEL, LABEL_OWNER, OWNER, build_all, pod_labels
from tests.test_deployment_reapply import _patches, _recipe, _row
from tests.test_pd_serving import _dep as _pd_dep
from tests.test_pd_serving import _extras
from tests.test_storage_recipes_deployments import DEPLOY, _k8s, _refresh, _result

RULES = {"team": "ml-platform", "app": "{{model_name}}", "env": "{{ namespace }}", "gpu": "{{gpu_type}}"}


def _render(rules, model_name="qwen3-32b", namespace="prod", gpu_type="H100"):
    return common_labels.render(rules, model_name=model_name, namespace=namespace, gpu_type=gpu_type)


# ─── rules ────────────────────────────────────────────────────────────────────


def test_render_fills_placeholders_and_keeps_fixed_values():
    assert _render(RULES) == {"team": "ml-platform", "app": "qwen3-32b", "env": "prod", "gpu": "H100"}


def test_render_makes_every_value_a_valid_label_value():
    rules = {"gpu": "x-{{gpu_type}}", "card": "{{gpu_type}}", "long": "{{namespace}}-{{model_name}}"}
    out = _render(rules, model_name="m" * 40, namespace="ns-" + "n" * 40, gpu_type="A100 80GB")
    assert out["gpu"] == "x-A100-80GB" and out["card"] == "A100-80GB"
    assert len(out["long"]) <= 63 and out["long"][-1].isalnum()
    # no GPU type: the key stays (keys are fixed), the dangling '-' goes
    assert _render(rules, gpu_type=None)["gpu"] == "x" and _render(rules, gpu_type=None)["card"] == ""


def test_render_without_rules_is_empty():
    assert _render(None) == {} and _render({}) == {}


def test_validate_accepts_what_k8s_accepts():
    rules = {
        "team": "ml-platform",
        "example.com/cost-center": "cc-1",
        "app.kubernetes.io/name": "{{model_name}}",
        "tier": "",
        "Zone_A.b": "{{namespace}}.{{gpu_type}}",
    }
    assert common_labels.validate_rules(rules) == rules


@pytest.mark.parametrize("key", ["llm-ops/team", "llm-d.ai/model", "llm-d.ai/role"])
def test_validate_rejects_portal_owned_keys(key):
    with pytest.raises(ValueError, match="managed by the portal"):
        common_labels.validate_rules({key: "x"})


@pytest.mark.parametrize("key", ["", "team name", "-team", "team-", "a/b/c", "Example.com/x", "/x", "x" * 64])
def test_validate_rejects_keys_k8s_refuses(key):
    with pytest.raises(ValueError, match="label key"):
        common_labels.validate_rules({key: "x"})


def test_validate_rejects_unknown_placeholders_and_bad_text():
    with pytest.raises(ValueError, match=r"unknown placeholder \{\{cluster\}\}"):
        common_labels.validate_rules({"c": "{{cluster}}"})
    for value in ("ml platform", "{{model_name}", "a/b", "x" * 64):
        with pytest.raises(ValueError, match="label value"):
            common_labels.validate_rules({"team": value})


ANNOTATIONS = {"owner": "ML Platform <ml@example.com>", "summary": "{{model_name}} on {{gpu_type}} in {{ namespace }}"}


def test_annotations_fill_placeholders_and_keep_any_text():
    rules = {**ANNOTATIONS, "long": "x" * 300}
    out = common_labels.render_annotations(rules, model_name="qwen3-32b", namespace="prod", gpu_type="A100 80GB")
    assert out == {**ANNOTATIONS, "summary": "qwen3-32b on A100 80GB in prod", "long": "x" * 300}
    assert common_labels.render_annotations(None, model_name="m", namespace="n", gpu_type=None) == {}


def test_validate_annotations_takes_free_text_but_known_placeholders_only():
    rules = {**ANNOTATIONS, "example.com/runbook": "https://wiki/x?a=1&b=2", "empty": ""}
    assert common_labels.validate_annotation_rules(rules) == rules
    with pytest.raises(ValueError, match=r"unknown placeholder \{\{cluster\}\}"):
        common_labels.validate_annotation_rules({"c": "{{cluster}}"})


@pytest.mark.parametrize("key", ["llm-ops/x", "llm-d.ai/x", "deployment.kubernetes.io/revision"])
def test_validate_annotations_rejects_keys_the_portal_or_k8s_owns(key):
    with pytest.raises(ValueError, match="managed by the portal or Kubernetes"):
        common_labels.validate_annotation_rules({key: "x"})


@pytest.mark.parametrize("key", ["", "a b", "a/b/c", "Example.com/x", "x" * 64])
def test_validate_annotations_rejects_keys_k8s_refuses(key):
    with pytest.raises(ValueError, match="annotation key"):
        common_labels.validate_annotation_rules({key: "x"})


# ─── manifests ────────────────────────────────────────────────────────────────


def test_aggregated_pods_and_deployment_carry_the_labels():
    dep = _row(gpu_type="h100", common_labels=RULES)
    deployment, service, ingress = build_all(dep)
    expected = {"team": "ml-platform", "app": "llama", "env": "ns", "gpu": "h100"}
    pod = deployment["spec"]["template"]["metadata"]["labels"]
    assert pod == {**expected, **pod_labels(dep)}
    assert deployment["metadata"]["labels"] == {**expected, LABEL_OWNER: OWNER, LABEL_MODEL: "llama"}
    # selectors are immutable: never there
    assert deployment["spec"]["selector"]["matchLabels"] == {LABEL_OWNER: OWNER, LABEL_MODEL: "llama"}
    assert "team" not in service["metadata"]["labels"] and "team" not in ingress["metadata"]["labels"]


def test_a_deployment_without_a_copy_of_the_rules_gets_none():
    dep = _row(common_labels=None)
    pod = build_all(dep)[0]["spec"]["template"]["metadata"]["labels"]
    assert pod == pod_labels(dep)


def test_pd_pools_fill_their_own_gpu_type_and_pool_labels_win():
    cfg = _pd_dep().pd_config
    cfg = {
        **cfg,
        "prefill": {**cfg["prefill"], "gpu_type": "b200", "pd_extras": _extras("prefill", labels={"team": "pool"})},
    }
    dep = _pd_dep(pd_config=cfg, common_labels={"team": "ml", "gpu": "{{gpu_type}}", "app": "{{model_name}}"})
    prefill, decode = [m for m in build_all(dep) if m["kind"] == "Deployment"]
    p, d = (m["spec"]["template"]["metadata"]["labels"] for m in (prefill, decode))
    assert (p["gpu"], d["gpu"]) == ("b200", "h200")
    assert (p["team"], d["team"]) == ("pool", "ml")  # the pool's own P/D label wins over the common one
    assert p["app"] == d["app"] == "glm"
    assert decode["metadata"]["labels"]["team"] == "ml"
    assert "team" not in decode["spec"]["selector"]["matchLabels"]


def test_router_links_see_the_common_labels():
    dep = _row(status="Ready", gpu_type="h100", common_labels={"app": "{{model_name}}"})
    assert portal_server(dep)["labels"]["app"] == "llama"


def test_aggregated_pods_and_deployment_carry_the_annotations():
    dep = _row(gpu_type="h100", common_annotations={"owner": "ML Platform", "summary": "{{model_name}}@{{gpu_type}}"})
    deployment, service, ingress = build_all(dep)
    expected = {"owner": "ML Platform", "summary": "llama@h100"}
    assert deployment["spec"]["template"]["metadata"]["annotations"] == expected
    assert deployment["metadata"]["annotations"] == expected
    for other in (service, ingress):
        assert "owner" not in (other["metadata"].get("annotations") or {})


def test_without_annotation_rules_the_manifests_have_no_annotations():
    """A deployment without a copy renders as before, so an update does not restart its pods."""
    deployment = build_all(_row(common_annotations=None))[0]
    assert "annotations" not in deployment["metadata"]
    assert "annotations" not in deployment["spec"]["template"]["metadata"]


def test_pd_pools_get_the_annotations_and_pool_annotations_win():
    cfg = _pd_dep().pd_config
    extras = _extras("prefill", annotations={"owner": "pool"})
    cfg = {**cfg, "prefill": {**cfg["prefill"], "gpu_type": "b200", "pd_extras": extras}}
    dep = _pd_dep(pd_config=cfg, common_annotations={"owner": "ml", "gpu": "{{gpu_type}}"})
    prefill, decode = [m for m in build_all(dep) if m["kind"] == "Deployment"]
    p, d = (m["spec"]["template"]["metadata"]["annotations"] for m in (prefill, decode))
    assert (p["owner"], p["gpu"]) == ("pool", "b200")  # the pool's own P/D annotation wins
    assert (d["owner"], d["gpu"]) == ("ml", "h200")
    assert decode["metadata"]["annotations"] == {"owner": "ml", "gpu": "h200"}


# ─── settings API ─────────────────────────────────────────────────────────────


def _settings_db(mock_db, saved: dict):
    """``mock_db`` reading portal settings rows from ``saved`` and writing them into it."""

    async def execute(_stmt, params=None):
        params = params or {}
        if "value" in params:
            saved[params["key"]] = params["value"]
            return _result()
        return _result(saved.get(params.get("key")))

    mock_db.execute = AsyncMock(side_effect=execute)
    return saved


async def test_settings_round_trip(client_for_user, super_user, mock_db):
    saved = _settings_db(mock_db, {common_labels.LABELS_KEY: json.dumps({"team": "ml"})})
    body = {"labels": {"team": "ml", "app": "{{model_name}}"}, "annotations": {"owner": "ML Platform <ml@x.com>"}}
    async with client_for_user(super_user) as client:
        got = await client.get("/api/settings/pod-labels")
        put = await client.put("/api/settings/pod-labels", json=body)
        again = await client.get("/api/settings/pod-labels")
    assert got.status_code == 200 and got.json() == {"labels": {"team": "ml"}, "annotations": {}}
    assert put.status_code == 200 and put.json() == body
    assert again.json() == body
    assert json.loads(saved[common_labels.ANNOTATIONS_KEY]) == body["annotations"]
    mock_db.commit.assert_awaited()


async def test_settings_refuse_invalid_rules_and_non_admins(client_for_user, super_user, regular_user, mock_db):
    _settings_db(mock_db, {})
    async with client_for_user(super_user) as client:
        bad = await client.put("/api/settings/pod-labels", json={"labels": {"llm-ops/team": "x"}})
        bad_annotation = await client.put(
            "/api/settings/pod-labels", json={"annotations": {"deployment.kubernetes.io/revision": "1"}}
        )
        empty = await client.get("/api/settings/pod-labels")
    assert bad.status_code == 400 and "managed by the portal" in bad.text
    assert bad_annotation.status_code == 400 and "deployment.kubernetes.io/revision" in bad_annotation.text
    assert empty.json() == {"labels": {}, "annotations": {}}
    async with client_for_user(regular_user) as client:
        assert (await client.put("/api/settings/pod-labels", json={"labels": {}})).status_code == 403


# ─── create / update / re-apply ───────────────────────────────────────────────


async def test_a_deployment_copies_what_settings_hold_now(mock_db):
    _settings_db(mock_db, {common_labels.LABELS_KEY: json.dumps({"team": "ml"})})
    assert await common_labels.load_deployment_copy(mock_db) == {
        "common_labels": {"team": "ml"},
        "common_annotations": None,
    }


def _rules(labels=None, annotations=None):
    copy = {"common_labels": labels or None, "common_annotations": annotations or None}
    return patch("app.api.model_deployments.load_deployment_copy", AsyncMock(return_value=copy))


def _pod_meta_sent(k8s):
    manifests = k8s.create_or_patch.await_args.args[1]
    return next(m for m in manifests if m["kind"] == "Deployment")["spec"]["template"]["metadata"]


def _pod_labels_sent(k8s):
    return _pod_meta_sent(k8s)["labels"]


def _pod_annotations_sent(k8s):
    return _pod_meta_sent(k8s).get("annotations") or {}


async def test_create_applies_and_keeps_the_current_rules(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    a, b, c, d = _patches(k8s)
    with a, b, c, d, _rules({"team": "ml", "app": "{{model_name}}"}, {"owner": "{{namespace}} team"}):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=DEPLOY)
    assert resp.status_code == 201, resp.text
    assert _pod_labels_sent(k8s)["team"] == "ml" and _pod_labels_sent(k8s)["app"] == "llama"
    assert _pod_annotations_sent(k8s) == {"owner": "prod team"}
    row = mock_db.add.call_args.args[0]
    assert row.common_labels == {"team": "ml", "app": "{{model_name}}"}
    assert row.common_annotations == {"owner": "{{namespace}} team"}


async def test_update_keeps_the_rules_the_deployment_was_applied_with(client_for_user, super_user, mock_db):
    """A scale must not pick up a settings change (that would roll every pod)."""
    dep = _row(replicas=2, common_labels={"team": "old"}, common_annotations={"owner": "old"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d, _rules({"team": "new"}, {"owner": "new"}):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{dep.id}", json={"replicas": 3})
    assert resp.status_code == 200, resp.text
    assert _pod_labels_sent(k8s)["team"] == "old" and dep.common_labels == {"team": "old"}
    assert _pod_annotations_sent(k8s) == {"owner": "old"} and dep.common_annotations == {"owner": "old"}


async def test_reapply_takes_the_current_rules(client_for_user, super_user, mock_db):
    dep = _row(common_labels=None)
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=_recipe(runtime=None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    ev = patch("app.api.model_deployments.reconcile_deployments._record_event", AsyncMock())
    with a, b, c, d, ev as record, _rules({"team": "ml"}, {"owner": "ml"}):
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{dep.id}/reapply", json={})
    assert resp.status_code == 200, resp.text
    assert _pod_labels_sent(k8s)["team"] == "ml" and dep.common_labels == {"team": "ml"}
    assert _pod_annotations_sent(k8s) == {"owner": "ml"} and dep.common_annotations == {"owner": "ml"}
    message = record.await_args.args[6]
    assert "common_labels" in message and "common_annotations" in message


async def test_recipe_diff_shows_a_label_change(client_for_user, super_user, mock_db):
    dep = _row(common_labels={"team": "old"}, common_annotations={"owner": "old"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.get = AsyncMock(return_value=_recipe())
    with _rules({"team": "new"}, {"owner": "new"}):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}/recipe-diff")
    assert resp.status_code == 200, resp.text
    deployment = next(m for m in resp.json()["manifests"] if m["kind"] == "Deployment")
    swapped = [(r["left"][1].strip(), r["right"][1].strip()) for r in deployment["rows"] if r["op"] == "replace"]
    assert ("team: old", "team: new") in swapped
    assert ("owner: old", "owner: new") in swapped


async def test_recipe_preview_shows_the_labels(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    recipe = {"name": "llama", "model_path": "/models/llama", "image": "vllm/vllm-openai:v0.9", "namespace": "prod"}
    copy = {"common_labels": {"env": "{{namespace}}"}, "common_annotations": {"owner": "{{namespace}} team"}}
    with patch("app.api.serving_recipes.load_deployment_copy", AsyncMock(return_value=copy)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes/preview", json=recipe)
    assert resp.status_code == 200, resp.text
    deployment = yaml.safe_load(resp.json()["manifests"][0]["yaml"])
    assert deployment["spec"]["template"]["metadata"]["labels"]["env"] == "prod"
    assert deployment["spec"]["template"]["metadata"]["annotations"] == {"owner": "prod team"}
