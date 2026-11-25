"""Capacity from the exact manifests used to deploy each serving pool."""

import re
from decimal import Decimal, DecimalException
from functools import partial

from sqlalchemy import func, or_, select

from app.config import settings
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import pd_serving
from app.services.gpu_profile_store import resolve_gpu_type
from app.services.kv_capacity import DEEPSEEK_V4_TYPES, RULE_VERSION, Scenario, estimate, final_flags, memory_size
from app.services.kv_draft_capacity import dspark_config
from app.services.kv_draft_metadata import draft_from_metadata, resolve_draft
from app.services.kv_host_offload import host_kv_offload
from app.services.kv_identity import pool_fingerprint
from app.services.kv_layout import text_config
from app.services.kv_metadata import metadata_for, uses_public_hub
from app.services.model_deployment_manifests import build_all


def _gpu_count(value):
    """Kubernetes extended resources must resolve to a whole, nonnegative quantity."""
    text = str(value)
    scales = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "m": Decimal(".001"), "k": 1000, "M": 10**6, "G": 10**9,
    }
    try:
        suffix = next((unit for unit in scales if text.endswith(unit)), "")
        amount = Decimal(text.removesuffix(suffix)) * scales.get(suffix, 1)
        if amount.is_finite() and 0 <= amount < 2**63 and amount == amount.to_integral_value():
            return int(amount)
    except DecimalException:
        pass
    return None


async def _saved_pool_observation(db, recipe_id, dep, role, fingerprint, container, gpu_type):
    """One real Pod incarnation with this recipe, target and immutable engine/weights."""
    try:
        from app.services.kv_observations import Observation, model_revision, pinned
    except ModuleNotFoundError as exc:
        if exc.name != "app.services.kv_observations":
            raise
        return None
    revision = model_revision(container["args"])
    if not pinned(container["image"], revision, gpu_type):
        return None
    # The observation worker and its migration can be installed independently.
    if not await db.scalar(select(func.to_regclass(Observation.__tablename__))):
        return None
    row = await db.scalar(
        select(Observation)
        .join(CustomModelDeployment, CustomModelDeployment.id == Observation.deployment_id)
        .where(
            CustomModelDeployment.recipe_id == recipe_id,
            CustomModelDeployment.cluster_id == dep.cluster_id,
            CustomModelDeployment.namespace == dep.namespace,
            Observation.role == role,
            Observation.fingerprint == fingerprint,
            Observation.gpu_type == gpu_type,
            Observation.image == container["image"],
            Observation.revision == revision,
            or_(Observation.capacity_tokens.is_not(None), Observation.budget_bytes.is_not(None)),
        )
        .order_by(
            Observation.active.desc(), Observation.last_seen.desc(), Observation.pod_uid, Observation.restart.desc()
        )
        .limit(1)
    )
    if row is None:
        return None
    return {
        "capacity_tokens": row.capacity_tokens,
        "budget_bytes_per_gpu": row.budget_bytes,
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        "deployment_id": str(row.deployment_id),
        "pod_name": row.pod_name,
        "active": row.active,
    }


async def _with_draft_metadata(db, dep, pod, container, target, draft, start):
    model, revision = draft.get("model"), draft.get("revision")
    if not isinstance(model, str) or not model.strip() or (revision is not None and not isinstance(revision, str)):
        return {**target, "resolved_draft": None, "draft_error": "Invalid DSpark checkpoint or revision."}
    if uses_public_hub(pod, container):
        reference = resolve_draft(model, revision)
        if reference is not None:
            return {**target, "resolved_draft": reference}
    metadata = await metadata_for(db, dep, pod, container, model, revision, start=start)
    descriptor = draft_from_metadata(metadata)
    return {
        **target, "resolved_draft": descriptor, "pending": bool(metadata.get("pending")), "_draft_metadata": metadata,
        "draft_error": metadata.get("error") or "DSpark checkpoint layout or tensor sizes are unavailable.",
    }


def _pool_size(scenario, *, metadata, container, profile_vram, gpu_type, budget, resource_key, gpu_count):
    size = estimate(
        metadata, container["name"], container["args"], scenario=scenario, vram_gb=profile_vram,
        gpu_type=gpu_type, observed_budget=budget,
        env={e["name"]: e["value"] for e in container.get("env", []) if "value" in e},
    )
    ranks = size.get("tp", 0) * size.get("pp", 0)
    if resource_key and (gpu_count is None or gpu_count < ranks):
        size["missing"].append(
            "GPU resource quantity must be a whole number."
            if gpu_count is None else f"TP × PP requires {ranks} GPUs per Pod; the recipe requests {gpu_count}."
        )
        size.update(capacity_tokens=None, complete_requests=None)
        size.pop("capacity_range", None)
        size.pop("complete_requests_range", None)
    return size


