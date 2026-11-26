import pytest

from app.services.deepseek_throughput import attention_flops, kv_read_bytes
from app.services.kv_capacity import Scenario, estimate
from app.services.kv_metadata import pinned_reference

CFG = {"model_type": "deepseek_v4", "num_hidden_layers": 3, "compress_ratios": [0, 4, 128, 0],
       "num_attention_heads": 2, "head_dim": 4, "index_n_heads": 3, "index_head_dim": 2,
       "sliding_window": 5, "index_topk": 7}


@pytest.mark.parametrize(("start", "end"), [(0, 1), (0, 4), (4, 28), (126, 130), (90_000, 90_007)])
def test_causal_csa_index_scores_hca_and_sliding_positions(start, end):
    expected = sum(
        4 * 2 * 4 * (3 * min(t, 5) + min(t // 4, 7) + t // 128) + 2 * 3 * 2 * (t // 4)
        for t in range(start + 1, end + 1)
    )
    assert attention_flops(CFG, start, end) == expected
    assert attention_flops(CFG, start, end) == attention_flops(CFG, 0, end) - attention_flops(CFG, 0, start)


def test_recipe_uses_rank_local_row_sizes_and_distinct_latent_and_index_read_counts():
    model = "deepseek-ai/DeepSeek-V4-Flash"
    revision = "60d8d70770c6776ff598c94bb586a859a38244f1"
    meta = pinned_reference({}, {}, model, revision)
    info = estimate(meta, "vllm", ["--kv-cache-dtype=fp8", "--tensor-parallel-size=8", "--block-size=256",
                    "--attention_config.indexer_kv_dtype=mxfp4"], gpu_type="B200", scenario=Scenario(101000, 8, 90000))
    ctx = 100000
    cfg = meta["config"]
    rows = info["components"]
    assert cfg["compress_ratios"][:43].count(4) == 21 and cfg["compress_ratios"][:43].count(128) == 20
    for kind, expected in {
        "Sliding-window MLA": 43 * 128 * 584, "Compressed MLA (1/4)": 21 * 512 * 584,
        "Compressed index (1/4)": 21 * (ctx // 4) * 68, "Compressed MLA (1/128)": 20 * (ctx // 128) * 584,
    }.items():
        row = next(row for row in rows if row["kind"] == kind)
        assert kv_read_bytes(cfg, [row], ctx, 8) == expected * 8
    assert attention_flops({**cfg, "model_type": "deepseek_v41_text"}, 0, ctx) is None
