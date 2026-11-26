"""Only the actual GPU board, decoder, checkpoint and variant can supply timing assumptions."""

import pytest

from app.services.kv_metadata_reader import structural
from app.services.recipe_throughput import estimate_throughput
from app.services.recipe_throughput_evidence import gpu_evidence, model_evidence, throughput_metadata, workload_kind

KIMI_REV = "d9e99b0792a5e93b78ea9bea62a68167e36ddd9e"
V41_REV = "314113f8a7dadb388215396ba5fbee2c610a04d6"


def test_dense_non_sparse_per_device_hardware_and_different_h100_boards():
    sxm = gpu_evidence("H100")
    assert sxm["bf16_tflops"] == 989 and sxm["bandwidth_gbps"] == 3350 and sxm["variant"] == "H100 SXM"
    pcie = gpu_evidence("NVIDIA H100 PCIe")
    assert pcie["bf16_tflops"] == 756.5 and pcie["bandwidth_gbps"] == 2000
    assert gpu_evidence("GB300")["bandwidth_gbps"] == 8000
    assert gpu_evidence("DGX-STATION-GB300")["bandwidth_gbps"] == 7100
    assert gpu_evidence("ARC-PRO-B60")["fp8_tflops"] is None
    assert gpu_evidence("some-H100-test") is None


def test_published_active_counts_use_actual_model_and_exact_revision():
    result = model_evidence({"revision": KIMI_REV, "source": "hub"}, "moonshotai/Kimi-K3.1-Pro")
    assert (result["total_params"], result["active_params"]) == (2_800_000_000_000, 104_000_000_000)
    assert KIMI_REV in result["source_url"]
    for wrong in ({"source": "mount", "revision": KIMI_REV}, {"source": "hub", "revision": "main"}):
        assert model_evidence(wrong, "moonshotai/Kimi-K3.1-Pro") is None
    v41 = model_evidence({"revision": V41_REV, "source": "pinned_reference"}, "deepseek-ai/DeepSeek-V4.1-Flash")
    assert (v41["active_prefill_params"], v41["active_params"]) == (8_000_000_000, 16_000_000_000)


def test_variant_does_not_use_parameter_count_from_larger_model_in_group():
    model = "mistralai/Ministral-3-8B-Instruct-2512"
    evidence = model_evidence({"revision": "d2ff65a33fc5f72d77a65a2bc366feefd44ca320", "source": "hub"}, model)
    assert evidence["total_params"] == 8_000_000_000 and model in evidence["source_url"]


def test_pinned_shard_payload_range_yields_to_known_tensor_bytes():
    repo = "Qwen/Qwen3.6-35B-A3B-FP8"
    meta = {"source": "hub", "revision": "c653bba46dfa06e9658dcc0b0dc87fe4685f14a2"}
    bounded = model_evidence(meta, repo)
    assert bounded["weight_basis"] == "shard_files" and bounded["weight_bytes"] is None
    assert bounded["weight_bytes_range"] == [32_409_801_216, 37_463_662_160]
    assert meta["revision"] in bounded["weight_source_url"]
    measured = model_evidence({**meta, "weight_bytes": 36_000_000_000}, repo)
    assert measured["weight_bytes"] == 36_000_000_000 and "weight_bytes_range" not in measured


def test_mounted_qwen_moe_sizes_are_computed_from_config_without_using_the_name():
    config = structural({"model_type": "qwen3_moe", "hidden_size": 128, "num_attention_heads": 4,
                         "num_key_value_heads": 2, "num_hidden_layers": 5, "intermediate_size": 1024,
                         "vocab_size": 4096, "decoder_sparse_step": 2, "mlp_only_layers": [1], "num_experts": 10,
                         "num_experts_per_tok": 2, "moe_intermediate_size": 256, "tie_word_embeddings": False})
    out = model_evidence({"config": config, "source": "mount", "weight_bytes": 4_000_000}, "/docs/model")
    assert out["kind"] == "moe"
    assert out["source"] == "decoder_geometry"
    # Only layer 3 is sparse. Eight inactive experts and one separate embedding table are stored.
    assert out["total_params"] - out["active_params"] == 8 * 3 * 128 * 256 + 4096 * 128
    assert model_evidence({"config": {**config, "num_experts_per_tok": 11}}, "/model") is None


