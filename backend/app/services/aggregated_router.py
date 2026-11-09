"""Render the dedicated llm-d stack for a single-pool deployment."""

import hashlib
import uuid
from typing import Annotated

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StringConstraints, with_config
from sqlalchemy import select
from typing_extensions import TypedDict

from app.config import settings
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.services import llmd_stacks, pd_serving
from app.services.llmd_manifests import LABEL_LLMD_MODEL, argo_app_name_for, deep_merge, default_llmd_values
from app.services.serving_engines import serving_port
from app.services.serving_names import model_label, model_stem


DEFAULT_CHART_VERSION = "v1.0.0"
DEFAULT_ENVOY_IMAGE = "docker.io/envoyproxy/envoy:distroless-v1.33.2"


@with_config(ConfigDict(extra="forbid"))
class RouterConfig(TypedDict, total=False):
    values_yaml: str
    chart_repo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    chart_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    chart_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


RouterOption = Annotated[
    RouterConfig | None,
    Field(
        description="Aggregated serving only. null uses the model Service; {} creates a dedicated llm-d router. "
        "Supply values_yaml to customize its Helm values."
    ),
]


def enabled(dep) -> bool:
    return getattr(dep, "aggregated_router", None) is not None


def routed(dep) -> bool:
    return enabled(dep) or pd_serving.is_pd(dep)


def chart_source(config: RouterConfig) -> tuple[str, str, str]:
    return (
        config.get("chart_repo", settings.llmd_chart_repo),
        config.get("chart_name", settings.llmd_chart_name),
        config.get("chart_version", DEFAULT_CHART_VERSION),
    )


def defaults() -> dict:
    image_repo = f"{settings.llmd_epp_image_registry}/{settings.llmd_epp_image_repository}"
    registry, repository = image_repo.rsplit("/", 1)
    values = default_llmd_values(
        "",
        epp_registry=registry,
        epp_repository=repository,
        epp_tag=DEFAULT_CHART_VERSION,
    )
    # Engine type, selector and port come from the target deployment.
    values["router"]["modelServers"] = {"matchLabels": {}}
    # v1.0.0 standalone chart defaults, with every co-located EPP replica active.
    epp = values["router"]["epp"]
    epp["image"]["pullPolicy"] = "IfNotPresent"
    epp["haMode"] = "all-active"
    epp["resources"] = {"requests": {"cpu": "8", "memory": "8Gi"}, "limits": {"memory": "16Gi"}}
    values["router"]["proxy"] = {
        "enabled": True,
        "proxyType": "envoy",
        "mode": "sidecar",
        "presets": {"envoy": {"image": DEFAULT_ENVOY_IMAGE}},
        "resources": {"requests": {"cpu": "4", "memory": "8Gi"}, "limits": {"memory": "16Gi"}},
    }
    return values


def validate_config(config, mode):
    if config is None:
        return
    if mode != "aggregated":
        raise ValueError("aggregated_router requires aggregated serving mode")
    pd_serving.parse_router_values(config.get("values_yaml", ""), "model")


def _scope_envoy_config(proxy: dict, release: str) -> None:
    # The chart otherwise creates and mounts "envoy" for every release in the namespace.
    config_name = f"{release}-envoy"
    config = proxy.setdefault("configMap", {})
    volumes = proxy.setdefault("volumes", [])
    if not isinstance(config, dict) or not isinstance(volumes, list) or not all(isinstance(v, dict) for v in volumes):
        raise ValueError("router.proxy needs a configMap mapping and a list of volumes")
    config["name"] = config_name
    for volume in volumes:
        if volume.get("name") == "config":
            if not isinstance(volume.get("configMap"), dict):
                raise ValueError("The Envoy config volume must mount the deployment's ConfigMap")
            volume["configMap"]["name"] = config_name
            break
    else:
        volumes.append({"name": "config", "configMap": {"name": config_name}})


