"""Capacity from the exact manifests used to deploy each serving pool."""

from app.config import settings
from app.services import pd_serving
from app.services.gpu_profile_store import resolve_gpu_type
from app.services.kv_capacity import RULE_VERSION, Scenario, estimate, final_flags
from app.services.kv_identity import pool_fingerprint
from app.services.kv_metadata import metadata_for
from app.services.model_deployment_manifests import build_all


async def deployment_estimate(db, dep, *, manifests=None, scenario=Scenario(), start_metadata=False, base_profile=None):
    if manifests is None:
        manifests = build_all(dep, sidecar_image=settings.llmd_sidecar_image)
    pools = []
    for doc in manifests:
        if doc.get("kind") != "Deployment":
            continue
        template = doc["spec"]["template"]
        role = template["metadata"]["labels"].get(pd_serving.LABEL_PD_ROLE, "aggregated")
        pod = template["spec"]
        container = next((c for c in pod["containers"] if c["name"] in ("vllm", "sglang")), None)
        if not container:
            continue
        flags = final_flags(container["args"])
        model = flags.get("model-path") or flags.get("model")
        gpu_type = ((dep.pd_config or {}).get(role) or {}).get("gpu_type") or dep.gpu_type
        fingerprint = pool_fingerprint(dep.cluster_id, dep.namespace, role, gpu_type, container, pod)
        info = {
            "role": role,
            "fingerprint": fingerprint,
            "engine": container["name"],
            "image": container["image"],
            "gpu_type": gpu_type,
            "replicas": doc["spec"].get("replicas", 1),
            "model_path": model,
            "capacity_tokens": None,
            "complete_requests": None,
            "rule_version": RULE_VERSION,
        }
        pools.append(info)
        meta = await metadata_for(db, dep, pod, container, model or "", flags.get("revision"), start=start_metadata)
        if not meta.get("config"):
            info.update(pending=bool(meta.get("pending")), missing=[meta.get("error") or "reading config.json"])
            continue
        profile = base_profile if base_profile and base_profile.name == gpu_type else None
        if profile is None and gpu_type:
            profile, _ = await resolve_gpu_type(db, dep.cluster_id, gpu_type)
        matched = None
        if "kv-cache-memory-bytes" not in flags:
            # Independent observation feature. Either PR may be deployed first.
            try:
                from app.services.kv_observations import matching_budget
            except ModuleNotFoundError as exc:
                if exc.name != "app.services.kv_observations":
                    raise
            else:
                matched = await matching_budget(
                    db, fingerprint, image=container["image"], revision=flags.get("revision"), gpu_type=gpu_type
                )
        info.update(
            estimate(
                meta,
                container["name"],
                container["args"],
                vram_gb=getattr(profile, "vram_gb", None),
                observed_budget=matched,
                scenario=scenario,
                env={v["name"]: v["value"] for v in container.get("env", []) if "value" in v},
            )
        )
        info.update(
            engine_version=meta.get("engine_versions", {}).get(container["name"]),
            revision=meta.get("revision"),
            metadata_digest=meta.get("digest"),
            config_digest=meta.get("config_digest"),
            metadata_source=meta.get("source"),
            weight_bytes=meta.get("weight_bytes"),
            weight_format=meta.get("weight_format"),
        )
    return {"rule_version": RULE_VERSION, "pools": pools}