@pytest.mark.parametrize("architecture", ["LlamaForSequenceClassification", "Qwen3ForTokenClassification"])
def test_classifier_on_a_supported_decoder_is_not_a_generation_recipe(architecture):
    cfg = structural({"model_type": "qwen3", "dtype": "bfloat16", "architectures": [architecture]})
    data = {"config": cfg, "weight_bytes": 1 << 30}
    assert workload_kind(data, "/model") == "pooling" and model_evidence(data, "/model") is None


def test_quantized_dense_checkpoint_bytes_are_not_mistaken_for_parameter_count():
    meta = {"config": {"model_type": "llama", "torch_dtype": "float16"}, "weight_bytes": 1 << 30}
    assert model_evidence(meta, "/model")["active_params"] == 1 << 29
    assert model_evidence({**meta, "weight_format": "awq"}, "/model") is None


@pytest.mark.parametrize(("name", "rev", "layers", "active", "precision"), [
    ("mistralai/Mistral-Large-3-675B-Instruct-2512", "51330c9da67945183568346369a07be224e5e1b0",
     61, 39_000_000_000, "fp8"),
    ("RedHatAI/Mistral-Small-4-119B-2603-NVFP4", "a7b11eb3bdf01f38077f6704e96cc1d9b83a4cfc",
     36, 6_500_000_000, "nvfp4"),
])
def test_native_mistral_recipe_uses_text_backbone_and_verified_shards(name, rev, layers, active, precision):
    flags = {"revision": rev, "config-format": "mistral", "load-format": "mistral"}
    missing = {"error": "no config.json"}
    meta = throughput_metadata(missing, name, flags, public_hub=True)
    assert meta["config"]["num_hidden_layers"] == layers and meta["config"]["kv_lora_rank"] > 0
    assert meta["config_url"].endswith("/params.json") and meta["weight_format"] == precision
    fact = model_evidence(meta, name)
    assert fact["active_params"] == active and fact["weight_bytes_range"][0] > 0
    assert fact["weight_source_url"].endswith("/consolidated.safetensors.index.json")
    pool = {"model_path": name, "gpu_count": 8, "gpu_type": "H200", "tp": 8, "pp": 1,
            "engine": "vllm", "flags": flags, "metadata": meta, "gpu_vram_gb": 141}
    result = estimate_throughput([pool], {"input_tokens": 4096, "output_tokens": 512,
                                         "concurrency": 4, "shared_prefix_percent": 50})
    assert result["status"] == "estimated" and 0 < result["rpm"][0] < result["rpm"][1]
    assert missing == {"error": "no config.json"}  # The KV preview retains its own metadata.
    assert throughput_metadata(missing, name, flags, public_hub=False) is missing
    for override in ({"revision": "main"}, {"config-format": "hf"}):
        assert throughput_metadata(missing, name, {**flags, **override}, public_hub=True) is missing
    live = {"config": {"hidden_size": 512}, "source": "mount"}
    assert throughput_metadata(live, name, flags, public_hub=True) is live


def test_image_pipeline_native_metadata_cannot_be_timed_as_language_generation():
    name = "MiniMaxAI/MiniMax-H3-Image"
    flags = {"revision": "8ced60068623def5b74b4d10558117cb61d6482d"}
    meta = throughput_metadata({}, name, flags, public_hub=True)
    assert workload_kind(meta, name) == "diffusion" and model_evidence(meta, name) is None
    assert meta["config_url"].endswith("/model_index.json")