def stack_for(dep, *, validate_targets=True):
    label, port = model_label(dep.model_name), serving_port(dep)
    override = pd_serving.parse_router_values(dep.aggregated_router.get("values_yaml", ""), label)
    values = deep_merge(defaults(), override)
    router = values.get("router")
    if not isinstance(router, dict) or not isinstance(router.get("modelServers"), dict):
        raise ValueError("router.modelServers must be a mapping")
    for key in ("epp", "proxy", "inferencePool"):
        if not isinstance(router.get(key), dict):
            raise ValueError(f"router.{key} must be a mapping")
    proxy = router["proxy"]
    if (
        proxy.get("mode", "sidecar") != "sidecar"
        or proxy.get("proxyType", "envoy") != "envoy"
        or proxy.get("enabled", True) is not True
        or router["inferencePool"].get("create") is not False
    ):
        raise ValueError("Aggregated routing requires standalone EPP with an Envoy sidecar")
    image = router["epp"].get("image")
    if not isinstance(image, dict) or not all(isinstance(image.get(k), str) and image[k].strip()
                                              for k in ("registry", "repository", "tag")):
        raise ValueError("router.epp.image requires a registry, repository and tag")
    presets = proxy.get("presets")
    envoy = presets.get("envoy") if isinstance(presets, dict) else None
    if not isinstance(envoy, dict):
        raise ValueError("router.proxy.presets.envoy must be a mapping")
    proxy_image = proxy.get("image", envoy.get("image"))
    if not isinstance(proxy_image, str) or not proxy_image.strip():
        raise ValueError("router.proxy.image must be an image reference")
    flags = router["epp"].get("flags", {})
    if (
        not isinstance(flags, dict)
        or {"endpoint-selector", "endpoint-target-ports", "pool-name", "pool-namespace"} & flags.keys()
    ):
        raise ValueError("router.epp.flags cannot override deployment endpoint selection")
    servers = router["modelServers"]
    servers.setdefault("type", dep.engine or "vllm")
    selector = {LABEL_LLMD_MODEL: label}
    if servers.get("matchLabels") not in (None, {}, selector):
        raise ValueError(f"router.modelServers.matchLabels must select this deployment: {selector}")
    ports = [{"number": port}]
    if validate_targets and "targetPorts" in servers and servers["targetPorts"] != ports:
        raise ValueError(f"router.modelServers.targetPorts must use the engine port {port}")
    servers.update(matchLabels=selector, targetPorts=ports)
    # Keep the generated release unique even when different model names sanitise to the same stem.
    digest = hashlib.sha256(dep.model_name.encode()).hexdigest()[:10]
    name = f"{model_stem(dep.model_name)[:15]}-{digest}-router"
    release = argo_app_name_for(name)
    _scope_envoy_config(proxy, release)
    repo, chart, version = chart_source(dep.aggregated_router)
    return CustomLlmdStack(
        chart_repo=repo,
        chart_name=chart,
        chart_version=version,
        name=name,
        target_model_name=dep.model_name,
        cluster_id=dep.cluster_id,
        namespace=dep.namespace,
        argo_app_name=release,
        helm_values=values,
        values_snapshot=values,
        ingress_host=dep.ingress_host,
        ingress_class=dep.ingress_class,
        ingress_path=dep.ingress_path,
        direct_route_enabled=False,
    )


async def prepare(db, dep):
    """Reserve before writing Kubernetes resources; retries keep the same stack identity."""
    desired = stack_for(dep, validate_targets=False)
    if dep.router_stack_id:
        stack = await db.get(CustomLlmdStack, dep.router_stack_id)
        if not stack or not dep.router_stack_created or stack.name != desired.name:
            raise HTTPException(409, "Deployment does not own the requested router")
        # Repair and port changes retain the Helm chart, images and resources
        # provisioned for this deployment, including stacks from older versions.
        target = desired.helm_values["router"]["modelServers"]
        patch = {"router": {"modelServers": target}}
        stack.helm_values = deep_merge(stack.helm_values, patch)
        stack.values_snapshot = deep_merge(stack.values_snapshot, patch)
        stack.ingress_host = dep.ingress_host
        stack.ingress_path = dep.ingress_path
        stack.ingress_class = dep.ingress_class
    else:
        collision = await db.scalar(
            select(CustomLlmdStack.id).where(CustomLlmdStack.argo_app_name == desired.argo_app_name)
        )
        if collision:
            raise HTTPException(409, f"Router release {desired.argo_app_name} already exists")
        stack = desired
        stack.id, stack.created_by = uuid.uuid4(), dep.updated_by
        db.add(stack)
        await db.flush()
        dep.router_stack_id, dep.router_stack_created = stack.id, True
    stack.updated_by = dep.updated_by
    await db.flush()
    return stack


async def sync(db, dep, *, reserved=None):
    stack = reserved if reserved is not None else await prepare(db, dep)
    try:
        await llmd_stacks.apply_stack(db, stack)
    except HTTPException as exc:
        dep.router_apply_error = str(exc.detail)
        dep.status_message = dep.router_apply_error
    else:
        dep.router_apply_error = None
        dep.status_message = None
    await db.flush()
    await db.refresh(dep)
