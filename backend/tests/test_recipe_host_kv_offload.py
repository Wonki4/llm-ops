import json

import pytest

from app.services.kv_capacity import final_flags
from app.services.kv_host_offload import host_kv_offload

GIB = 1 << 30


def connector(name, **options):
    return {"kv_connector": name, "kv_role": "kv_both", "kv_connector_extra_config": options}


def vllm(config, *args, env=None):
    flags = final_flags(["--kv-transfer-config", json.dumps(config), *args])
    return host_kv_offload("vllm", flags, env=env)


def test_native_budget_already_covers_tp_pp_workers():
    result = vllm({}, "--kv-offloading-size=3.25", "-tp", "8", "-pp", "2")
    assert result == {"source": "OffloadingConnector", "scope": "all_workers", "configured_bytes": 13 * GIB // 4}


def test_pd_multiconnector_cpu_tier_and_dotted_input():
    cpu = connector("OffloadingConnector", cpu_bytes_to_use=GIB)
    nixl = connector("NixlConnector")
    assert vllm(connector("MultiConnector", connectors=[nixl, cpu])) == vllm(cpu)
    assert vllm(nixl) is None
    assert vllm(connector("MultiConnector", connectors=[cpu, cpu]))["scope"] == "unknown"
    assert host_kv_offload("vllm", final_flags([
        "--kv-transfer-config.kv_connector", "OffloadingConnector",
        "--kv-transfer-config.kv_connector_extra_config.cpu_bytes_to_use", str(GIB),
    ])) == vllm(cpu)


def test_simple_per_rank_limit_wins_and_disk_has_no_host_tier():
    simple = connector("SimpleCPUOffloadConnector", cpu_bytes_to_use=5 * GIB, cpu_bytes_to_use_per_rank=GIB)
    assert vllm(simple, "-tp", "2") == {
        "source": "SimpleCPUOffloadConnector", "scope": "per_worker", "configured_bytes": GIB,
    }
    simple["kv_connector_extra_config"]["kv_offload_backend"] = "disk"
    assert vllm(simple) is None


def test_native_shorthand_overrides_connector_but_preserves_simple_extra_options():
    config = connector("NixlConnector", cpu_bytes_to_use_per_rank=GIB)
    args = ["--kv-offloading-size=8"]
    assert vllm(config, *args, env={"VLLM_USE_SIMPLE_KV_OFFLOAD": "1"})["configured_bytes"] == GIB
    assert vllm(config, *args)["configured_bytes"] == 8 * GIB
    unknown_env = {"VLLM_USE_SIMPLE_KV_OFFLOAD": None}
    assert vllm(config, *args, env=unknown_env)["scope"] == "unknown"
    assert vllm({}, *args, env=unknown_env)["configured_bytes"] == 8 * GIB


def test_lmcache_depends_on_image_and_server_configuration():
    assert vllm({}, "--kv-offloading-size=16", "--kv-offloading-backend=lmcache") == {
        "source": "LMCache", "scope": "unknown", "configured_bytes": None,
    }


def test_zero_shorthand_is_distinct_from_an_absent_host_option():
    assert vllm({}, "--kv-offloading-size=0")["configured_bytes"] == 0
    assert vllm({}) is None
    assert vllm(connector("LMCacheMPConnector"))["scope"] == "external"


def test_swap_and_weights_do_not_create_a_host_prefix_store():
    assert host_kv_offload("vllm", final_flags(["--swap-space=4", "--cpu-offload-gb=7"])) is None


@pytest.mark.parametrize("value", [-1, {}, None, True, "NaN", "Infinity", "100e1000000", "0.5"])
def test_bad_connector_budget_remains_unknown(value):
    assert vllm(connector("OffloadingConnector", cpu_bytes_to_use=value))["configured_bytes"] is None


def test_sglang_hicache_units_and_ratio_override():
    flags = {"hicache-size": "20", "hicache-ratio": "3.5"}
    assert host_kv_offload("sglang", flags) is None
    flags["enable-hierarchical-cache"] = "true"
    assert host_kv_offload("sglang", flags) == {
        "source": "HiCache", "scope": "per_worker", "configured_bytes": 20 * 10**9,
    }
    flags["hicache-size"] = "0"
    assert host_kv_offload("sglang", flags) == {
        "source": "HiCache", "scope": "per_worker", "configured_bytes": None, "device_pool_ratio": 3.5,
    }
