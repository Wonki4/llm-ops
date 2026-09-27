"""Selector-based linking between llm-d stacks and model servers."""

import types
import uuid

from app.services.llmd_links import external_server, link_stacks, portal_server, selector_matches, stacks_for_server
from app.services.llmd_manifests import stack_selector
from app.services.model_deployment_manifests import build_deployment, pod_labels


def _dep(**kw):
    base = dict(
        id=uuid.uuid4(),
        model_name="qwen",
        namespace="ns",
        image="img",
        replicas=1,
        gpu_count=1,
        gpu_resource_key="nvidia.com/gpu",
        cpu_request=None,
        cpu_limit=None,
        memory_request=None,
        memory_limit=None,
        node_selector=None,
        tolerations=None,
        pvc_name=None,
        pvc_mount_path=None,
        model_path="/m",
        vllm_extra_args=None,
        env=None,
        ingress_host="h",
        ingress_path="/",
        ingress_class="nginx",
        engine="vllm",
        engine_args=None,
        status="Ready",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _stack(values, **kw):
    base = dict(id=uuid.uuid4(), name="stack", values_snapshot=values)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_pod_template_carries_llmd_label_but_selector_does_not():
    dep = _dep()
    manifest = build_deployment(dep)
    assert manifest["spec"]["template"]["metadata"]["labels"]["llm-d.ai/model"] == "qwen"
    assert "llm-d.ai/model" not in manifest["spec"]["selector"]["matchLabels"]
    assert pod_labels(dep)["llm-ops/model-name"] == "qwen"


def test_stack_selector_reads_current_and_legacy_schemas():
    assert stack_selector({"router": {"modelServers": {"matchLabels": {"llm-d.ai/model": "Qwen3-32B"}}}}) == {
        "llm-d.ai/model": "Qwen3-32B"
    }
    legacy = {"inferenceExtension": {"endpointsServer": {"endpointSelector": "llm-ops/model-name=cpu-demo"}}}
    assert stack_selector(legacy) == {"llm-ops/model-name": "cpu-demo"}
    assert stack_selector({}) == {} and stack_selector(None) == {}


def test_selector_matches_is_subset_equality():
    assert selector_matches({"llm-d.ai/model": "a"}, {"llm-d.ai/model": "a", "app": "x"})
    assert not selector_matches({"llm-d.ai/model": "a", "app": "y"}, {"llm-d.ai/model": "a", "app": "x"})
    assert not selector_matches({}, {"llm-d.ai/model": "a"})  # empty selector links nothing


def test_link_by_label_not_by_name():
    portal = portal_server(_dep(model_name="cpu-demo"))
    ext = external_server(
        {
            "deployment_name": "vllm-qwen",
            "namespace": "ml",
            "status": "Ready",
            "labels": {"llm-d.ai/model": "Qwen3-32B", "app": "vllm"},
        },
        registered_model_name="qwen3-32b",
    )
    by_llmd_label = _stack({"router": {"modelServers": {"matchLabels": {"llm-d.ai/model": "Qwen3-32B"}}}}, name="ext")
    by_legacy = _stack(
        {"inferenceExtension": {"endpointsServer": {"endpointSelector": "llm-ops/model-name=cpu-demo"}}}, name="legacy"
    )
    by_name_only = _stack(
        {"router": {"modelServers": {"matchLabels": {"app": "other"}}}}, name="orphan", target_model_name="cpu-demo"
    )
    links = link_stacks([by_llmd_label, by_legacy, by_name_only], [portal, ext])
    assert [s["name"] for s in links[str(by_llmd_label.id)]["servers"]] == ["vllm-qwen"]
    assert [s["name"] for s in links[str(by_legacy.id)]["servers"]] == ["cpu-demo"]
    assert links[str(by_name_only.id)]["servers"] == []  # same target name, wrong selector → not linked
    assert stacks_for_server(portal, [by_llmd_label, by_legacy, by_name_only]) == [by_legacy]
