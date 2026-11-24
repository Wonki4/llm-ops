"""CPU-only asset check, embedded in a Job using the candidate's own image/mounts.

No package installation at runtime. Hash all model shards, not just a path or tag:
a restored candidate must see the same bytes before it can receive GPU resources.
"""

import hashlib
import json
import os
from pathlib import Path


def inspect_assets(config):
    from transformers import AutoTokenizer

    if config.get("kind") == "accuracy":
        from lm_eval.tasks import TaskManager, get_task_dict

        if not Path("/app/entrypoint.py").is_file() or not Path("/app/accuracy_model.py").is_file():
            raise ValueError("Use the portal accuracy runner image built from bench/Dockerfile")
        tasks = config["tasks"]
        if isinstance(tasks, str):
            tasks = [t.strip() for t in tasks.split(",")]
        loaded = get_task_dict(tasks, task_manager=TaskManager())
        fingerprints = {}
        for name, task in loaded.items():
            if isinstance(task, tuple):
                task = task[-1]
            dataset = getattr(task, "dataset", None)
            if dataset:
                fingerprints[name] = {k: getattr(v, "_fingerprint", None) for k, v in dataset.items()}
        if not fingerprints or any(not value for splits in fingerprints.values() for value in splits.values()):
            raise ValueError("Evaluation datasets are unavailable")
        encoded = json.dumps(fingerprints, sort_keys=True).encode()
        return {"weights_sha256": hashlib.sha256(encoded).hexdigest(), "datasets": fingerprints}

    model = config["model_path"]
    offline = config.get("airgap", False)
    revision = config.get("revision")
    if Path(model).is_dir():
        root = Path(model)
    else:
        if model.startswith("/"):
            raise ValueError("Model directory is missing from the selected storage binding")
        from huggingface_hub import snapshot_download

        root = Path(snapshot_download(model, revision=revision, local_files_only=offline))
        if root.parent.name == "snapshots":
            revision = root.name
    if not (root / "config.json").is_file():
        raise ValueError("Model config.json is unavailable")
    shards = set(root.glob("*.safetensors")) | set(root.glob("pytorch_model*.bin"))
    for index in root.glob("*.index.json"):
        document = json.loads(index.read_text())
        for relative in (document.get("weight_map") or {}).values():
            shard = root / relative
            if not shard.is_file():
                raise ValueError(f"Model shard is missing: {relative}")
            shards.add(shard)
    if not shards:
        raise ValueError("No supported model weight shards found")
    files = shards | {
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in (".json", ".model", ".tiktoken", ".py", ".jinja", ".txt")
    }
    fingerprint = hashlib.sha256()
    config_digest = hashlib.sha256()
    total = 0
    for path in sorted(files):
        fingerprint.update(str(path.relative_to(root)).encode())
        with path.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                fingerprint.update(chunk)
                if path == root / "config.json":
                    config_digest.update(chunk)
                total += len(chunk)
    server_path = config.get("server_tokenizer") or str(root)
    tokenizer_revision = config.get("tokenizer_revision")
    if config.get("server_tokenizer") and not Path(server_path).is_dir():
        from huggingface_hub import snapshot_download

        server_path = snapshot_download(server_path, revision=tokenizer_revision, local_files_only=offline)
        if Path(server_path).parent.name == "snapshots":
            tokenizer_revision = Path(server_path).name

    def load_tokenizer(path):
        return AutoTokenizer.from_pretrained(
            path,
            local_files_only=offline,
            trust_remote_code=config.get("trust_remote_code", False),
        )

    def tokenizer_digest(tokenizer):
        return hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()

    server_tokenizer = load_tokenizer(server_path)
    tokenizer_path = config.get("tokenizer") or server_path
    tokenizer = load_tokenizer(tokenizer_path)
    if not tokenizer.encode("Recipe validation readiness check"):
        raise ValueError("Tokenizer returned no tokens")
    # Dependencies required by the portal streaming runner, without requiring a GPU.
    import aiohttp  # noqa: F401

    return {
        "weights_sha256": fingerprint.hexdigest(),
        "config_digest": config_digest.hexdigest(),
        "tokenizer_sha256": tokenizer_digest(tokenizer),
        "server_tokenizer_sha256": tokenizer_digest(server_tokenizer),
        "bytes": total,
        "shards": len(shards),
        "revision": revision,
        "tokenizer_revision": tokenizer_revision,
        "tokenizer": tokenizer_path,
        "server_tokenizer": server_path,
        "offline": offline,
    }


def main():
    config = json.loads(os.environ["VALIDATION_ASSETS"])
    try:
        result = inspect_assets(config)
    except Exception as exc:
        # Library messages can contain access tokens/URLs; return only the class and
        # our explicit asset errors. Full logs are scrubbed again by the backend.
        message = str(exc) if isinstance(exc, ValueError) and type(exc) is ValueError else type(exc).__name__
        print("<<<ASSETS>>>" + json.dumps({"error": message[:500]}), flush=True)
        raise SystemExit(1)
    print("<<<ASSETS>>>" + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
