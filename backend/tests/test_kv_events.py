"""Structured vLLM KV events (``runtime.kv_events``): schema, rendering, conflicts."""

import json
import types
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.model_deployments import CreateDeploymentRequest
from app.api.serving_recipes import RecipeBody
from app.services import pd_serving
from app.services.model_deployment_manifests import build_deployment, build_pd_deployments
from app.services.pd_serving import check_kv_events, validate_runtime
from app.services.serving_engines import serve_argv
from tests.test_litellm_registration import _patches
from tests.test_storage_recipes_deployments import _dep_row, _k8s, _refresh, _result

KV = {"endpoint": "tcp://*:5556", "replay_endpoint": "tcp://*:5559"}
POD_IP_ENV = {"name": "POD_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}}
KV_PORTS = [
    {"containerPort": 5556, "name": "kv-events", "protocol": "TCP"},
    {"containerPort": 5559, "name": "kv-replay", "protocol": "TCP"},
]


def _dep(**kw):
    base = dict(
        model_name="m", namespace="ns", image="img", replicas=1, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        storages=[], model_path="Qwen/Qwen3-32B", vllm_extra_args=None,
        env=None, ingress_host="h", ingress_path="/", ingress_class="nginx",
        engine="vllm", engine_args=None, runtime={"kv_events": dict(KV)},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _pd(**kw):
    return _dep(
        serving_mode="pd",
        pd_config={"prefill": {"replicas": 1}, "decode": {"replicas": 1}},
        probes=None,
        gpu_type=None,
        **kw,
    )


def _container(manifest: dict) -> dict:
    return manifest["spec"]["template"]["spec"]["containers"][0]


def _kv_config(args: list[str]) -> dict:
    """The JSON of the single ``--kv-events-config=`` arg."""
    found = [a for a in args if a.startswith("--kv-events-config")]
    assert len(found) == 1, args
    return json.loads(found[0].partition("=")[2])


def _by_role(manifests: list[dict]) -> dict[str, dict]:
    return {m["spec"]["template"]["metadata"]["labels"][pd_serving.LABEL_PD_ROLE]: _container(m) for m in manifests}


# ─── schema ───────────────────────────────────────────────────────────────────


def test_empty_kv_events_gets_the_default_endpoint():
    assert validate_runtime({"kv_events": {}}) == {"kv_events": {"endpoint": "tcp://*:5556"}}


def test_blank_kv_event_fields_are_dropped():
    out = validate_runtime({"kv_events": {"endpoint": " tcp://*:5556 ", "replay_endpoint": "", "topic": "  "}})
    assert out == {"kv_events": {"endpoint": "tcp://*:5556"}}


def test_null_kv_events_is_off():
    assert validate_runtime({"kv_events": None}) is None


@pytest.mark.parametrize(
    "kv",
    [
        {"endpoint": "5556"},
        {"endpoint": "tcp://"},
        {"endpoint": "tcp://*:5556 extra"},
        {"replay_endpoint": "*:5559"},
        {"buffer_steps": 0},
        {"hwm": -1},
        {"max_queue_size": 0},
        {"publisher": "null"},
    ],
)
def test_bad_kv_events_rejected(kv):
    with pytest.raises(ValidationError):
        validate_runtime({"kv_events": kv})


# ─── aggregated rendering ────────────────────────────────────────────────────


def test_kv_events_render_as_one_json_flag_with_auto_topic():
    c = _container(build_deployment(_dep()))
    assert _kv_config(c["args"]) == {
        "enable_kv_cache_events": True,
        "publisher": "zmq",
        "endpoint": "tcp://*:5556",
        "replay_endpoint": "tcp://*:5559",
        "topic": "kv@$(POD_IP):8000@Qwen/Qwen3-32B",
    }
    assert c["ports"] == [{"containerPort": 8000, "name": "http"}, *KV_PORTS]
    assert POD_IP_ENV in c["env"]


def test_auto_topic_follows_served_name_and_port():
    dep = _dep(engine_args={"served-model-name": "qwen3"}, vllm_extra_args=["--port=9000"])
    assert _kv_config(_container(build_deployment(dep))["args"])["topic"] == "kv@$(POD_IP):9000@qwen3"


def test_topic_override_and_advanced_values_render_verbatim():
    kv = {**KV, "topic": "kv@custom@m", "buffer_steps": 20000, "hwm": 0, "max_queue_size": 50000}
    c = _container(build_deployment(_dep(runtime={"kv_events": kv})))
    cfg = _kv_config(c["args"])
    assert cfg["topic"] == "kv@custom@m"
    assert (cfg["buffer_steps"], cfg["hwm"], cfg["max_queue_size"]) == (20000, 0, 50000)
    assert all(e["name"] != "POD_IP" for e in c["env"])  # nothing references $(POD_IP)


def test_existing_pod_ip_env_is_kept():
    c = _container(build_deployment(_dep(env={"POD_IP": "fieldRef:status.podIP", "A": "1"})))
    assert [e["name"] for e in c["env"]] == ["POD_IP", "A"]


def test_no_replay_endpoint_declares_only_the_events_port():
    c = _container(build_deployment(_dep(runtime={"kv_events": {"endpoint": "tcp://*:5556"}})))
    assert "replay_endpoint" not in _kv_config(c["args"])
    assert c["ports"][1:] == KV_PORTS[:1]


def test_without_kv_events_nothing_changes():
    c = _container(build_deployment(_dep(runtime=None)))
    assert not any(a.startswith("--kv-events-config") for a in c["args"])
    assert c["env"] == [] and c["ports"] == [{"containerPort": 8000, "name": "http"}]


def test_sglang_never_renders_kv_events():
    c = _container(build_deployment(_dep(engine="sglang")))
    assert not any(a.startswith("--kv-events-config") for a in c["args"])


def test_bench_argv_leaves_kv_events_out():
    # serve_argv runs under a shell, where $(POD_IP) would be a command substitution.
    assert not any("kv-events" in a for a in serve_argv(_dep()))


# ─── P/D rendering ────────────────────────────────────────────────────────────


def test_pd_pools_render_kv_events_at_the_router_port():
    pools = _by_role(build_pd_deployments(_pd(), sidecar_image="sc:v1"))
    # Decode vLLM listens on 8200, but the router reaches decode pods through the sidecar on 8000.
    for role in ("prefill", "decode"):
        c = pools[role]
        assert _kv_config(c["args"])["topic"] == "kv@$(POD_IP):8000@Qwen/Qwen3-32B"
        assert c["ports"][2:] == KV_PORTS
        assert POD_IP_ENV in c["env"]


def test_pd_pool_runtime_without_kv_events_turns_them_off_for_that_pool():
    dep = _pd()
    dep.pd_config["prefill"]["runtime"] = {"shm_size_gi": 8}
    pools = _by_role(build_pd_deployments(dep, sidecar_image="sc:v1"))
    assert not any(a.startswith("--kv-events-config") for a in pools["prefill"]["args"])
    assert _kv_config(pools["decode"]["args"])["endpoint"] == "tcp://*:5556"


# ─── conflicts ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kw",
    [
        {"vllm_extra_args": ['--kv-events-config={"enable_kv_cache_events":true}']},
        {"vllm_extra_args": ["--kv-events-config", "{}"]},
        {"vllm_extra_args": ["--kv_events_config={}"]},
        {"vllm_extra_args": ["--kv-events-config.topic=x"]},
        {"engine_args": {"kv-events-config": "{}"}},
    ],
)
def test_kv_events_and_the_raw_flag_conflict(kw):
    with pytest.raises(ValueError, match="--kv-events-config"):
        check_kv_events(_dep(**kw))


def test_raw_flag_alone_is_fine():
    check_kv_events(_dep(runtime=None, vllm_extra_args=["--kv-events-config={}"]))


def test_kv_events_on_sglang_rejected():
    with pytest.raises(ValueError, match="vLLM"):
        check_kv_events(_dep(engine="sglang"))


def test_pd_conflict_names_the_pool():
    dep = _pd()
    dep.pd_config["decode"]["vllm_extra_args"] = ["--kv-events-config={}"]
    with pytest.raises(ValueError, match="decode"):
        check_kv_events(dep)


def test_pd_pool_without_kv_events_may_keep_the_raw_flag():
    dep = _pd(runtime=None)
    dep.pd_config["decode"]["runtime"] = {"kv_events": dict(KV)}
    dep.pd_config["prefill"]["vllm_extra_args"] = ["--kv-events-config={}"]
    check_kv_events(dep)


# ─── API ──────────────────────────────────────────────────────────────────────


def test_recipe_body_rejects_the_conflict():
    with pytest.raises(ValidationError, match="--kv-events-config"):
        RecipeBody(
            name="r", model_path="/m", image="img",
            runtime={"kv_events": KV}, vllm_extra_args=["--kv-events-config={}"],
        )


def test_recipe_body_rejects_kv_events_on_sglang():
    with pytest.raises(ValidationError, match="vLLM"):
        RecipeBody(name="r", model_path="/m", image="img", engine="sglang", runtime={"kv_events": KV})


def test_create_request_rejects_the_conflict():
    with pytest.raises(ValidationError, match="--kv-events-config"):
        CreateDeploymentRequest(
            model_name="m", model_path="/m", ingress_host="h",
            runtime={"kv_events": KV}, engine_args={"kv-events-config": "{}"},
        )


async def test_update_rejects_the_conflict(client_for_user, super_user, mock_db):
    row = _dep_row(storages=[], runtime={"kv_events": dict(KV)})
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    a, b, c = _patches(k8s)
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(
                f"/api/model-deployments/{row.id}", json={"vllm_extra_args": ["--kv-events-config={}"]}
            )
    assert resp.status_code == 400, resp.text
    assert "--kv-events-config" in resp.json()["detail"]
    k8s.create_or_patch.assert_not_awaited()
