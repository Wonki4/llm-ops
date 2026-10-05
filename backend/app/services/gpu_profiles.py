"""Resolve a GPU type profile into concrete pod placement.

A profile (``CustomGpuProfile`` or any object with ``label_key``,
``label_value``, ``gpu_resource_key``, ``tolerations``) is merged into the
placement a recipe/deployment spelled out explicitly:

* ``node_selector``: the profile's label pair is set on top of the explicit
  selector (same key → profile wins; other keys stay).
* ``tolerations``: union, de-duplicated on (key, operator, value, effect).
* ``gpu_resource_key``: the profile's.

No profile → explicit values pass through unchanged. Pure functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_GPU_LABEL_KEY = "gpu-type"
DEFAULT_GPU_RESOURCE_KEY = "nvidia.com/gpu"

_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")


def validate_profile_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name):
        raise ValueError("gpu_type must match ^[a-z0-9][a-z0-9.-]*$ (max 64 chars)")
    return name


@dataclass(frozen=True)
class Placement:
    node_selector: dict | None
    tolerations: list | None
    gpu_resource_key: str


def _toleration_key(t: Any) -> tuple:
    if not isinstance(t, dict):
        return (repr(t),)
    return (t.get("key"), t.get("operator"), t.get("value"), t.get("effect"))


def merge_tolerations(explicit: list | None, extra: list | None) -> list | None:
    out: list = []
    seen: set[tuple] = set()
    for t in list(explicit or []) + list(extra or []):
        k = _toleration_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(dict(t) if isinstance(t, dict) else t)
    return out or None


def profile_label_key(profile, cluster_label_key: str | None) -> str:
    return getattr(profile, "label_key", None) or cluster_label_key or DEFAULT_GPU_LABEL_KEY


def resolve_placement(
    profile,
    cluster_label_key: str | None,
    *,
    node_selector: dict | None,
    tolerations: list | None,
    gpu_resource_key: str | None,
) -> Placement:
    if profile is None:
        return Placement(
            node_selector=dict(node_selector) if node_selector else None,
            tolerations=list(tolerations) if tolerations else None,
            gpu_resource_key=gpu_resource_key or DEFAULT_GPU_RESOURCE_KEY,
        )
    selector = dict(node_selector or {})
    selector[profile_label_key(profile, cluster_label_key)] = profile.label_value
    return Placement(
        node_selector=selector,
        tolerations=merge_tolerations(tolerations, getattr(profile, "tolerations", None)),
        gpu_resource_key=getattr(profile, "gpu_resource_key", None) or DEFAULT_GPU_RESOURCE_KEY,
    )


def apply_profile(dep, profile, cluster_label_key: str | None) -> None:
    """Resolve in place on a deployment-like row (used at deploy time)."""
    placement = resolve_placement(
        profile,
        cluster_label_key,
        node_selector=dep.node_selector,
        tolerations=dep.tolerations,
        gpu_resource_key=dep.gpu_resource_key,
    )
    dep.node_selector = placement.node_selector
    dep.tolerations = placement.tolerations
    dep.gpu_resource_key = placement.gpu_resource_key


def match_profile(profiles: list, label_key: str, node_selector: dict | None):
    """The enabled profile whose (effective label key, value) is in ``node_selector``, else None.

    Used by the recipe importer to recognise a GPU type on an external Deployment.
    """
    for p in profiles:
        if not getattr(p, "enabled", True):
            continue
        key = profile_label_key(p, label_key)
        if (node_selector or {}).get(key) == p.label_value:
            return p
    return None
