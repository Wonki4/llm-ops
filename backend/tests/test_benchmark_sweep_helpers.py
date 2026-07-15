"""Sweep grid expansion, serve-argv merge, queued-run promotion."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.services.benchmark_manifests import job_name_for
from app.services.benchmark_sweeps import expand_combos, merge_serve_argv, promote_queued_run


def test_expand_combos_row_major_first_variable_slowest():
    combos = expand_combos([
        {"flag": "--max-num-seqs", "values": [128, 256]},
        {"flag": "--gpu-memory-utilization", "values": [0.9, 0.95]},
    ])
    assert combos == [
        {"--max-num-seqs": 128, "--gpu-memory-utilization": 0.9},
        {"--max-num-seqs": 128, "--gpu-memory-utilization": 0.95},
        {"--max-num-seqs": 256, "--gpu-memory-utilization": 0.9},
        {"--max-num-seqs": 256, "--gpu-memory-utilization": 0.95},
    ]


def test_expand_single_variable():
    assert expand_combos([{"flag": "--a", "values": ["x"]}]) == [{"--a": "x"}]


def test_merge_replaces_space_and_equals_forms_and_appends_new():
    argv = ["vllm", "serve", "/m", "--max-num-seqs", "64", "--dtype=float16"]
    out = merge_serve_argv(argv, {"--max-num-seqs": 256, "--dtype": "bfloat16", "--kv-cache-dtype": "fp8"})
    assert out == [
        "vllm", "serve", "/m", "--max-num-seqs", "256", "--dtype=bfloat16",
        "--kv-cache-dtype", "fp8",
    ]
    assert argv[4] == "64"  # input untouched


async def test_promote_creates_job_and_clears_manifest():
    run = types.SimpleNamespace(
        id=uuid.uuid4(), k8s_namespace="bench", status="queued",
        k8s_job_name=None, queued_job_manifest={"kind": "Job"},
    )
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    await promote_queued_run(k8s, run)
    k8s.create_job.assert_awaited_once_with("bench", {"kind": "Job"})
    assert run.status == "pending"
    assert run.k8s_job_name == job_name_for(run.id)
    assert run.queued_job_manifest is None
