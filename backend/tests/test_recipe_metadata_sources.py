"""Public snapshots, scoped credentials and native metadata have distinct evidence boundaries."""

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.kv_metadata import metadata_job, uses_public_hub
from app.services.kv_metadata_reader import (
    MetadataReadError,
    apply_hub_env,
    inspect,
    native_mistral_config,
    safe_failure,
)
from app.services.recipe_throughput_evidence import (
    model_evidence,
    throughput_metadata,
    throughput_metadata_diagnostic,
)

ML4 = "mistralai/Mistral-Large-4-1T-A52B-NVFP4"
ML4_REV = "b8897f6fc5e86fadd6ebd0bddf08fe99591790db"
LLAMA = "meta-llama/Llama-3.1-8B"
LLAMA_REV = "d04e592bb4f6aa9cfee91e2e20afa771667e1d4b"


def test_public_source_is_scoped_to_checkpoint_and_anonymous_probe():
    flags = {"revision": LLAMA_REV}
    failed = {"error": "metadata unavailable"}
    detail = throughput_metadata_diagnostic(failed, LLAMA, flags, public_hub=True)
    assert detail == {"code": "hub_access_required", "source": "public_audit"}
    for model, actual_flags, public in [(LLAMA, {"revision": "other"}, True), (LLAMA, flags, False)]:
        assert (
            throughput_metadata_diagnostic(failed, model, actual_flags, public_hub=public)["code"]
            == "metadata_job_unavailable"
        )
    assert throughput_metadata_diagnostic({"config": {"x": 1}}, LLAMA, flags, public_hub=True) is None
    assert throughput_metadata_diagnostic({"pending": True}, LLAMA, flags, public_hub=True) is None
    for key, source in [("mount_unavailable", "mount"), ("offline_cache_missing", "offline_cache")]:
        assert throughput_metadata_diagnostic({"error_code": key}, LLAMA, flags, public_hub=False) == {
            "code": key,
            "source": source,
        }
    assert (
        throughput_metadata_diagnostic({"metadata_issue": "config_missing"}, LLAMA, flags, public_hub=True)["code"]
        == "config_missing"
    )


@pytest.mark.parametrize(
    ("model", "revision", "fmt", "filename"),
    [
        (
            "stepfun-ai/Step-3.5-Flash-INT8",
            "cd1b6ac3724fd6ec8b7e47091ae38c9ecb21144d",
            "step35_q8_0",
            "step3p5_flash_Q8_0-00001-of-00022.gguf",
        ),
        ("RecursiveSI/flint-nvfp4-gguf", "6ec43e19b565fc8d560e2d04985d7724f20673bf", "flint_nvfp4", "flint-nvfp4.gguf"),
    ],
)
def test_gguf_only_public_artifact_is_identified_when_hf_config_is_missing(model, revision, fmt, filename):
    assert throughput_metadata_diagnostic(
        {"metadata_issue": "config_missing"}, model, {"revision": revision}, public_hub=True
    ) == {"code": "gguf_selection_required", "source": "public_audit", "format": fmt}
    selected = model + "/" + filename
    assert throughput_metadata_diagnostic(
        {"metadata_issue": "gguf_layout_unsupported"}, selected, {"revision": revision}, public_hub=True
    ) == {"code": "gguf_layout_unsupported", "source": "public_audit", "format": fmt}


@pytest.mark.parametrize(
    ("repo", "revision"), [(LLAMA, LLAMA_REV), (LLAMA + "-Instruct", "0e9e39f249a16976918f6564b8830bc894c89659")]
)
def test_public_llama_architecture_cross_checks_the_gated_checkpoint_without_reading_config(repo, revision):
    errors = {"metadata_issue": "hub_access_required", "error": "hub_access_required"}
    flags = {"revision": revision}
    meta = throughput_metadata(errors, repo, flags, public_hub=True)
    cfg, fact = meta["config"], model_evidence(meta, repo)
    assert (
        "config_digest" not in meta
        and meta["architecture_source"] == fact["architecture_source"] == "public_model_spec"
    )
    assert fact["weight_bytes"] == 2 * fact["total_params"] == 16060522496
    assert fact["active_params"] == fact["total_params"] - 4096 * 128256
    assert (cfg["num_hidden_layers"], cfg["num_attention_heads"], cfg["num_key_value_heads"]) == (32, 32, 8)
    assert "arxiv.org" in fact["config_url"] and revision in fact["weight_source_url"]
    assert throughput_metadata(errors, repo, flags, public_hub=False) is errors
    assert throughput_metadata(errors, repo, {"revision": "main"}, public_hub=True) is errors


def test_native_mistral_sharing_and_shards_match_the_pinned_cohort():
    flags = {"revision": ML4_REV, "config-format": "mistral"}
    meta = throughput_metadata({}, ML4, flags, public_hub=True)
    cfg = meta["config"]
    assert cfg["index_topk"] == 2048 and cfg["index_head_dim"] == 128 and cfg["hidden_size"] == 6144
    assert len(set(cfg["kv_source_layer_ids"])) == 63
    assert cfg["indexer_types"].count("full") == 44
    evidence = model_evidence(meta, ML4)
    assert evidence["active_params"] == 49 * 10**9 and evidence["weight_bytes_range"] == [606842251056, 610042251312]
    assert model_evidence({**meta, "hub_origin": "mirror"}, ML4) is None
    assert throughput_metadata({}, ML4, flags, public_hub=False) == {}
    assert throughput_metadata({}, ML4, {**flags, "revision": "main"}, public_hub=True) == {}


