"""Causal attention work for DeepSeek-V4's sliding, CSA and HCA layers.

DeepSeek-V4-Flash@60d8d707, inference/model.py (Attention and Indexer):
CSA scores all completed 4-token groups and gathers up to index_topk latents.
HCA uses every completed 128-token group. The sliding window is additional.
"""


def _geometry(cfg):
    if cfg.get("model_type") != "deepseek_v4":
        return None
    required = ("num_hidden_layers", "sliding_window", "num_attention_heads", "head_dim",
                "index_n_heads", "index_head_dim", "index_topk")
    if not all(type(cfg.get(key)) is int and cfg[key] > 0 for key in required):
        return None
    count = cfg["num_hidden_layers"]
    ratios = cfg.get("compress_ratios")
    if not isinstance(ratios, list) or len(ratios) < count or any(r not in (0, 4, 128) for r in ratios[:count]):
        return None
    return ratios[:count]  # Draft layers have their own verification path.


def _pairs(length, ratio, limit=None):
    """Sum the number of visible rows for successive 1-based query positions."""
    end = length if limit is None else min(length, limit * ratio)
    groups, tail = divmod(end, ratio)
    pairs = ratio * groups * (groups - 1) // 2 + groups * (tail + 1)
    return pairs if limit is None else pairs + (length - end) * limit


def attention_flops(cfg, before, after):
    ratios = _geometry(cfg)
    if ratios is None:
        return None

    def pairs(ratio, limit=None):
        return _pairs(after, ratio, limit) - _pairs(before, ratio, limit)

    csa = ratios.count(4)
    attended = (len(ratios) * pairs(1, cfg["sliding_window"])
                + csa * pairs(4, cfg["index_topk"]) + ratios.count(128) * pairs(128))
    # QK and AV each perform a multiply-add. The CSA index scorer only computes QK.
    return (4 * cfg["num_attention_heads"] * cfg["head_dim"] * attended
            + 2 * cfg["index_n_heads"] * cfg["index_head_dim"] * csa * pairs(4))


def kv_read_bytes(cfg, rows, ctx, tp):
    if not rows or _geometry(cfg) is None:
        return None
    total = 0
    for row in rows:
        ratio = row.get("compression_ratio")
        if ratio:
            stored = int(ctx // ratio)
            if ratio == 4 and row["kind"].startswith("Compressed MLA"):
                stored = min(stored, cfg["index_topk"])
            # KV sizing records bytes per *input token*. Undo that averaging
            # before charging for the compressed rows actually read by a query.
            total += row["bytes_per_gpu"] * ratio * stored
        elif row["kind"].startswith("Compressed "):
            return None  # A layout produced without compression geometry.
        else:
            span = 2 if row["unit"] == "request" else min(ctx, row.get("window") or ctx)
            total += row["bytes_per_gpu"] * span
    return total * tp
