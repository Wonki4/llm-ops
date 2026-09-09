"""Serving engine catalogue: how vLLM and SGLang are launched, and how the
structured ``engine_args`` dict becomes CLI flags.

Pure functions, no I/O. The API layer validates ``engine`` / ``engine_args``
with these helpers; the manifest builder and benchmark serve-argv sites render
with them. The curated list of "main" flags lives in the frontend; the backend
only checks key shape and value type so new flags need no server change.
"""

from __future__ import annotations

import re
from typing import Literal

ServingEngine = Literal["vllm", "sglang"]
ENGINES: tuple[str, ...] = ("vllm", "sglang")
DEFAULT_ENGINE = "vllm"
SERVING_PORT = 8000

DEFAULT_IMAGES: dict[str, str] = {
    "vllm": "vllm/vllm-openai:latest",
    "sglang": "lmsysorg/sglang:latest",
}

# command=None means "use the image entrypoint" (the vllm-openai image starts
# the OpenAI server itself and takes --model). SGLang images have no serving
# entrypoint, so the command is explicit.
_LAUNCH: dict[str, dict] = {
    "vllm": {
        "command": None,
        "model_flag": "--model",
        "base_args": ["--port", str(SERVING_PORT)],
    },
    "sglang": {
        "command": ["python3", "-m", "sglang.launch_server"],
        "model_flag": "--model-path",
        "base_args": ["--host", "0.0.0.0", "--port", str(SERVING_PORT)],
    },
}

_ARG_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def default_image(engine: str) -> str:
    return DEFAULT_IMAGES.get(engine, DEFAULT_IMAGES[DEFAULT_ENGINE])


def engine_of(dep) -> str:
    """Engine name for a row. In-memory rows built without the column (benchmark
    clones, mocks in tests) read as None or a non-string and are treated as
    vLLM. The API validates ``engine`` on the way in, so persisted rows always
    hold a known value."""
    value = getattr(dep, "engine", None)
    return value if isinstance(value, str) and value in ENGINES else DEFAULT_ENGINE


def validate_engine_args(args: dict | None) -> dict | None:
    """Raise ValueError on a malformed key or non-scalar value; else return args."""
    for key, value in (args or {}).items():
        if not isinstance(key, str) or not _ARG_KEY.match(key):
            raise ValueError(f"engine_args key {key!r} must match {_ARG_KEY.pattern} (no leading dashes)")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"engine_args[{key!r}] must be a string, number or boolean")
    return args


def render_engine_args(args: dict | None) -> list[str]:
    """``{"tp-size": 4, "trust-remote-code": True}`` → ``["--tp-size", "4", "--trust-remote-code"]``.

    Keys are sorted so manifests are deterministic. ``True`` renders as a bare
    flag; ``False``, ``None`` and ``""`` are omitted; numbers use ``str()``.
    """
    out: list[str] = []
    for key in sorted(args or {}):
        value = args[key]
        if value is None or value is False or value == "":
            continue
        if value is True:
            out.append(f"--{key}")
        else:
            out.extend([f"--{key}", str(value)])
    return out


def engine_flags(dep) -> list[str]:
    """Structured args first, then the free-text extra args (so a power user can
    still override a structured value by repeating the flag)."""
    return render_engine_args(getattr(dep, "engine_args", None)) + list(dep.vllm_extra_args or [])


def container_launch(dep) -> tuple[list[str] | None, list[str]]:
    """(command, args) for the serving container of a K8s Deployment."""
    spec = _LAUNCH[engine_of(dep)]
    args = [spec["model_flag"], dep.model_path, *spec["base_args"], *engine_flags(dep)]
    return spec["command"], args


def serve_argv(dep, port: int = SERVING_PORT) -> list[str]:
    """Full argv that starts the server from a shell (self-serving bench jobs)."""
    flags = engine_flags(dep)
    if engine_of(dep) == "sglang":
        return [
            "python3", "-m", "sglang.launch_server", "--model-path", dep.model_path,
            "--host", "0.0.0.0", "--port", str(port), *flags,
        ]
    return ["vllm", "serve", dep.model_path, "--port", str(port), *flags]