def test_job_reads_scoped_env_from_and_explicit_ca_mounts(monkeypatch):
    engine = {
        "image": "engine",
        "args": ["--config-format=mistral"],
        "envFrom": [{"prefix": "HF_", "secretRef": {"name": "mirror"}}, {"configMapRef": {"name": "overrides"}}],
        "env": [
            {"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {"name": "own", "key": "token"}}},
            {"name": "REQUESTS_CA_BUNDLE", "value": "/ca/root.pem"},
        ],
        "volumeMounts": [{"name": n, "mountPath": "/" + n} for n in ("ca", "other", "models")],
    }
    pod = {
        "volumes": [
            {"name": "ca", "configMap": {"name": "ca"}},
            {"name": "other", "secret": {"secretName": "other"}},
            {"name": "models", "persistentVolumeClaim": {"claimName": "models"}},
        ]
    }
    assert not uses_public_hub(pod, engine)
    _, job, _, _ = metadata_job(SimpleNamespace(cluster_id="west", namespace="a"), pod, engine, "/models/q", None)
    helper = job["spec"]["template"]["spec"]["containers"][0]
    assert all(m["readOnly"] for m in helper["volumeMounts"])
    assert {m["name"] for m in helper["volumeMounts"]} == {"ca", "models"}
    assert set(helper["resources"]["limits"]) == {"memory", "cpu"}
    env = {e["name"]: e for e in helper["env"]}
    assert "envFrom" not in helper and "KV_HUB_0_HF_TOKEN" not in env
    assert env["KV_HUB_0_HF_ENDPOINT"]["valueFrom"]["secretKeyRef"]["key"] == "ENDPOINT"
    assert env["KV_HUB_1_HF_ENDPOINT"]["valueFrom"]["configMapKeyRef"]["key"] == "HF_ENDPOINT"
    assert env["HF_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "own"
    assert env["KV_CONFIG_FORMAT"]["value"] == "mistral"
    for i in (0, 1):
        monkeypatch.setenv(f"KV_HUB_{i}_HF_ENDPOINT", str(i))
    monkeypatch.setenv("HF_ENDPOINT", "image-default")
    monkeypatch.setenv("KV_HUB_9_OTHER", "unexpected")
    monkeypatch.delenv("OTHER", raising=False)
    apply_hub_env()
    import os

    assert os.environ["HF_ENDPOINT"] == "1" and "OTHER" not in os.environ


def test_authorized_hub_cache_pins_each_file_and_never_fetches_weights(tmp_path, monkeypatch):
    hub = ModuleType("huggingface_hub")
    errors = ModuleType("huggingface_hub.errors")
    errors.EntryNotFoundError = type("EntryNotFoundError", (Exception,), {})
    errors.LocalEntryNotFoundError = type("LocalEntryNotFoundError", (Exception,), {})
    rev = "d" * 40
    root = tmp_path / "snapshots" / rev
    root.mkdir(parents=True)
    (root / "config.json").write_text('{"model_type":"llama","hidden_size":512,"torch_dtype":"bfloat16"}')
    (root / "model.safetensors.index.json").write_text('{"metadata":{"total_size":8192}}')

    def cached(repo, name, revision):
        assert repo == "private/decoder" and revision == ("main" if name == "config.json" else rev)
        return str(root / name) if (root / name).exists() else None

    hub.try_to_load_from_cache = cached
    hub.hf_hub_download = Mock(side_effect=AssertionError("network access in offline mode"))
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_ENDPOINT", "https://private.example")
    result = inspect("private/decoder", "main")
    assert result["revision"] == rev and result["weight_bytes"] == 8192 and result["hub_origin"] == "mirror"
    hub.hf_hub_download.assert_not_called()
    hub.try_to_load_from_cache = Mock(return_value=None)
    with pytest.raises(MetadataReadError, match="offline_cache_missing"):
        inspect("private/decoder", rev)


def test_native_params_mounted_without_network(tmp_path):
    raw = {
        "dim": 512,
        "n_heads": 8,
        "n_kv_heads": 8,
        "n_layers": 2,
        "head_dim": 64,
        "kv_lora_rank": 256,
        "qk_rope_head_dim": 32,
        "max_position_embeddings": 4096,
        "dsa": {"topk": 64, "head_dim": 32, "sharing": "full,shared"},
        "kv_sharing": {"source_layers_str": "0,0"},
    }
    (tmp_path / "config.json").write_text('{"model_type":"other"}')
    (tmp_path / "params.json").write_text(json.dumps(raw))
    (tmp_path / "consolidated.safetensors.index.json").write_text('{"metadata":{"total_size":1024}}')
    result = inspect(str(tmp_path), None, "mistral")
    assert result["source"] == "mount" and result["weight_bytes"] == 1024 and result["config_digest"]
    assert result["config"]["kv_source_layer_ids"] == [0, 0]
    with pytest.raises(MetadataReadError, match="native_config_unsupported"):
        native_mistral_config({**raw, "dsa": {}})
    assert inspect(str(tmp_path), None)["config"] == {"model_type": "other"}
    denied = Exception("https://private.example/secret-token")
    denied.response = SimpleNamespace(status_code=401)
    assert safe_failure(denied) == {"error": "hub_access_required", "metadata_issue": "hub_access_required"}