def _shorter_workload(workload, pools):
    # Preserve output length and concurrency whenever a full output still fits.
    limits = [p["max_model_len"] for p in pools if isinstance(p.get("max_model_len"), int) and p["max_model_len"] > 0]
    limit = min(limits, default=workload["input_tokens"] + workload["output_tokens"])
    if limit < 2:
        return None
    output = min(workload["output_tokens"], limit - 1)
    inputs = min(workload["input_tokens"], 8192, limit - output)
    if (inputs, output) == (workload["input_tokens"], workload["output_tokens"]):
        inputs = max(1, inputs // 2)
        if inputs == workload["input_tokens"]:
            output = max(1, output // 2)
    if (inputs, output) != (workload["input_tokens"], workload["output_tokens"]):
        return {**workload, "input_tokens": inputs, "output_tokens": output}
    return None


async def deployment_estimate(
    db, dep, *, manifests=None, scenario=Scenario(), start_metadata=False, base_profile=None, saved_recipe_id=None,
    throughput_scenario=None,
):
    if manifests is None:
        manifests = build_all(dep, sidecar_image=settings.llmd_sidecar_image)
    pools = []
    throughput_pools = []
    sizes = []
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
        pool = (dep.pd_config or {}).get(role) or {}
        gpu_type = pool.get("gpu_type") or dep.gpu_type
        profile = base_profile if base_profile and base_profile.name == gpu_type else None
        if profile is None and gpu_type:
            profile, _ = await resolve_gpu_type(db, dep.cluster_id, gpu_type)
        vram_gb = getattr(profile, "vram_gb", None)
        resource_key = pool.get("gpu_resource_key") or getattr(dep, "gpu_resource_key", None)
        limits = container.get("resources", {}).get("limits", {})
        gpu_count = _gpu_count(limits.get(resource_key, 0)) if resource_key else None
        fingerprint = pool_fingerprint(dep.cluster_id, dep.namespace, role, gpu_type, container, pod)
        info = {
            "role": role,
            "fingerprint": fingerprint,
            "engine": container["name"],
            "image": container["image"],
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "gpu_profile_resolved": profile is not None,
            "gpu_vram_gb": vram_gb,
            "replicas": doc["spec"].get("replicas", 1),
            "model_path": model,
            "capacity_tokens": None,
            "complete_requests": None,
            "rule_version": RULE_VERSION,
        }
        offload_env = {"VLLM_USE_SIMPLE_KV_OFFLOAD": None} if container.get("envFrom") else {}
        offload_env.update({e["name"]: e.get("value") for e in container.get("env", [])
                            if e["name"] == "VLLM_USE_SIMPLE_KV_OFFLOAD"})
        host_cache = host_kv_offload(container["name"], flags, env=offload_env)
        if host_cache is not None:
            info["host_kv_offload"] = host_cache
        pools.append(info)
        observed = None
        if saved_recipe_id is not None and profile is not None:
            observed = await _saved_pool_observation(db, saved_recipe_id, dep, role, fingerprint, container, gpu_type)
            if observed is not None:
                info["observed"] = observed
        public_hub = uses_public_hub(pod, container)
        if any(
            v.get("persistentVolumeClaim", {}).get("claimName", "").startswith("<unbound:")
            for v in pod.get("volumes", [])
        ):
            meta = {"error": "Model storage has no binding for this cluster and namespace.",
                    "metadata_issue": "mount_unavailable"}
        else:
            meta = await metadata_for(db, dep, pod, container, model or "", flags.get("revision"), start=start_metadata)
            if throughput_scenario is not None:
                from app.services.recipe_throughput_evidence import throughput_metadata

                meta = throughput_metadata(meta, model, flags, public_hub=public_hub)
        try:
            draft = dspark_config(flags) if container["name"] == "vllm" else None
        except ValueError:
            draft = None  # estimate() reports the invalid launch option without interrupting the deployment.
        integrated = (
            bool(draft) and text_config(meta.get("config", {})).get("model_type") in DEEPSEEK_V4_TYPES
            and draft.get("model") in (None, model) and draft.get("revision") in (None, flags.get("revision"))
        )
        if draft and not integrated and meta.get("config"):
            meta = await _with_draft_metadata(db, dep, pod, container, meta, draft, start_metadata)
        info["pending"] = bool(meta.get("pending"))
        # A saved observation still describes its own Pod. Reuse its memory budget
        # for another launch only when the draft weights also identify a fixed revision.
        draft_pinned = not draft or integrated or (
            isinstance(draft.get("revision"), str)
            and re.fullmatch(r"[0-9a-fA-F]{40}", draft["revision"])
            and not str(draft.get("model", "")).startswith("/")
        )
        matched = observed["budget_bytes_per_gpu"] if observed and draft_pinned else None
        try:
            use_profiled_budget = container["name"] != "vllm" or not memory_size(
                flags.get("kv-cache-memory-bytes", "0")
            )
        except (ValueError, ArithmeticError):
            # The estimator reports the malformed override; a previous observation cannot correct it.
            use_profiled_budget = False
        if (draft_pinned and use_profiled_budget and saved_recipe_id is None
                and meta.get("config") and profile is not None):
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
            engine_version=meta.get("engine_versions", {}).get(container["name"]),
            revision=meta.get("revision"),
            metadata_digest=meta.get("digest"),
            config_digest=meta.get("config_digest"),
            metadata_source=meta.get("source"),
            weight_bytes=meta.get("weight_bytes"),
            weight_format=meta.get("weight_format"),
        )
        size_pool = partial(_pool_size, metadata=meta, container=container, profile_vram=vram_gb,
                            gpu_type=gpu_type, budget=matched, resource_key=resource_key, gpu_count=gpu_count)
        if throughput_scenario is not None:
            from app.services.recipe_throughput_evidence import throughput_metadata_diagnostic
            from app.services.speculative_throughput import public_draft_known, speculative_options

            diagnostic = throughput_metadata_diagnostic(meta, model, flags, public_hub=public_hub)
            if diagnostic:
                info["metadata_issue"] = diagnostic
            draft_meta = meta.get("_draft_metadata")
            opts = speculative_options(flags)
            if opts and opts.get("method") in ("eagle3", "dspark", "dfly", "mtp"):
                info["draft_method"] = opts["method"]
                width = opts.get("num_speculative_tokens")
                if type(width) is int and width > 0:
                    info["draft_tokens"] = width
            if (not draft and opts and isinstance(opts.get("model"), str) and opts["model"] != model
                    and (opts.get("revision") is None or isinstance(opts.get("revision"), str))
                    and meta.get("config")
                    and not (public_hub and public_draft_known(opts["model"], opts.get("revision")))):
                draft_meta = await metadata_for(db, dep, pod, container, opts["model"], opts.get("revision"),
                                                start=start_metadata)
                info["pending"] |= bool(draft_meta.get("pending"))
            # Keep the raw model inputs and sizing function private. Re-evaluate a
            # shorter scenario on the same evidence without another metadata Job or DB read.
            throughput_pools.append({
                **info, "flags": flags, "metadata": meta, "public_hub": public_hub,
                "draft_metadata": draft_meta, "gpu_label_value": getattr(profile, "label_value", None),
                "v2_runner": next((e.get("value", "unknown") for e in reversed(container.get("env", []))
                                   if e.get("name") == "VLLM_USE_V2_MODEL_RUNNER"),
                                  "unknown" if container.get("envFrom") else None),
            })
            sizes.append(size_pool)
        info.update(size_pool(scenario))
        if throughput_scenario is not None and not info.get("max_model_len"):
            # Some KV layouts cannot be resolved, while the launch context limit is still explicit.
            maximum = flags.get("max-model-len")
            if maximum in (None, "auto", "-1"):
                maximum = text_config(meta.get("config") or {}).get("max_position_embeddings")
            if maximum:
                try:
                    info["max_model_len"] = memory_size(maximum)
                except (ValueError, ArithmeticError):
                    pass

    result = {"rule_version": RULE_VERSION, "pools": pools}
    if throughput_scenario is not None:
        from app.services.recipe_throughput import estimate_throughput

        primary = [{**p, **s} for p, s in zip(throughput_pools, pools, strict=True)]
        forecast = estimate_throughput(primary, throughput_scenario)
        checked, previous = forecast, throughput_scenario
        while checked.get("reason") in {"context_limit", "kv_insufficient"}:
            shorter = _shorter_workload(previous, pools)
            if not shorter:
                break
            alternate = Scenario(shorter["input_tokens"] + shorter["output_tokens"], shorter["concurrency"],
                                 shorter["input_tokens"] * shorter["shared_prefix_percent"] // 100)
            checked = estimate_throughput(
                [{**p, **size(alternate)} for p, size in zip(throughput_pools, sizes, strict=True)], shorter,
            )
            forecast["shorter_workload"] = {"scenario": shorter, "status": checked["status"],
                                            "reason": checked["reason"]}
            if checked["reason"] == "draft_unknown":
                forecast.update({k: checked[k] for k in ("draft_missing", "draft_method") if k in checked})
            previous = shorter
        result["throughput"] = forecast
    return result
