"""Reverse-engineer a serving recipe from a live K8s Deployment.

The manifest builder turns a recipe into a Deployment; this module walks the
other way so an operator can capture a hand-written (or externally managed)
vLLM/SGLang Deployment as a portal recipe. It works on the sanitized dict that
``K8sClient.read_deployment`` returns and never touches the cluster.

Everything the recipe schema cannot express, and every place the parser had to
guess, is reported as a warning ``{"code", "detail"}`` so the UI can show the
operator exactly what to double-check before saving. Codes are stable strings
the frontend maps to translated text.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from app.services.serving_engines import _ARG_KEY, DEFAULT_ENGINE, SERVING_PORT
from app.services.serving_probes import probe_from_k8s

# Program tokens that precede the engine's own flags. Anything here is dropped
# from the flag stream without a warning.
_PYTHON = re.compile(r"^(python[0-9.]*|python[0-9.]*\.exe)$")
_VLLM_MODULES = {"vllm.entrypoints.openai.api_server", "vllm.entrypoints.api_server"}
_SGLANG_MODULES = {"sglang.launch_server"}
_SHELLS = {"sh", "bash", "dash", "zsh", "/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash"}
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
_INT = re.compile(r"^-?\d+$")

# Flags the portal owns: it always rebinds the server to SERVING_PORT on all
# interfaces, so these are stripped from the imported args.
_PORTAL_FLAGS = {"port", "host"}

# Standard (non-extended) compute resources. Any other limit key is treated as
# the accelerator resource (nvidia.com/gpu, amd.com/gpu, habana.ai/gaudi, ...).
_STD_RESOURCES = {"cpu", "memory", "ephemeral-storage"}


def _warn(warnings: list[dict], code: str, detail: str | None = None) -> None:
    entry: dict[str, Any] = {"code": code}
    if detail is not None:
        entry["detail"] = detail
    warnings.append(entry)


# ─── Command line ────────────────────────────────────────────────────────────


def _unwrap_shell(command: list[str], args: list[str], warnings: list[dict]) -> list[str]:
    """Flatten command+args; unwrap ``sh -c "<script>"`` into its last command."""
    tokens = [str(t) for t in (*command, *args)]
    if not tokens or tokens[0] not in _SHELLS:
        return tokens
    # sh -c "<script>" [arg0 ...]  → the script is the first non-flag token after -c
    try:
        c_idx = tokens.index("-c")
    except ValueError:
        _warn(warnings, "command_unrecognized", " ".join(tokens))
        return []
    if c_idx + 1 >= len(tokens):
        _warn(warnings, "command_unrecognized", " ".join(tokens))
        return []
    script = tokens[c_idx + 1]
    # Keep the last pipeline segment: `pip install x && python -m ...` → the launch.
    segments = [s.strip() for s in re.split(r"&&|\|\||;|\n", script) if s.strip()]
    if len(segments) > 1:
        _warn(warnings, "shell_script_truncated", " && ".join(segments[:-1]))
    launch = segments[-1] if segments else ""
    try:
        out = shlex.split(launch)
    except ValueError:
        _warn(warnings, "command_unrecognized", launch)
        return []
    if out and out[0] == "exec":
        out = out[1:]
    while out and _ENV_ASSIGN.match(out[0]):
        _warn(warnings, "inline_env_dropped", out.pop(0))
    _warn(warnings, "shell_wrapper", launch)
    return out


def _detect_engine(tokens: list[str], image: str, warnings: list[dict]) -> tuple[str, list[str], str | None]:
    """Return (engine, flag_tokens, positional_model). Strips the program prefix.

    Recognised launches::

        [python -m] vllm.entrypoints.openai.api_server --model M ...
        vllm serve M ...   |   vllm serve --model M ...
        [python -m] sglang.launch_server --model-path M ...
        <image entrypoint> --model M ...                (vllm-openai image)
    """
    i = 0
    n = len(tokens)
    if i < n and _PYTHON.match(tokens[i]):
        i += 1
        if i < n and tokens[i] == "-m":
            i += 1
    if i < n and tokens[i] in _SGLANG_MODULES:
        return "sglang", tokens[i + 1 :], None
    if i < n and tokens[i] in _VLLM_MODULES:
        return "vllm", tokens[i + 1 :], None
    if i < n and tokens[i] == "vllm":
        rest = tokens[i + 1 :]
        if rest and rest[0] == "serve":
            rest = rest[1:]
            if rest and not rest[0].startswith("-"):
                return "vllm", rest[1:], rest[0]
            return "vllm", rest, None
        _warn(warnings, "command_unrecognized", " ".join(tokens))
        return "vllm", rest, None
    # No program: the image entrypoint runs the server. Engine from the image name.
    if i == 0 and (not tokens or tokens[0].startswith("-")):
        lowered = (image or "").lower()
        if "sglang" in lowered:
            return "sglang", tokens, None
        if "vllm" in lowered:
            return "vllm", tokens, None
        _warn(warnings, "engine_from_default", image)
        return DEFAULT_ENGINE, tokens, None
    # A program we do not know (custom wrapper script, uvicorn, ...): keep the
    # flags, guess the engine from the image, and tell the operator.
    lowered = (image or "").lower()
    engine = "sglang" if "sglang" in lowered else "vllm" if "vllm" in lowered else DEFAULT_ENGINE
    _warn(warnings, "command_unrecognized", " ".join(tokens[: i + 1]))
    return engine, tokens[i + 1 :], None


def _coerce(value: str) -> str | int | float:
    if _INT.match(value):
        try:
            return int(value)
        except ValueError:
            return value
    if _NUMBER.match(value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def parse_flags(tokens: list[str], warnings: list[dict]) -> tuple[dict[str, Any], list[str], list[str]]:
    """``["--tp-size", "4", "--trust-remote-code", "--dtype=bf16"]`` →
    (engine_args, extra_args, positionals).

    * ``--k v`` / ``--k=v`` → ``{k: v}`` with numeric strings coerced.
    * bare ``--k`` (next token is a flag or end) → ``{k: True}``.
    * A value may start with ``-`` only when it is a number (``--x -1``).
    * Keys are normalised to lower-case dashes (``--tensor_parallel_size`` →
      ``tensor-parallel-size``; argparse accepts both, the portal stores one).
    * Repeated key → last wins, warned.
    * Single-dash / malformed keys go to ``extra_args`` verbatim, warned.
    * Positional leftovers are returned separately for the caller to judge.
    """
    engine_args: dict[str, Any] = {}
    extra: list[str] = []
    positionals: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "--":
            positionals.extend(tokens[i + 1 :])
            break
        if tok.startswith("--") and len(tok) > 2:
            key, eq, inline = tok[2:].partition("=")
            norm = key.strip().lower().replace("_", "-")
            if norm != key:
                _warn(warnings, "flag_normalized", f"--{key} → --{norm}")
            if not _ARG_KEY.match(norm):
                # e.g. "--=x" or unicode — keep verbatim so nothing is lost.
                _warn(warnings, "flag_unparsed", tok)
                extra.append(tok)
                i += 1
                continue
            if eq:
                value: Any = _coerce(inline)
                i += 1
            elif i + 1 < n and (not tokens[i + 1].startswith("-") or _NUMBER.match(tokens[i + 1])):
                value = _coerce(tokens[i + 1])
                i += 2
            else:
                value = True
                i += 1
            if norm in engine_args:
                _warn(warnings, "duplicate_flag", f"--{norm}")
            engine_args[norm] = value
            continue
        if tok.startswith("-") and not _NUMBER.match(tok):
            # Short option: -tp 4. We cannot map it to a long name safely.
            if i + 1 < n and not tokens[i + 1].startswith("-"):
                extra.append(f"{tok} {tokens[i + 1]}")
                i += 2
            else:
                extra.append(tok)
                i += 1
            _warn(warnings, "short_flag", extra[-1])
            continue
        positionals.append(tok)
        i += 1
    return engine_args, extra, positionals


def _take_model_path(engine: str, engine_args: dict[str, Any], positional: str | None, warnings: list[dict]) -> str:
    """Remove the model flag from engine_args and return its value."""
    flag = "model-path" if engine == "sglang" else "model"
    other = "model" if engine == "sglang" else "model-path"
    value = engine_args.pop(flag, None)
    alt = engine_args.pop(other, None)
    if value is None and alt is not None:
        _warn(warnings, "flag_normalized", f"--{other} → --{flag}")
        value = alt
    if value is None and positional:
        value = positional
    elif positional and value is not None and str(value) != positional:
        _warn(warnings, "duplicate_flag", f"--{flag} (positional {positional!r} ignored)")
    if value is None or value is True:
        _warn(warnings, "model_path_missing")
        return ""
    return str(value)


# ─── Pod spec ────────────────────────────────────────────────────────────────


def _resources(container: dict, out: dict, warnings: list[dict]) -> None:
    res = container.get("resources") or {}
    limits = dict(res.get("limits") or {})
    requests = dict(res.get("requests") or {})
    extended = [k for k in limits if k not in _STD_RESOURCES and not k.startswith("hugepages-")]
    if extended:
        key = extended[0]
        out["gpu_resource_key"] = key
        try:
            out["gpu_count"] = int(str(limits[key]))
        except ValueError:
            _warn(warnings, "gpu_count_unparsed", f"{key}={limits[key]}")
            out["gpu_count"] = 0
        if len(extended) > 1:
            _warn(warnings, "multi_gpu_resource", ", ".join(extended[1:]))
    else:
        out["gpu_count"] = 0
    out["cpu_request"] = _str_or_none(requests.get("cpu"))
    out["cpu_limit"] = _str_or_none(limits.get("cpu"))
    out["memory_request"] = _str_or_none(requests.get("memory"))
    out["memory_limit"] = _str_or_none(limits.get("memory"))
    dropped = [k for k in requests if k not in _STD_RESOURCES and k not in extended]
    if dropped:
        _warn(warnings, "resource_request_dropped", ", ".join(dropped))


def _str_or_none(v: Any) -> str | None:
    return None if v is None else str(v)


def _env(container: dict, warnings: list[dict]) -> dict[str, str] | None:
    env: dict[str, str] = {}
    indirect: list[str] = []
    for item in container.get("env_raw") or container.get("env") or []:
        name = item.get("name")
        if not name:
            continue
        if item.get("valueFrom") is not None:
            indirect.append(name)
            continue
        env[name] = "" if item.get("value") is None else str(item["value"])
    if container.get("envFrom") or container.get("env_from"):
        _warn(warnings, "env_from_dropped")
    if indirect:
        _warn(warnings, "env_value_from", ", ".join(indirect))
    return env or None


def _volumes(spec: dict, container: dict, out: dict, warnings: list[dict]) -> None:
    mounts = {m.get("name"): m for m in (container.get("volume_mounts") or container.get("volumeMounts") or [])}
    pvc_pairs: list[tuple[str, str | None]] = []
    unsupported: list[str] = []
    for vol in spec.get("volumes") or []:
        name = vol.get("name")
        pvc = vol.get("persistentVolumeClaim")
        if pvc and pvc.get("claimName"):
            mount = mounts.get(name) or {}
            if mount.get("subPath") or mount.get("subPathExpr"):
                _warn(warnings, "subpath_dropped", f"{name}: {mount.get('subPath') or mount.get('subPathExpr')}")
            pvc_pairs.append((pvc["claimName"], mount.get("mountPath")))
            continue
        kinds = [k for k in vol if k != "name"]
        kind = kinds[0] if kinds else "unknown"
        if kind == "emptyDir" and (vol.get("emptyDir") or {}).get("medium") == "Memory":
            # /dev/shm for NCCL: the portal does not add it, so flag it loudly.
            _warn(warnings, "shm_volume_dropped", name)
        else:
            unsupported.append(f"{name} ({kind})")
    if pvc_pairs:
        claim, path = pvc_pairs[0]
        out["pvc_name"] = claim
        out["pvc_mount_path"] = path
        if path is None:
            _warn(warnings, "pvc_unmounted", claim)
        if len(pvc_pairs) > 1:
            _warn(warnings, "multi_pvc", ", ".join(c for c, _ in pvc_pairs[1:]))
    else:
        out["pvc_name"] = None
        out["pvc_mount_path"] = None
    if unsupported:
        _warn(warnings, "volume_unsupported", ", ".join(unsupported))


def _probes(container: dict, warnings: list[dict]) -> dict | None:
    out: dict = {}
    for kind in ("readiness", "liveness", "startup"):
        spec, code = probe_from_k8s(container.get(f"{kind}_probe"))
        if code:
            _warn(warnings, code, kind)
        if spec is not None:
            out[kind] = spec
    return out or None


# ─── Entry point ─────────────────────────────────────────────────────────────


def build_recipe_draft(spec: dict) -> dict:
    """Return ``{"draft": <RecipeBody-shaped dict>, "warnings": [...], "source": {...}}``.

    ``spec`` is ``K8sClient.read_deployment`` output. The draft's ``engine_args``
    holds every long flag found (the frontend relocates flags it has no field
    for into the free-text extra args); ``vllm_extra_args`` holds what could
    not be parsed into ``{key: value}`` form.
    """
    warnings: list[dict] = []
    container = spec.get("container") or {}
    image = container.get("image") or ""

    tokens = _unwrap_shell(list(container.get("command") or []), list(container.get("args") or []), warnings)
    engine, flag_tokens, positional_model = _detect_engine(tokens, image, warnings)
    engine_args, extra, positionals = parse_flags(flag_tokens, warnings)
    model_path = _take_model_path(engine, engine_args, positional_model, warnings)
    if positionals:
        _warn(warnings, "positional_dropped", " ".join(positionals))
    port = engine_args.pop("port", None)
    if port is not None and str(port) != str(SERVING_PORT):
        _warn(warnings, "port_changed", f"{port} → {SERVING_PORT}")
    for key in _PORTAL_FLAGS:
        engine_args.pop(key, None)  # the portal always rebinds host/port itself

    draft: dict[str, Any] = {
        "name": spec.get("name") or "",
        "description": None,
        "engine": engine,
        "image": image,
        "model_path": model_path,
        "engine_args": engine_args or None,
        "vllm_extra_args": extra or None,
        "gpu_resource_key": "nvidia.com/gpu",
    }
    _resources(container, draft, warnings)
    draft["env"] = _env(container, warnings)
    _volumes(spec, container, draft, warnings)
    draft["probes"] = _probes(container, warnings)
    draft["node_selector"] = dict(spec.get("node_selector") or {}) or None
    draft["tolerations"] = list(spec.get("tolerations") or []) or None

    if (spec.get("containers_count") or 1) > 1:
        _warn(warnings, "multi_container", str(spec["containers_count"]))
    if spec.get("init_containers"):
        _warn(warnings, "init_containers", ", ".join(spec["init_containers"]))
    if spec.get("affinity"):
        _warn(warnings, "affinity_dropped")
    ports = [p.get("containerPort") for p in (container.get("ports") or []) if p.get("containerPort")]
    if ports and SERVING_PORT not in ports and port is None:
        _warn(warnings, "port_changed", f"{ports[0]} → {SERVING_PORT}")
    if not image:
        _warn(warnings, "image_missing")

    return {
        "draft": draft,
        "warnings": warnings,
        "source": {
            "namespace": spec.get("namespace"),
            "deployment_name": spec.get("name"),
            "replicas": spec.get("replicas"),
            "command": list(container.get("command") or []),
            "args": list(container.get("args") or []),
        },
    }
