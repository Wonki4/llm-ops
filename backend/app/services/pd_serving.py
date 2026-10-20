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
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.serving_engines import SERVING_PORT, engine_of, render_engine_args, validate_engine_args
from app.services.serving_probes import validate_probes

Role = Literal["prefill", "decode"]
ServingMode = Literal["aggregated", "pd"]
# Who orchestrates the P/D pair (router, KV plumbing). Only llm-d today; another
# framework (e.g. Dynamo) would add a value here and branch on it at render/deploy.
PdFramework = Literal["llm-d"]
DEFAULT_PD_FRAMEWORK: PdFramework = "llm-d"
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
# What P/D settings (pd_extras) may not set: the per-deployment model label and the
# portal's selector labels.
RESERVED_LABELS = ("llm-d.ai/model",)
NIXL_HOST_ENV = "VLLM_NIXL_SIDE_CHANNEL_HOST"
# An env value ``fieldRef:<path>`` renders as valueFrom.fieldRef (e.g. the pod IP).
FIELD_REF_PREFIX = "fieldRef:"
# ``{{model_name}}`` (spaces inside allowed) in a P/D label/annotation value becomes the
# deployment's model name, K8s-safe — the same value on both pools.
MODEL_NAME_PLACEHOLDER = re.compile(r"\{\{\s*model_name\s*\}\}")
RESERVED_LABEL_PREFIX = "llm-ops/"

DEFAULT_PEAK_PREFILL_THROUGHPUT = 33821  # llm-d guide value (gpt-oss-120b on 8× H200 TP=1)
DEFAULT_PREFIX_TOKENS_TO_MATCH = 131072


# ─── schema ───────────────────────────────────────────────────────────────────


class Runtime(BaseModel):
    shm_size_gi: int | None = Field(None, ge=1, le=4096)
    host_ipc: bool = False
    privileged: bool = False
    extra_resources: dict[str, str] = {}

    @field_validator("extra_resources")
    @classmethod
    def _resource_keys(cls, v):
        return _resource_entries(v, "extra_resources")


def _resource_entries(v: dict[str, str], what: str) -> dict[str, str]:
    for key, val in (v or {}).items():
        if not key or "/" not in key or not str(val).strip():
            raise ValueError(f"{what} entries must be '<vendor>/<name>': '<quantity>' (got {key!r})")
    return v


def flag_value(args: list[str], flag: str) -> str | None:
    """The value of ``flag`` in an argv list (``--f=v`` or ``--f v``), None when absent."""
    for i, a in enumerate(args):
        if a.startswith(flag + "="):
            return a[len(flag) + 1 :]
        if a == flag:
            return args[i + 1] if i + 1 < len(args) else ""
    return None


class PdExtras(BaseModel):
    """One pool's P/D settings: the plumbing the portal used to inject (``--port``,
    ``--kv-transfer-config``, ``llm-d.ai/role``, NIXL side-channel port) plus
    UCX/RDMA env, flags, labels, annotations and resources. A pool that has them
    renders them verbatim on top of its general config; a pool without them
    (rows from before) keeps the portal-injected plumbing."""

    env: dict[str, str] = {}
    args: list[str] = []
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    resources: dict[str, str] = {}

    @field_validator("env")
    @classmethod
    def _env(cls, v):
        for key, val in v.items():
            if val.startswith(FIELD_REF_PREFIX) and not val[len(FIELD_REF_PREFIX) :].strip():
                raise ValueError(f"{key}: fieldRef needs a field path (e.g. fieldRef:status.podIP)")
        return v

    @field_validator("labels")
    @classmethod
    def _labels(cls, v):
        for key in v:
            if key in RESERVED_LABELS or key.startswith(RESERVED_LABEL_PREFIX):
                raise ValueError(f"pod label {key!r} is managed by the portal")
        return v

    @field_validator("resources")
    @classmethod
    def _resources(cls, v):
        return _resource_entries(v, "P/D resources")


class SidecarSpec(BaseModel):
    """The decode pool's routing sidecar (native-sidecar initContainer). A blank image
    uses the recipe's/portal's sidecar image; env values accept ``fieldRef:<path>``."""

    image: str | None = None
    args: list[str] = list(SIDECAR_ARGS)
    env: dict[str, str] = {}
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}

    @field_validator("image")
    @classmethod
    def _blank_is_none(cls, v):
        v = (v or "").strip()
        return v or None

    @field_validator("args")
    @classmethod
    def _port(cls, v):
        if flag_value(v, "--port") != str(SIDECAR_PORT):
            raise ValueError(f"routing sidecar needs --port={SIDECAR_PORT} (the router targets decode pods on it)")
        return v


