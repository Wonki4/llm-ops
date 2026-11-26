import pytest

from app.services.kv_capacity import Scenario, estimate

CONFIG = {
    "model_type": "glm_moe_dsa",
    "kv_lora_rank": 512,
    "qk_rope_head_dim": 64,
    "index_head_dim": 128,
    "index_topk": 2048,
    "num_hidden_layers": 8,
    "index_topk_freq": 4,
    "index_skip_topk_offset": 3,
    "dtype": "bfloat16",
}


def run(*args, cfg=CONFIG):
    return estimate({"config": cfg}, "vllm", ["--kv-cache-memory-bytes=1G", *args], scenario=Scenario(context=32768))


@pytest.mark.parametrize(("fmt", "latent"), [("fp8_e4m3", 576), ("fp8_ds_mla", 656), ("nvfp4_ds_mla", 352)])
def test_glm_sparse_payload_floor_and_capacity_ceiling(fmt, latent):
    result = run(f"--kv-cache-dtype={fmt}", "-tp", "8")
    width = 8 * latent + 4 * 132  # Only layers 0, 1, 2, 6 each store their own index.
    assert result["attention_bytes_per_token_per_gpu"] == width
    assert result["required_bytes_per_gpu"] == 32768 * width
    assert result["required_bytes_per_replica"] == 8 * 32768 * width
    assert result["capacity_tokens"] is None and result["payload_lower_bound"]
    assert result["capacity_range"] == [0, (1 << 30) // (16 * width) * 16]


def test_index_layer_precedence_pp_partition_and_fused_backend():
    cfg = {**CONFIG, "indexer_types": ["full", "shared"] * 4, "index_topk_pattern": "FFFFFFFF"}
    result = run(
        "--kv-cache-dtype=fp8",
        "--attention-backend=FLASHMLA_SPARSE",
        "--attention-config.indexer-kv-dtype=mxfp4",
        "-pp",
        "2",
        cfg=cfg,
    )
    assert result["attention_bytes_per_token_per_gpu"] == 4 * 656 + 2 * 68
    assert result["capacity_range"][1] > 0


def test_missing_old_index_metadata_can_only_bound_from_latents():
    cfg = {k: v for k, v in CONFIG.items() if k != "index_topk_freq"}
    result = run("--kv-cache-dtype=fp8_ds_mla", cfg=cfg)
    assert result["attention_bytes_per_token_per_gpu"] == 8 * 656
    assert result["payload_lower_bound"] and result["capacity_tokens"] is None


def test_unknown_geometry_or_block_override_cannot_claim_capacity():
    for r in (
        run(cfg={**CONFIG, "index_head_dim": 64}),
        run("--num-gpu-blocks-override=10"),
        run("--hf-overrides={}"),
        run(cfg={**CONFIG, "indexer_types": ["full"]}),
    ):
        assert r["capacity_tokens"] is None and not r.get("capacity_range") and r["missing"]
