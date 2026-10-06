"""Prefill/decode disaggregated serving: config schema, per-role merge, manifests.

A recipe/deployment with ``serving_mode == "pd"`` keeps a shared base (image,
model, storage, probes, runtime, placement) and carries a ``pd_config`` with
one complete config per role (replicas, GPUs, CPU/memory, engine args, extra
args, env) plus the KV plumbing and the router block. Role values win over the
base's, extra args are appended and env is merged, so older rows that only
stored per-role *overrides* keep rendering the same. This module turns that
into the two K8s Deployments (+ one Service each) the way the llm-d P/D guide
does:

* prefill: vLLM on 8000, ``kv_role=kv_producer``;
* decode: vLLM on 8200 behind the ``llm-d-router-disagg-sidecar`` native
  sidecar on 8000 (the router targets decode pods on 8000; the sidecar calls
  the prefill pod the EPP chose, then the local vLLM), ``kv_role=kv_consumer``;
* both: ``--kv-transfer-config`` (NixlConnector) rendered by the portal,
  ``VLLM_NIXL_SIDE_CHANNEL_HOST`` from the pod IP, the NIXL port exposed.

Pure functions, no I/O. vLLM only in v1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.serving_engines import SERVING_PORT, engine_of, render_engine_args, validate_engine_args

Role = Literal["prefill", "decode"]
ServingMode = Literal["aggregated", "pd"]
ROLES: tuple[Role, ...] = ("prefill", "decode")

PREFILL_PORT = SERVING_PORT  # 8000
DECODE_VLLM_PORT = 8200  # the sidecar owns 8000 on decode pods
SIDECAR_PORT = SERVING_PORT
DEFAULT_NIXL_PORT = 5600
KV_CONNECTOR = "NixlConnector"
DEFAULT_SIDECAR_IMAGE = "ghcr.io/llm-d/llm-d-router-disagg-sidecar:main"
SIDECAR_ARGS = ["--port=8000", "--kv-connector=nixlv2", "--zap-log-level=1", "--secure-proxy=false"]

LABEL_ROLE = "llm-d.ai/role"  # llm-d's prefill/decode marker (EPP filters on it)
LABEL_PD_ROLE = "llm-ops/pd-role"  # selector label so the two Deployments never overlap

# Flags the portal owns in P/D mode; a recipe that sets them would fight the plumbing.
FORBIDDEN_FLAGS = ("--kv-transfer-config", "--port")

DEFAULT_PEAK_PREFILL_THROUGHPUT = 33821  # llm-d guide value (gpt-oss-120b on 8× H200 TP=1)
DEFAULT_PREFIX_TOKENS_TO_MATCH = 131072


# ─── schema ───────────────────────────────────────────────────────────────────


class RoleOverride(BaseModel):
    """One pool's config. ``None`` fields fall back to the recipe base."""

    replicas: int = Field(1, ge=0)
    gpu_count: int | None = Field(None, ge=0)
    gpu_type: str | None = None
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    engine_args: dict[str, str | int | float | bool] | None = None
    vllm_extra_args: list[str] | None = None
    env: dict[str, str] | None = None

    @field_validator("gpu_type", "cpu_request", "cpu_limit", "memory_request", "memory_limit")
    @classmethod
    def _blank_is_none(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None

    @field_validator("engine_args")
    @classmethod
    def _engine_args(cls, v):
        return validate_engine_args(v)

    @field_validator("vllm_extra_args")
    @classmethod
    def _no_forbidden(cls, v):
        _reject_forbidden(v)
        return v


class PdRouter(BaseModel):
    """The llm-d router the deployment creates: EPP scheduler tuning plus the
    stack-level knobs the operator would otherwise set on the llm-d form."""

    peak_prefill_throughput: int = Field(DEFAULT_PEAK_PREFILL_THROUGHPUT, ge=1)
    prefix_tokens_to_match: int = Field(DEFAULT_PREFIX_TOKENS_TO_MATCH, ge=1)
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None
    epp_replicas: int | None = Field(None, ge=1)
    ingress_class: str | None = None

    @field_validator("epp_registry", "epp_repository", "epp_tag", "ingress_class")
    @classmethod
    def _blank_is_none(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None


class PdConfig(BaseModel):
    prefill: RoleOverride = RoleOverride()
    decode: RoleOverride = RoleOverride()
    nixl_port: int = Field(DEFAULT_NIXL_PORT, ge=1024, le=65535)
    kv_transfer_extra: dict[str, Any] = {}
    sidecar_image: str | None = None
    router: PdRouter = PdRouter()

    @field_validator("sidecar_image")
    @classmethod
    def _strip(cls, v):
        v = (v or "").strip()
        return v or None


class Runtime(BaseModel):
    shm_size_gi: int | None = Field(None, ge=1, le=4096)
    host_ipc: bool = False
    privileged: bool = False
    extra_resources: dict[str, str] = {}

    @field_validator("extra_resources")
    @classmethod
    def _resource_keys(cls, v):
        for key, val in (v or {}).items():
            if not key or "/" not in key or not str(val).strip():
                raise ValueError(f"extra_resources entries must be '<vendor>/<name>': '<quantity>' (got {key!r})")
        return v


def _reject_forbidden(args: list[str] | None) -> None:
    for a in args or []:
        for flag in FORBIDDEN_FLAGS:
            if a == flag or a.startswith(flag + "="):
                raise ValueError(f"{flag} is managed by the portal in P/D mode; remove it from the extra args")


def validate_pd_config(value: dict | None, *, engine: str, base_extra_args: list[str] | None) -> dict:
    """Validate and normalise ``pd_config`` for a P/D recipe/deployment."""
    if engine != "vllm":
        raise ValueError("serving_mode 'pd' requires engine 'vllm'")
    _reject_forbidden(base_extra_args)
    spec = PdConfig.model_validate(value or {})
    out = spec.model_dump(exclude_none=True)
    for role in ROLES:
        out.setdefault(role, {})
    return out


def validate_runtime(value: dict | None) -> dict | None:
    if value is None:
        return None
    out = Runtime.model_validate(value).model_dump(exclude_none=True)
    if not out.get("extra_resources"):
        out.pop("extra_resources", None)
    for k in ("host_ipc", "privileged"):
        if out.get(k) is False:
            out.pop(k, None)
    return out or None


# ─── per-role view ────────────────────────────────────────────────────────────


def kv_transfer_config(role: Role, extra: dict | None) -> str:
    """The JSON for ``--kv-transfer-config``; ``kv_connector``/``kv_role`` are ours."""
    body = dict(extra or {})
    body.pop("kv_connector", None)
    body.pop("kv_role", None)
    merged = {
        "kv_connector": KV_CONNECTOR,
        "kv_role": "kv_producer" if role == "prefill" else "kv_consumer",
        **body,
    }
    return json.dumps(merged, separators=(",", ":"))


@dataclass(frozen=True)
class RoleSpec:
    role: Role
    port: int  # the vLLM listen port
    replicas: int
    gpu_count: int
    gpu_type: str | None
    engine_args: dict
    extra_args: list[str]
    env: dict[str, str]
    nixl_port: int
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None

    def resources(self) -> dict:
        """The CPU/memory values to render (role, else base) for ``_resources``."""
        return {
            "cpu_request": self.cpu_request,
            "cpu_limit": self.cpu_limit,
            "memory_request": self.memory_request,
            "memory_limit": self.memory_limit,
        }


def pd_config_of(dep) -> dict:
    return dict(getattr(dep, "pd_config", None) or {})


def role_view(dep, role: Role) -> RoleSpec:
    """Merge the recipe base with the role's overrides (see spec Decision 3)."""
    cfg = pd_config_of(dep)
    ov = dict(cfg.get(role) or {})
    engine_args = {**(getattr(dep, "engine_args", None) or {}), **(ov.get("engine_args") or {})}
    extra_args = list(getattr(dep, "vllm_extra_args", None) or []) + list(ov.get("vllm_extra_args") or [])
    env = {**(getattr(dep, "env", None) or {}), **(ov.get("env") or {})}
    gpu_count = ov.get("gpu_count")
    if gpu_count is None:
        gpu_count = int(getattr(dep, "gpu_count", 0) or 0)

    def pick(key: str):
        return ov.get(key) or getattr(dep, key, None)

    return RoleSpec(
        role=role,
        port=PREFILL_PORT if role == "prefill" else DECODE_VLLM_PORT,
        replicas=int(ov.get("replicas", 1) if ov.get("replicas") is not None else 1),
        gpu_count=int(gpu_count),
        gpu_type=ov.get("gpu_type") or getattr(dep, "gpu_type", None),
        engine_args=engine_args,
        extra_args=extra_args,
        env=env,
        nixl_port=int(cfg.get("nixl_port") or DEFAULT_NIXL_PORT),
        cpu_request=pick("cpu_request"),
        cpu_limit=pick("cpu_limit"),
        memory_request=pick("memory_request"),
        memory_limit=pick("memory_limit"),
    )


def role_args(dep, spec: RoleSpec) -> list[str]:
    """``--model <path> --port <p> --kv-transfer-config <json> <engine args> <extra args>``."""
    cfg = pd_config_of(dep)
    return [
        "--model",
        dep.model_path,
        "--port",
        str(spec.port),
        "--kv-transfer-config",
        kv_transfer_config(spec.role, cfg.get("kv_transfer_extra")),
        *render_engine_args(spec.engine_args),
        *spec.extra_args,
    ]


def role_env(spec: RoleSpec) -> list[dict]:
    """Recipe env plus the NIXL side channel (host from the pod IP, port fixed)."""
    items = [{"name": k, "value": str(v)} for k, v in spec.env.items() if k not in ("VLLM_NIXL_SIDE_CHANNEL_HOST",)]
    items.append({"name": "VLLM_NIXL_SIDE_CHANNEL_HOST", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}})
    items = [i for i in items if i["name"] != "VLLM_NIXL_SIDE_CHANNEL_PORT"]
    items.append({"name": "VLLM_NIXL_SIDE_CHANNEL_PORT", "value": str(spec.nixl_port)})
    return items


def sidecar_container(image: str) -> dict:
    """llm-d routing sidecar as a native sidecar (init container that keeps running)."""
    return {
        "name": "routing-proxy",
        "image": image,
        "imagePullPolicy": "Always",
        "args": list(SIDECAR_ARGS),
        "ports": [{"containerPort": SIDECAR_PORT, "name": "sidecar", "protocol": "TCP"}],
        "resources": {},
        "restartPolicy": "Always",
        "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True},
    }


def sidecar_image_for(dep, default: str | None) -> str:
    return pd_config_of(dep).get("sidecar_image") or default or DEFAULT_SIDECAR_IMAGE


def is_pd(dep) -> bool:
    return getattr(dep, "serving_mode", None) == "pd"


def role_resource_names(dep) -> dict[str, str]:
    """``{prefill_deployment, decode_deployment, prefill_service, decode_service}``."""
    safe = dep.model_name.lower().replace("_", "-").replace(".", "-").replace("/", "-")
    out: dict[str, str] = {}
    for role in ROLES:
        out[f"{role}_deployment"] = f"{safe}-{role}-deployment"
        out[f"{role}_service"] = f"{safe}-{role}-service"
    return out


def pd_status_summary(status: dict | None) -> str | None:
    """``"D 2/2 · P 8/8"`` for list cells; None when nothing observed yet."""
    if not status:
        return None
    parts = []
    for role, letter in (("decode", "D"), ("prefill", "P")):
        r = status.get(role) or {}
        if r:
            parts.append(f"{letter} {r.get('ready', 0)}/{r.get('desired', 0)}")
    return " · ".join(parts) or None


def derive_pd_status(
    prefill: tuple[str, str | None] | None, decode: tuple[str, str | None] | None
) -> tuple[str, str | None]:
    """Overall status from the two roles' ``classify()`` results (None = Missing).

    Failed/Unhealthy of either role wins; Ready needs both Ready; Updating
    when both have at least one ready pod; otherwise Pending.
    """
    roles = {"prefill": prefill, "decode": decode}
    for name, res in roles.items():
        if res is None:
            return "Unhealthy", f"{name}: K8s Deployment not found"
    for level in ("Failed", "Unhealthy"):
        for name, (st, msg) in roles.items():
            if st == level:
                return level, f"{name}: {msg}" if msg else name
    statuses = {name: st for name, (st, _) in roles.items()}
    if all(st == "Ready" for st in statuses.values()):
        return "Ready", None
    if all(st in ("Ready", "Updating") for st in statuses.values()):
        detail = ", ".join(f"{n}: {m}" for n, (st, m) in roles.items() if st == "Updating" and m)
        return "Updating", detail or None
    if all(st == "Stopped" for st in statuses.values()):
        return "Stopped", "replicas set to 0"
    detail = ", ".join(f"{n}: {m}" for n, (st, m) in roles.items() if st != "Ready" and m)
    return "Pending", detail or "No ready pods yet"


def engine_guard(dep) -> None:
    if engine_of(dep) != "vllm":
        raise ValueError("serving_mode 'pd' requires engine 'vllm'")