def _check_pool_extras(role: Role, ex: PdExtras) -> None:
    """The plumbing a P/D pool cannot run without must be in its settings, matching the role."""
    if not ex.env.get(NIXL_HOST_ENV):
        raise ValueError(f"{role}: P/D settings need {NIXL_HOST_ENV} (usually fieldRef:status.podIP)")
    if ex.labels.get(LABEL_ROLE) != role:
        raise ValueError(f"{role}: P/D settings need the pod label {LABEL_ROLE}={role} (the router filters on it)")
    port = flag_value(ex.args, "--port")
    expected = PREFILL_PORT if role == "prefill" else DECODE_VLLM_PORT
    if port != str(expected):
        raise ValueError(f"{role}: P/D settings need --port={expected} (the service, sidecar and probes target it)")
    raw = flag_value(ex.args, "--kv-transfer-config")
    if not raw:
        raise ValueError(f"{role}: P/D settings need --kv-transfer-config")
    try:
        kv = json.loads(raw)
    except ValueError:
        raise ValueError(f"{role}: --kv-transfer-config must be JSON") from None
    want = "kv_producer" if role == "prefill" else "kv_consumer"
    if not isinstance(kv, dict) or kv.get("kv_role") != want or not kv.get("kv_connector"):
        raise ValueError(f"{role}: --kv-transfer-config needs kv_connector and kv_role={want!r}")
    nixl = ex.env.get("VLLM_NIXL_SIDE_CHANNEL_PORT")
    if nixl is not None and not (nixl.isdigit() and 1024 <= int(nixl) <= 65535):
        raise ValueError(f"{role}: VLLM_NIXL_SIDE_CHANNEL_PORT must be a port number (1024-65535)")


