"""Serving engine catalogue: how vLLM and SGLang are launched, and how the
structured ``engine_args`` dict becomes CLI flags.

Pure functions, no I/O. The API layer validates ``engine`` / ``engine_args``
with these helpers; the manifest builder and benchmark serve-argv sites render
with them. The curated list of "main" flags lives in the frontend; the backend
only checks key shape and value type so new flags need no server change.
"""

from __future__ import annotations

import json
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
        "base_args": [],
    },
    "sglang": {
        "command": ["python3", "-m", "sglang.launch_server"],
        "model_flag": "--model-path",
        "base_args": ["--host", "0.0.0.0"],
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
    if (args or {}).get("port") not in (None, False, ""):
        parse_port(args["port"])
    return args


def split_port(flags: list[str]) -> tuple[list[str], str | None]:
    """``flags`` without any ``--port`` (``--port=N`` or ``--port N``), and the last value given."""
    rest: list[str] = []
    port: str | None = None
    i = 0
    while i < len(flags):
        a = flags[i]
        if a.startswith("--port="):
            port = a[len("--port=") :]
        elif a == "--port":
            port = flags[i + 1] if i + 1 < len(flags) else ""
            i += 1
        else:
            rest.append(a)
        i += 1
    return rest, port


def parse_port(value) -> int:
    text = str(value).strip()
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ValueError(f"--port must be a number between 1 and 65535, got {value!r}")
    return int(text)


def validate_port_flag(extra_args: list[str] | None) -> list[str] | None:
    """Raise ValueError when the extra args set an invalid ``--port``; else return them."""
    _, port = split_port(list(extra_args or []))
    if port is not None:
        parse_port(port)
    return extra_args


KV_EVENTS_FLAG = "--kv-events-config"
# Config key -> container port name for the ZMQ sockets the server binds on the pod.
_KV_EVENT_PORTS = {"endpoint": "kv-events", "replay_endpoint": "kv-replay"}
_TCP_ENDPOINT = re.compile(r"^tcp://(?P<host>.+):(?P<port>\d+)$")


def _kv_events_config(flags: list[str]) -> dict:
    """``--kv-events-config`` as a dict: the JSON form (``=``/space) and dotted keys, last wins."""
    cfg: dict = {}
    i = 0
    while i < len(flags):
        flag, eq, value = flags[i].partition("=")
        name = flag.replace("_", "-")
        if name == KV_EVENTS_FLAG or name.startswith(KV_EVENTS_FLAG + "."):
            if not eq and i + 1 < len(flags):
                value = flags[i + 1]
                i += 1
            if name == KV_EVENTS_FLAG:
                try:
                    parsed = json.loads(value)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    cfg.update(parsed)
            else:
                cfg[name[len(KV_EVENTS_FLAG) + 1 :].replace("-", "_")] = value
        i += 1
    return cfg


def kv_event_ports(args: list[str]) -> list[dict]:
    """Container ports for the KV-event sockets ``--kv-events-config`` binds on the pod.

    Mirrors vLLM: the publisher ``endpoint`` binds only on a wildcard host (``tcp://*:5556``),
    otherwise it connects out and needs no pod port; ``replay_endpoint`` always binds.
    Only tcp endpoints written in the config count (the engine's implicit default does not).
    """
    cfg = _kv_events_config(args)
    out: list[dict] = []
    for key, port_name in _KV_EVENT_PORTS.items():
        m = _TCP_ENDPOINT.match(str(cfg.get(key) or "").strip())
        if not m or (key == "endpoint" and "*" not in m["host"] and "::" not in m["host"]):
            continue
        port = int(m["port"])
        if 1 <= port <= 65535:
            out.append({"containerPort": port, "name": port_name, "protocol": "TCP"})
    return out


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


def serving_port(dep) -> int:
    """The port the server listens on: a ``--port`` in the engine/extra args, else SERVING_PORT.

    The container port, the probes and the Service's targetPort follow it.
    """
    _, port = split_port(engine_flags(dep))
    return SERVING_PORT if port is None else parse_port(port)


def container_launch(dep) -> tuple[list[str] | None, list[str]]:
    """(command, args) for the serving container of a K8s Deployment.

    ``--port`` renders once, after the model, whether it is the default or the user's.
    """
    spec = _LAUNCH[engine_of(dep)]
    flags, _ = split_port(engine_flags(dep))
    args = [spec["model_flag"], dep.model_path, *spec["base_args"], "--port", str(serving_port(dep)), *flags]
    return spec["command"], args


def serve_argv(dep, port: int = SERVING_PORT) -> list[str]:
    """Full argv that starts the server from a shell (self-serving bench jobs).

    ``port`` is where the bench client connects, so a ``--port`` in the args is dropped.
    """
    flags, _ = split_port(engine_flags(dep))
    if engine_of(dep) == "sglang":
        return [
            "python3", "-m", "sglang.launch_server", "--model-path", dep.model_path,
            "--host", "0.0.0.0", "--port", str(port), *flags,
        ]
    return ["vllm", "serve", dep.model_path, "--port", str(port), *flags]