class RoleOverride(BaseModel):
    """One pool's config. ``None`` fields fall back to the recipe base."""

    replicas: int = Field(1, ge=0)
    image: str | None = None
    model_path: str | None = None
    gpu_count: int | None = Field(None, ge=0)
    gpu_type: str | None = None
    gpu_resource_key: str | None = None
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    storages: list[dict] | None = None
    engine_args: dict[str, str | int | float | bool] | None = None
    vllm_extra_args: list[str] | None = None
    env: dict[str, str] | None = None
    probes: dict | None = None
    runtime: dict | None = None
    node_selector: dict[str, str] | None = None
    tolerations: list | None = None
    pd_extras: PdExtras | None = None
    sidecar: SidecarSpec | None = None  # decode only

    @field_validator(
        "image",
        "model_path",
        "gpu_type",
        "gpu_resource_key",
        "cpu_request",
        "cpu_limit",
        "memory_request",
        "memory_limit",
    )
    @classmethod
    def _blank_is_none(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None

    @field_validator("storages")
    @classmethod
    def _storages(cls, v):
        # Imported here: storage_mounts imports ROLES from this module at load time.
        from app.services.storage_mounts import validate_entries

        return None if v is None else validate_entries(v)

    @field_validator("probes")
    @classmethod
    def _probes(cls, v):
        return validate_probes(v)

    @field_validator("runtime")
    @classmethod
    def _runtime(cls, v):
        return Runtime.model_validate(v).model_dump(exclude_none=True) if v is not None else None

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
    stack-level knobs the operator would otherwise set on the llm-d form.

    ``values_yaml`` (when set) is the router chart's values.yaml as written —
    used verbatim, so a chart upgrade needs no portal change — and supersedes
    the structured EPP/scheduler fields. ``chart_*`` pin the chart per recipe
    (blank = the portal default). See ``pd_router``.
    """

    peak_prefill_throughput: int = Field(DEFAULT_PEAK_PREFILL_THROUGHPUT, ge=1)
    prefix_tokens_to_match: int = Field(DEFAULT_PREFIX_TOKENS_TO_MATCH, ge=1)
    epp_registry: str | None = None
    epp_repository: str | None = None
    epp_tag: str | None = None
    epp_replicas: int | None = Field(None, ge=1)
    ingress_class: str | None = None
    values_yaml: str | None = None
    chart_repo: str | None = None
    chart_name: str | None = None
    chart_version: str | None = None

    @field_validator(
        "epp_registry", "epp_repository", "epp_tag", "ingress_class", "chart_repo", "chart_name", "chart_version"
    )
    @classmethod
    def _blank_is_none(cls, v):
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None

    @field_validator("values_yaml")
    @classmethod
    def _values_yaml(cls, v):
        if v is None or not v.strip():
            return None
        parse_router_values(v, "model")  # syntax + mapping check; placeholders filled with a dummy
        return v


class PdConfig(BaseModel):
    framework: PdFramework = DEFAULT_PD_FRAMEWORK
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

    @model_validator(mode="after")
    def _pool_extras(self):
        if self.prefill.sidecar is not None:
            raise ValueError("prefill: only the decode pool runs the routing sidecar")
        for role in ROLES:
            ex = getattr(self, role).pd_extras
            if ex is not None:
                _check_pool_extras(role, ex)
        return self


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
    pool: Any = None  # the pool as a deployment-like row (see ``pool_row``)
    # The pool has P/D settings: they carry --port / --kv-transfer-config / the NIXL port,
    # so the portal does not inject them (env/args are already merged above).
    explicit: bool = False
    pod_labels: dict[str, str] = field(default_factory=dict)
    pod_annotations: dict[str, str] = field(default_factory=dict)
    pd_resources: dict[str, str] = field(default_factory=dict)

    def resources(self) -> dict:
        """The CPU/memory values to render (role, else base) for ``_resources``."""
        return {
            "cpu_request": self.cpu_request,
            "cpu_limit": self.cpu_limit,
            "memory_request": self.memory_request,
            "memory_limit": self.memory_limit,
        }


# Deployment attributes a pool can override; everything else (namespace, name,
# cluster, ingress, status…) is read from the row itself.
POOL_FIELDS = (
    "image",
    "model_path",
    "gpu_count",
    "gpu_type",
    "gpu_resource_key",
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
    "storages",
    "probes",
    "runtime",
    "node_selector",
    "tolerations",
)


def pool_row(dep, role: Role, spec: RoleSpec | None = None) -> SimpleNamespace:
    """The pool as a deployment-like object the aggregated manifest helpers accept.

    Scalars: the role's value when set, else the row's. ``engine_args`` /
    ``vllm_extra_args`` / ``env`` are the merged values from ``role_view``.
    """
    cfg = pd_config_of(dep)
    ov = dict(cfg.get(role) or {})
    attrs = {
        key: getattr(dep, key, None)
        for key in (
            "id",
            "model_name",
            "namespace",
            "cluster_id",
            "engine",
            "serving_mode",
            "pd_config",
            "ingress_host",
            "ingress_path",
            "ingress_class",
            "replicas",
        )
    }
    for key in POOL_FIELDS:
        attrs[key] = ov[key] if ov.get(key) is not None else getattr(dep, key, None)
    if spec is None:
        spec = role_view(dep, role)
    attrs.update(
        engine_args=spec.engine_args,
        vllm_extra_args=spec.extra_args,
        env=spec.env,
        gpu_count=spec.gpu_count,
        gpu_type=spec.gpu_type,
        replicas=spec.replicas,
    )
    return SimpleNamespace(**attrs)


def pd_config_of(dep) -> dict:
    return dict(getattr(dep, "pd_config", None) or {})


def role_view(dep, role: Role) -> RoleSpec:
    """Merge the recipe base with the role's overrides (see spec Decision 3)."""
    cfg = pd_config_of(dep)
    ov = dict(cfg.get(role) or {})
    explicit = ov.get("pd_extras") is not None
    extras = dict(ov.get("pd_extras") or {})
    engine_args = {**(getattr(dep, "engine_args", None) or {}), **(ov.get("engine_args") or {})}
    extra_args = (
        list(getattr(dep, "vllm_extra_args", None) or [])
        + list(ov.get("vllm_extra_args") or [])
        + list(extras.get("args") or [])
    )
    env = {**(getattr(dep, "env", None) or {}), **(ov.get("env") or {}), **(extras.get("env") or {})}
    gpu_count = ov.get("gpu_count")
    if gpu_count is None:
        gpu_count = int(getattr(dep, "gpu_count", 0) or 0)

    def pick(key: str):
        return ov.get(key) or getattr(dep, key, None)

    spec = RoleSpec(
        role=role,
        port=PREFILL_PORT if role == "prefill" else DECODE_VLLM_PORT,
        replicas=int(ov.get("replicas", 1) if ov.get("replicas") is not None else 1),
        gpu_count=int(gpu_count),
        gpu_type=ov.get("gpu_type") or getattr(dep, "gpu_type", None),
        engine_args=engine_args,
        extra_args=extra_args,
        env=env,
        nixl_port=int(
            (extras.get("env") or {}).get("VLLM_NIXL_SIDE_CHANNEL_PORT") or cfg.get("nixl_port") or DEFAULT_NIXL_PORT
        ),
        cpu_request=pick("cpu_request"),
        cpu_limit=pick("cpu_limit"),
        memory_request=pick("memory_request"),
        memory_limit=pick("memory_limit"),
        explicit=explicit,
        pod_labels=_fill_name(extras.get("labels"), dep),
        pod_annotations=_fill_name(extras.get("annotations"), dep),
        pd_resources=dict(extras.get("resources") or {}),
    )
    return RoleSpec(**{**spec.__dict__, "pool": pool_row(dep, role, spec)})


def role_args(dep, spec: RoleSpec) -> list[str]:
    """``--model <path> --port <p> --kv-transfer-config <json> <engine args> <extra args>``.

    A pool with P/D settings carries ``--port`` / ``--kv-transfer-config`` in them
    (already in ``extra_args``), so only the model comes first.
    """
    model = spec.pool.model_path if spec.pool is not None else dep.model_path
    if spec.explicit:
        return ["--model", model, *render_engine_args(spec.engine_args), *spec.extra_args]
    cfg = pd_config_of(dep)
    return [
        "--model",
        model,
        "--port",
        str(spec.port),
        "--kv-transfer-config",
        kv_transfer_config(spec.role, cfg.get("kv_transfer_extra")),
        *render_engine_args(spec.engine_args),
        *spec.extra_args,
    ]


def _env_item(name: str, value) -> dict:
    text = str(value)
    if text.startswith(FIELD_REF_PREFIX):
        return {"name": name, "valueFrom": {"fieldRef": {"fieldPath": text[len(FIELD_REF_PREFIX) :].strip()}}}
    return {"name": name, "value": text}


def role_env(spec: RoleSpec) -> list[dict]:
    """Recipe env plus the NIXL side channel (host from the pod IP, port fixed).

    A pool with P/D settings carries the side channel in its own env (validated),
    where ``fieldRef:<path>`` values render as valueFrom.
    """
    if spec.explicit:
        return [_env_item(k, v) for k, v in spec.env.items()]
    items = [{"name": k, "value": str(v)} for k, v in spec.env.items() if k != NIXL_HOST_ENV]
    items.append({"name": NIXL_HOST_ENV, "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}})
    items = [i for i in items if i["name"] != "VLLM_NIXL_SIDE_CHANNEL_PORT"]
    items.append({"name": "VLLM_NIXL_SIDE_CHANNEL_PORT", "value": str(spec.nixl_port)})
    return items


def sidecar_container(image: str, spec: dict | None = None) -> dict:
    """llm-d routing sidecar as a native sidecar (init container that keeps running).

    ``spec`` is the decode pool's sidecar settings (image/args/env/resources); without
    it the portal defaults render, as for rows from before the settings existed.
    """
    spec = spec or {}
    container: dict = {
        "name": "routing-proxy",
        "image": (spec.get("image") or "").strip() or image,
        "imagePullPolicy": "Always",
        "args": list(spec["args"]) if spec.get("args") else list(SIDECAR_ARGS),
        "ports": [{"containerPort": SIDECAR_PORT, "name": "sidecar", "protocol": "TCP"}],
        "resources": {},
        "restartPolicy": "Always",
        "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True},
    }
    if spec.get("env"):
        container["env"] = [_env_item(k, v) for k, v in spec["env"].items()]
    for kind in ("requests", "limits"):
        if spec.get(kind):
            container["resources"][kind] = {k: str(v) for k, v in spec[kind].items()}
    return container


def decode_sidecar_spec(dep) -> dict | None:
    return (pd_config_of(dep).get("decode") or {}).get("sidecar")


def sidecar_image_for(dep, default: str | None) -> str:
    return pd_config_of(dep).get("sidecar_image") or default or DEFAULT_SIDECAR_IMAGE


def is_pd(dep) -> bool:
    return getattr(dep, "serving_mode", None) == "pd"


def fill_model_name(text: str, name: str) -> str:
    """Replace ``{{model_name}}`` placeholders in ``text`` with ``name``."""
    return MODEL_NAME_PLACEHOLDER.sub(name, text)


def parse_router_values(text: str, name: str) -> dict:
    """The router values.yaml with ``{{model_name}}`` filled in, as a dict (ValueError otherwise)."""
    import yaml

    try:
        data = yaml.safe_load(fill_model_name(text, name))
    except yaml.YAMLError as e:
        raise ValueError(f"router values.yaml is not valid YAML: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("router values.yaml must be a mapping")
    return data


def serving_name(dep) -> str:
    """The deployment's name as a K8s-safe token (resource-name stem, valid label value)."""
    return dep.model_name.lower().replace("_", "-").replace(".", "-").replace("/", "-")[:63].strip("-")


def _fill_name(values: dict | None, dep) -> dict[str, str]:
    name = serving_name(dep)
    return {k: MODEL_NAME_PLACEHOLDER.sub(name, str(v)) for k, v in (values or {}).items()}


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
