"""Idempotent Kubernetes/LiteLLM actions used by the durable validation worker."""

import json
import math
import re
import uuid
from copy import deepcopy
from datetime import UTC
from pathlib import Path

from kubernetes_asyncio import client
from kubernetes_asyncio.client.exceptions import ApiException
from sqlalchemy import select

from app.config import settings
from app.db.models.custom_benchmark_run import CustomBenchmarkRun
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.services import aggregated_router, llmd_stacks, pd_router, pd_serving
from app.services.benchmark_credentials import BenchmarkCredentials
from app.services.benchmark_manifests import job_name_for
from app.services.clusters import argocd_placement_for, k8s_for_cluster
from app.services.model_deployment_manifests import build_all, k8s_resource_names, serving_api_key
from app.services.recipe_validations import unpack
from app.services.serving_engines import engine_flags
from app.services.validation_protocol import effective_flag, without_flags


def stable_id(attempt, key):
    return uuid.uuid5(attempt.id, key)


def scrub(value, snapshot):
    """Bounded diagnostics may contain echoed args or environment credentials."""
    from app.services.benchmark_credentials import sensitive_name

    secrets = set()

    def collect(v, env=False):
        if isinstance(v, dict):
            for k, item in v.items():
                if (env or sensitive_name(k)) and isinstance(item, str) and item:
                    secrets.add(item)
                collect(item, k == "env")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, str) and item.startswith("--") and sensitive_name(item.split("=")[0]):
                    secrets.add(item.split("=", 1)[1] if "=" in item else str(v[i + 1]) if i + 1 < len(v) else "")
                collect(item)

    collect(snapshot)
    ordered = sorted(filter(None, secrets), key=len, reverse=True)

    def redact_text(text, *, key=False):
        for secret in ordered:
            # Preserve field names like p90_ttft_ms when an env switch is "0".
            if key and len(secret) < 8 and text != secret:
                continue
            text = text.replace(secret, "***")
        return re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+", r"\1***", text)

    def redact(item):
        if isinstance(item, dict):
            return {redact_text(str(key), key=True): redact(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [redact(child) for child in item]
        if item is None or isinstance(item, (bool, float, int)):
            return item
        return redact_text(str(item))

    return redact(value)


async def prepare(db, job, candidate, attempt):
    """Persist identities first. This function never writes external resources."""
    dep_id = stable_id(attempt, "deployment")
    dep = await db.get(CustomModelDeployment, dep_id)
    if dep:
        return dep
    fields = deepcopy(unpack(candidate)["deployment"])
    for name in ("cluster_id", "recipe_id"):
        fields[name] = uuid.UUID(fields[name]) if fields.get(name) else None
    fields["recipe_id"] = candidate.recipe_id
    fields.update(
        model_name=f"validation-{attempt.id.hex[:20]}",
        litellm_model_name=job.model_name,
        litellm_api_base=None,
        created_by=job.created_by,
        updated_by=job.created_by,
    )
    dep = CustomModelDeployment(
        id=dep_id,
        **fields,
        validation_id=job.id,
        validation_attempt_id=attempt.id,
        validation_state="candidate",
        status="Pending",
        ready_replicas=0,
    )
    db.add(dep)
    await db.flush()
    if aggregated_router.enabled(dep):
        stack = await aggregated_router.prepare(db, dep)
        stack.validation_private = True
    elif pd_serving.is_pd(dep):
        cfg = (dep.pd_config or {}).get("router") or {}
        stack = CustomLlmdStack(
            id=stable_id(attempt, "router"),
            name=pd_router.stack_name(dep),
            argo_app_name=pd_router.stack_name(dep),
            target_model_name=dep.model_name,
            cluster_id=job.cluster_id,
            namespace=job.namespace,
            helm_values=pd_router.router_values(dep),
            values_snapshot={},
            validation_private=True,
            ingress_host=dep.ingress_host,
            ingress_class=dep.ingress_class,
            direct_route_enabled=False,
            created_by=job.created_by,
            updated_by=job.created_by,
            **{
                key: cfg.get(key)
                for key in ("chart_repo", "chart_name", "chart_version", "epp_registry", "epp_repository", "epp_tag")
            },
        )
        stack.values_snapshot = llmd_stacks.values_for(stack)
        db.add(stack)
        await db.flush()
        dep.router_stack_id, dep.router_stack_created = stack.id, True
    attempt.deployment_id = dep.id
    return dep


def bind_node(pod, node_name):
    if node_name:
        pod.setdefault("affinity", {})["nodeAffinity"] = {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {"matchFields": [{"key": "metadata.name", "operator": "In", "values": [node_name]}]}
                ]
            }
        }


def manifests(dep, limits, *, public=False, assets=None):
    docs = build_all(dep, sidecar_image=settings.llmd_sidecar_image)
    result = []
    for doc in docs:
        if doc["kind"] == "Ingress" and not public:
            continue
        if doc["kind"] == "Deployment":
            index = sum(d["kind"] == "Deployment" for d in result)
            spec = doc["spec"]
            bind_node(spec["template"]["spec"], (assets or {}).get(str(index), {}).get("node_name"))
            spec["strategy"] = {"type": "Recreate"}  # Never temporarily exceed the GPU envelope.
            spec["progressDeadlineSeconds"] = limits["scheduling"] + limits["startup"] + 60
            for container in spec["template"]["spec"]["containers"]:
                readiness = container.get("readinessProbe")
                if readiness:
                    container["startupProbe"] = {
                        **deepcopy(readiness),
                        "periodSeconds": 15,
                        "failureThreshold": math.ceil(limits["startup"] / 15) + 1,
                        "successThreshold": 1,
                    }
            if getattr(dep, "validation_id", None):
                spec["template"].setdefault("metadata", {}).setdefault("labels", {})["llm-ops/validation"] = str(
                    dep.validation_id
                )
                spec["template"]["metadata"]["labels"]["llm-ops/validation-attempt"] = str(dep.validation_attempt_id)
        result.append(doc)
    return result


def asset_jobs(dep, attempt, protocol):
    protocol = deepcopy(protocol)
    if attempt.state.get("restore_assets", {}).get("accuracy"):
        protocol["accuracy_image"] = attempt.state["restore_assets"]["accuracy"]["image"]
    source = Path(__file__).with_name("validation_asset_runner.py").read_text()
    out = []
    pools = [pd_serving.pool_row(dep, role) for role in pd_serving.ROLES] if pd_serving.is_pd(dep) else [dep]
    docs = [m for m in manifests(dep, protocol["time_limits"]) if m["kind"] == "Deployment"]
    for index, (doc, pool) in enumerate(zip(docs, pools)):
        name = f"assets-{attempt.id.hex[:24]}-{index}"
        pod = deepcopy(doc["spec"]["template"]["spec"])
        bind_node(pod, attempt.state.get("restore_assets", {}).get(str(index), {}).get("node_name"))
        container = next(c for c in pod["containers"] if c.get("readinessProbe"))
        pod["containers"] = [container]
        pod.pop("initContainers", None)
        for key in ("startupProbe", "readinessProbe", "livenessProbe", "ports", "args", "lifecycle"):
            container.pop(key, None)
        container["command"] = ["python", "-c", source]
        container["resources"] = {"requests": {"cpu": "500m", "memory": "1Gi"}, "limits": {"cpu": "2", "memory": "4Gi"}}
        flags = engine_flags(pool)
        cfg = {
            "model_path": pool.model_path,
            "revision": effective_flag(flags, "--revision"),
            "server_tokenizer": effective_flag(flags, "--tokenizer") or effective_flag(flags, "--tokenizer-path"),
            "tokenizer_revision": effective_flag(flags, "--tokenizer-revision"),
            "trust_remote_code": "--trust-remote-code" in flags,
            "tokenizer": protocol.get("tokenizer"),
            "workload_tokenizer_revision": attempt.state.get("restore_assets", {})
            .get(str(index), {})
            .get("workload_tokenizer_revision"),
            "airgap": protocol["airgap"],
        }
        container.setdefault("env", []).append({"name": "VALIDATION_ASSETS", "value": json.dumps(cfg)})
        if protocol["airgap"]:
            container["env"] += offline_env()
        credentials = BenchmarkCredentials(stable_id(attempt, f"assets-{index}"))
        pod["containers"] = [credentials.protect_container(container)]
        pod["restartPolicy"] = "Never"
        out.append(
            (
                name,
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {"name": name, "labels": {"bench-run": str(credentials.run_id)}},
                    "spec": {
                        "backoffLimit": 0,
                        "activeDeadlineSeconds": protocol["time_limits"]["assets"],
                        "template": {
                            "metadata": {"labels": {"llm-ops/validation-attempt": str(attempt.id)}},
                            "spec": pod,
                        },
                    },
                },
                credentials,
                index,
            )
        )
    if protocol.get("accuracy"):
        _, template, _, _ = out[-1]
        template = deepcopy(template)
        name = f"assets-{attempt.id.hex[:24]}-accuracy"
        template["metadata"]["name"] = name
        container = template["spec"]["template"]["spec"]["containers"][0]
        bind_node(
            template["spec"]["template"]["spec"],
            attempt.state.get("restore_assets", {}).get("accuracy", {}).get("node_name"),
        )
        from app.services.pd_serving import env_item

        container["env"] = [env_item(k, v) for k, v in (pools[-1].env or {}).items()]
        if protocol["airgap"]:
            container["env"] += offline_env()
        container["image"] = protocol.get("accuracy_image") or settings.benchmark_accuracy_image
        cfg = {"kind": "accuracy", "tasks": protocol["accuracy"]["tasks"]}
        container["env"].append({"name": "VALIDATION_ASSETS", "value": json.dumps(cfg)})
        credentials = BenchmarkCredentials(stable_id(attempt, "assets-accuracy"))
        template["metadata"]["labels"] = {"bench-run": str(credentials.run_id)}
        template["spec"]["template"]["spec"]["containers"] = [credentials.protect_container(container)]
        out.append((name, template, credentials, "accuracy"))
    return out


def offline_env():
    return [{"name": key, "value": "1"} for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")]


async def image_ids(k8s, namespace, selector):
    async with await k8s._api_client() as api:
        pods = await client.CoreV1Api(api).list_namespaced_pod(namespace, label_selector=selector)
        return {
            f"{i}:{s.name}": s.image_id
            for i, p in enumerate(pods.items)
            for s in (p.status.container_statuses or [])
            if s.image_id
        }


def pinned_image(original, image_id):
    value = image_id.split("://", 1)[-1]
    if "@sha256:" in value:
        return value
    if value.startswith("sha256:"):
        repo = original.split("@")[0]
        if ":" in repo.rsplit("/", 1)[-1]:
            repo = repo.rsplit(":", 1)[0]
        return f"{repo}@{value}"
    raise ValueError("The runtime did not report an immutable image digest")


async def asset_placement(k8s, dep, index, manifest, checked):
    pod = manifest["spec"]["template"]["spec"]
    if index == "accuracy":
        bind_node(pod, checked.get("1", checked.get("0", {})).get("node_name"))
        return
    pool = pd_serving.pool_row(dep, pd_serving.ROLES[index]) if pd_serving.is_pd(dep) else dep
    needed = pool.gpu_count * pool.replicas
    async with await k8s._api_client() as api:
        nodes = await client.CoreV1Api(api).list_node(_request_timeout=15)
    eligible = [
        node.metadata.name
        for node in nodes.items
        if not node.spec.unschedulable
        and int((node.status.allocatable or {}).get(pool.gpu_resource_key, 0)) >= needed
        and all((node.metadata.labels or {}).get(k) == v for k, v in pod.get("nodeSelector", {}).items())
    ]
    if not eligible:
        raise ValueError(f"No schedulable node matches the recipe and provides {needed} {pool.gpu_resource_key}")
    if not pod.get("affinity", {}).get("nodeAffinity"):
        pod.setdefault("affinity", {})["nodeAffinity"] = {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [{"matchFields": [{"key": "metadata.name", "operator": "In", "values": eligible}]}]
            }
        }


async def preflight(db, job, candidate, attempt, dep):
    k8s = await k8s_for_cluster(db, job.cluster_id)
    results = {}
    for name, manifest, credentials, index in asset_jobs(dep, attempt, job.conditions):
        try:
            status = await k8s.read_job_status(job.namespace, name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            await asset_placement(k8s, dep, index, manifest, results)
            await k8s.create_job(job.namespace, manifest, secret=credentials.manifest())
            return None
        if status.get("run_id") != str(credentials.run_id):
            raise ValueError("Asset Job identity changed outside its validation job")
        if credentials.manifest():
            await k8s.ensure_benchmark_job_secret(job.namespace, manifest, credentials.manifest())
        if not status["succeeded"] and not status["failed"]:
            return None
        log = await k8s.read_job_pod_logs(job.namespace, name)
        result = next(
            (
                json.loads(line.split("<<<ASSETS>>>", 1)[1])
                for line in reversed(log.splitlines())
                if "<<<ASSETS>>>" in line
            ),
            {},
        )
        if status["failed"] or result.get("error") or not result.get("weights_sha256"):
            reason = result.get("error") or "Missing asset result; inspect image, storage and tokenizer availability"
            raise ValueError(f"Asset check failed: {reason}")
        ids = await image_ids(k8s, job.namespace, f"job-name={name}")
        if not ids:
            raise ValueError("Asset check did not expose its image digest")
        result["image"] = pinned_image(
            manifest["spec"]["template"]["spec"]["containers"][0]["image"], next(iter(ids.values()))
        )
        async with await k8s._api_client() as api:
            pods = await client.CoreV1Api(api).list_namespaced_pod(job.namespace, label_selector=f"job-name={name}")
            nodes = {p.spec.node_name for p in pods.items if p.status.phase == "Succeeded" and p.spec.node_name}
        if len(nodes) != 1:
            raise ValueError("Cannot identify the node whose model assets were validated")
        result["node_name"] = next(iter(nodes))
        results[str(index)] = result
    return results


def restored_assets_match(current, previous):
    if not previous:
        return False
    # Older asset records already covered config.json in weights_sha256. Ignore only
    # the newly added per-file digest when restoring one of those records.
    normalized = {
        key: {k: v for k, v in asset.items() if k != "config_digest" or k in previous.get(key, {})}
        for key, asset in current.items()
    }
    return normalized == previous


def apply_pins(dep, assets):
    def pin(pool, asset):
        pool["image"] = asset["image"]
        revisions = {key.replace("_", "-"): asset[key] for key in ("revision", "tokenizer_revision") if asset.get(key)}
        if revisions:
            pool["engine_args"] = {**(pool.get("engine_args") or {}), **revisions}
            pool["vllm_extra_args"] = without_flags(pool.get("vllm_extra_args"), {f"--{k}" for k in revisions})

    if pd_serving.is_pd(dep):
        cfg = deepcopy(dep.pd_config)
        names = {
            f"--{key.replace('_', '-')}"
            for a in assets.values()
            for key in ("revision", "tokenizer_revision")
            if a.get(key)
        }
        dep.vllm_extra_args = without_flags(dep.vllm_extra_args, names)
        for i, role in enumerate(pd_serving.ROLES):
            pin(cfg[role], assets[str(i)])
            if cfg[role].get("pd_extras"):
                extras = cfg[role]["pd_extras"]
                extras["args"] = without_flags(extras.get("args"), names)
        dep.pd_config = cfg
    else:
        pool = {k: getattr(dep, k) for k in ("image", "engine_args", "vllm_extra_args")}
        pin(pool, assets["0"])
        for key, value in pool.items():
            setattr(dep, key, value)


async def apply(db, job, attempt, dep):
    k8s = await k8s_for_cluster(db, job.cluster_id)
    rollout = await k8s.create_or_patch(
        job.namespace, manifests(dep, job.conditions["time_limits"], assets=attempt.state.get("assets"))
    )
    if dep.router_stack_id:
        await llmd_stacks.apply_stack(db, await db.get(CustomLlmdStack, dep.router_stack_id))
    return rollout


async def observe(db, job, attempt, dep, candidate):
    from app.mcp.serving import Revision, diagnostics, rollout_status

    expected = {k: Revision(**v) for k, v in (attempt.state.get("rollout") or {}).items()}
    status = await rollout_status(str(dep.id), expected or None, None, db)
    roles = ["prefill", "decode"] if pd_serving.is_pd(dep) else ["aggregated"]
    if dep.router_stack_id:
        roles.append("router")
    evidence = {r: await diagnostics(str(dep.id), r, 80, None, db) for r in roles}
    return status, scrub(evidence, unpack(candidate))


async def collect_jobs(db, job, attempt, dep, candidate):
    from app.services.serving_diagnostics import workload_diagnostics

    k8s = await k8s_for_cluster(db, job.cluster_id)
    names = [name for name, _, _, _ in asset_jobs(dep, attempt, job.conditions)]
    runs = (
        await db.scalars(select(CustomBenchmarkRun).where(CustomBenchmarkRun.validation_attempt_id == attempt.id))
    ).all()
    names += [run.k8s_job_name for run in runs]
    evidence = {}
    for name in names:
        try:
            evidence[name] = await workload_diagnostics(k8s, job.namespace, name, router=False, tail_lines=80, job=True)
        except ApiException as exc:
            evidence[name] = {"error": f"Job diagnostics unavailable (HTTP {exc.status})"}
    return scrub(evidence, unpack(candidate))


async def prepare_benchmark(db, job, candidate, attempt, dep, key, params, kind="performance"):
    run_id = stable_id(attempt, key)
    run = await db.get(CustomBenchmarkRun, run_id)
    if run:
        return run
    from app.api.benchmarks import _serving_snapshot

    if params.get("capacity_search"):
        from app.services.capacity_identity import deployment_identity

        params = {**params, "_capacity_identity": deployment_identity(dep)}
    run = CustomBenchmarkRun(
        id=run_id,
        validation_id=job.id,
        validation_attempt_id=attempt.id,
        deployment_id=dep.id,
        cluster_id=job.cluster_id,
        model_name=job.model_name,
        k8s_namespace=job.namespace,
        k8s_job_name=job_name_for(run_id),
        tool="lm_eval" if kind == "accuracy" else f"{dep.engine}_serving",
        kind=kind,
        params=params,
        label=f"{candidate.name}: {key}"[:128],
        status="pending",
        created_by=job.created_by,
        ephemeral=False,
        serving_snapshot=_serving_snapshot(dep),
    )
    db.add(run)
    await db.flush()
    return run


async def benchmark(db, job, attempt, dep, run):
    from datetime import datetime

    from app.api.benchmarks import _build_bench_job, _router_target_base
    from app.db.models.custom_recipe_validation import CustomValidationCandidate
    from app.jobs.reconcile_benchmarks import _drive_job

    k8s = await k8s_for_cluster(db, job.cluster_id)
    routed = aggregated_router.routed(dep) and run.params.get("experiment") != "prefix_cache"
    target = (
        await _router_target_base(db, dep)
        if routed
        else f"http://{k8s_resource_names(dep)['service']}.{job.namespace}.svc.cluster.local:80"
    )
    pool = pd_serving.pool_row(dep, "decode") if pd_serving.is_pd(dep) else dep
    credentials = BenchmarkCredentials(run.id)
    api_key = serving_api_key(engine_flags(pool), pool.env)
    image = attempt.state.get("assets", {}).get("accuracy", {}).get("image") if run.kind == "accuracy" else None
    manifest = _build_bench_job(
        run, deployment=pool, target_base=target, api_key=api_key, credentials=credentials, image_override=image
    )
    manifest["spec"]["activeDeadlineSeconds"] = job.conditions["time_limits"]["measurement"]
    pod = manifest["spec"]["template"]["spec"]
    pod["tolerations"] = deepcopy(pool.tolerations or [])
    if run.kind == "accuracy":
        asset_source = Path(__file__).with_name("validation_asset_runner.py").read_text()
        verify = (
            "import json, os, runpy\n"
            f"scope = {{'__name__': 'asset_validation'}}; exec({asset_source!r}, scope)\n"
            "actual = scope['inspect_assets'](json.loads(os.environ['VALIDATION_ASSETS']))\n"
            "if actual['weights_sha256'] != os.environ['VALIDATION_DATASETS_SHA256']:\n"
            "    raise ValueError('Evaluation datasets changed after asset validation')\n"
            "path, _ = scope['tokenizer_location'](os.environ['BENCH_TOKENIZER'], "
            "os.environ.get('BENCH_TOKENIZER_REVISION') or None, os.environ.get('HF_HUB_OFFLINE') == '1')\n"
            "from transformers import AutoTokenizer\n"
            "import hashlib\n"
            "trust = os.environ.get('BENCH_VALIDATION_TRUST_REMOTE_CODE') == '1'\n"
            "tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=trust)\n"
            "actual_hash = hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()\n"
            "if actual_hash != os.environ['BENCH_VALIDATION_TOKENIZER_SHA256']:\n"
            "    raise ValueError('Evaluation tokenizer changed after validation')\n"
            "os.environ['BENCH_TOKENIZER'] = path\n"
            "runpy.run_path('/app/entrypoint.py', run_name='__main__')\n"
        )
        pod["containers"][0]["command"] = ["python", "-c", verify]
    labels = {"llm-ops/validation-attempt": str(attempt.id), "llm-ops/validation": str(job.id)}
    if pd_serving.is_pd(dep):
        labels[pd_serving.LABEL_PD_ROLE] = "decode"
    pod.setdefault("affinity", {})["podAffinity"] = {
        "requiredDuringSchedulingIgnoredDuringExecution": [
            {"topologyKey": "kubernetes.io/hostname", "labelSelector": {"matchLabels": labels}}
        ]
    }
    manifest["spec"]["template"]["metadata"].setdefault("labels", {})["llm-ops/validation-attempt"] = str(attempt.id)
    for container in pod["containers"]:
        # Match cache paths and credentials used by the CPU asset check. Never copy
        # credentials into history/YAML; use the runner Job's owned Secret.
        from app.services.pd_serving import env_item

        reserved = {e["name"] for e in container.get("env", [])} | {
            "BENCH_CAPACITY_SEARCH",
            "BENCH_CAPACITY_DEADLINE",
            "BENCH_CAPACITY_SCOPE",
            "BENCH_VALIDATION_WARMUP",
        }
        inherited = [env_item(k, v) for k, v in (pool.env or {}).items() if k not in reserved]
        container.setdefault("env", []).extend(credentials.protect_env(inherited))
        container["env"].append({"name": "BENCH_VALIDATION_WARMUP", "value": "1"})
        if options := run.params.get("capacity_search"):
            # The attempt controls the target and all GPU resources. Router-backed
            # measurements describe the complete endpoint; never assign them to a pool.
            def deadline(start, seconds):
                return (start if start.tzinfo else start.replace(tzinfo=UTC)).timestamp() + seconds

            limits = job.conditions["time_limits"]
            deadlines = [
                deadline(attempt.stage_started_at, limits["measurement"]),
                deadline(job.started_at, limits["total"]),
            ]
            if job.conditions.get("search") and attempt.purpose == "compare":
                deadlines.append(deadline(job.started_at, job.conditions["search"]["max_search_seconds"]))
            container["env"] += [
                {"name": "BENCH_CAPACITY_SEARCH", "value": json.dumps(options)},
                {"name": "BENCH_CAPACITY_DEADLINE", "value": str(min(deadlines) - 10)},
                {"name": "BENCH_CAPACITY_SCOPE", "value": "endpoint" if routed else "deployment"},
            ]
        if not routed and not pd_serving.is_pd(dep) and dep.replicas == 1 and dep.engine == "vllm":
            container["env"] = [e for e in container["env"] if e["name"] not in {"BENCH_ISOLATED", "BENCH_TOKEN_INPUT"}]
            container["env"] += [{"name": "BENCH_ISOLATED", "value": "1"}]
            # A shared tokenizer for alternate weights must remain authoritative.
            if not job.conditions.get("tokenizer"):
                container["env"] += [{"name": "BENCH_TOKEN_INPUT", "value": "1"}]

        asset = attempt.state.get("assets", {}).get("1" if pd_serving.is_pd(dep) else "0", {})
        if run.kind == "accuracy":
            container["env"] += [
                {
                    "name": "VALIDATION_ASSETS",
                    "value": json.dumps({"kind": "accuracy", "tasks": job.conditions["accuracy"]["tasks"]}),
                },
                {"name": "VALIDATION_DATASETS_SHA256", "value": attempt.state["assets"]["accuracy"]["weights_sha256"]},
            ]
            if asset.get("server_tokenizer"):
                container["env"] = [e for e in container["env"] if e["name"] != "BENCH_TOKENIZER"]
                container["env"].append({"name": "BENCH_TOKENIZER", "value": asset["server_tokenizer"]})
                container["env"] += [
                    {"name": "BENCH_TOKENIZER_REVISION", "value": asset.get("tokenizer_revision") or ""},
                    {"name": "BENCH_VALIDATION_TOKENIZER_SHA256", "value": asset["server_tokenizer_sha256"]},
                    {
                        "name": "BENCH_VALIDATION_TRUST_REMOTE_CODE",
                        "value": "1" if "--trust-remote-code" in engine_flags(pool) else "0",
                    },
                ]
        if (
            run.kind == "performance"
            and run.params.get("experiment") != "prefix_cache"
            and asset.get("tokenizer_sha256")
        ):
            names = {
                "BENCH_TOKENIZER",
                "BENCH_TOKENIZER_REVISION",
                "BENCH_VALIDATION_TOKENIZER_SHA256",
                "BENCH_VALIDATION_TRUST_REMOTE_CODE",
            }
            container["env"] = [e for e in container["env"] if e["name"] not in names]
            container["env"] += [
                {"name": "BENCH_TOKENIZER", "value": asset["tokenizer"]},
                {"name": "BENCH_TOKENIZER_REVISION", "value": asset.get("workload_tokenizer_revision") or ""},
                {"name": "BENCH_VALIDATION_TOKENIZER_SHA256", "value": asset["tokenizer_sha256"]},
                {
                    "name": "BENCH_VALIDATION_TRUST_REMOTE_CODE",
                    "value": "1" if "--trust-remote-code" in engine_flags(pool) else "0",
                },
            ]
        if job.conditions["airgap"]:
            names = {e["name"] for e in offline_env()}
            container["env"] = [e for e in container["env"] if e["name"] not in names] + offline_env()
    try:
        observed = await k8s.read_job_status(job.namespace, run.k8s_job_name)
    except ApiException as exc:
        if exc.status != 404:
            raise
        if run.status == "running":
            run.status, run.error_message, run.finished_at = (
                "failed",
                "Measurement Job disappeared",
                datetime.now(UTC),
            )
            return
        await k8s.create_job(job.namespace, manifest, secret=credentials.manifest())
        run.status, run.started_at = "running", datetime.now(UTC)
        return
    if observed.get("run_id") != str(run.id):
        raise ValueError("Measurement Job identity changed outside its validation job")
    if credentials.manifest():
        await k8s.ensure_benchmark_job_secret(job.namespace, manifest, credentials.manifest())
    await _drive_job(k8s, run)
    candidate = await db.get(CustomValidationCandidate, attempt.candidate_id)
    run.result = scrub(run.result, unpack(candidate))
    run.error_message = scrub(run.error_message, unpack(candidate))


async def cleanup(db, job, attempt, dep):
    """Return only after all owned Pods are gone, including terminating GPU Pods."""
    from app.clients.litellm import LiteLLMClient
    from app.db.models.custom_recipe_validation import CustomValidationCandidate

    if not dep and not attempt.state.get("registration_started"):
        return True
    if dep and "kv_observations" not in attempt.state:
        from app.jobs.observe_kv_capacity import retain_attempt

        await retain_attempt(attempt, dep)
    k8s = await k8s_for_cluster(db, job.cluster_id)
    if attempt.state.get("registration_started"):
        model_id = str(stable_id(attempt, "litellm"))
        litellm = LiteLLMClient()
        if any((m.get("model_info") or {}).get("id") == model_id for m in await litellm.get_model_info()):
            await litellm.delete_model(model_id)
    if dep:
        await k8s.delete(job.namespace, k8s_resource_names(dep))
        if dep.router_stack_id:
            stack = await db.get(CustomLlmdStack, dep.router_stack_id)
            if stack:
                argo, ns, _ = await argocd_placement_for(db, job.cluster_id)
                await argo.delete_application(ns, stack.argo_app_name)
                await k8s.delete(job.namespace, {"ingress": llmd_stacks.ingress_for(stack)["metadata"]["name"]})
                if await argo.get_application(ns, stack.argo_app_name):
                    return False
    runs = (
        await db.scalars(select(CustomBenchmarkRun).where(CustomBenchmarkRun.validation_attempt_id == attempt.id))
    ).all()
    for run in runs:
        if (run.params or {}).get("capacity_search") and run.status in ("pending", "running"):
            # A parent deadline can win the race against the next poll. Preserve
            # the last complete probe before deleting the runner Pod.
            from app.jobs.reconcile_benchmarks import _parse_result

            try:
                checkpoint = _parse_result(await k8s.read_job_pod_logs(job.namespace, run.k8s_job_name, tail_lines=4))
            except ApiException:
                checkpoint = None
            checkpoint = checkpoint or run.result or {}
            if progress := checkpoint.get("capacity_search"):
                if progress.get("stop_reason") == "searching":
                    reason = "cancelled" if attempt.error_code == "cancelled" else "time_budget"
                    checkpoint = {**checkpoint, "capacity_search": {**progress, "stop_reason": reason}}
                candidate = await db.get(CustomValidationCandidate, attempt.candidate_id)
                run.result = scrub(checkpoint, unpack(candidate))
        await k8s.delete_job(job.namespace, run.k8s_job_name)
        if run.status in ("pending", "running"):
            run.status = "cancelled"
    if dep:
        for name, _, _, _ in asset_jobs(dep, attempt, job.conditions):
            await k8s.delete_job(job.namespace, name)
    async with await k8s._api_client() as api:
        pods = await client.CoreV1Api(api).list_namespaced_pod(
            job.namespace, label_selector=f"llm-ops/validation-attempt={attempt.id}"
        )
        if pods.items:
            return False
    if dep:
        dep.validation_state, dep.status, dep.ready_replicas = "retired", "Stopped", 0
        dep.litellm_model_id = None
    return True


async def ensure_route_available(k8s, namespace, docs):
    """Refuse to shadow an existing ingress, including one in another namespace."""
    if not docs:
        return
    async with await k8s._api_client() as api:
        existing = await client.NetworkingV1Api(api).list_ingress_for_all_namespaces(_request_timeout=15)
    for doc in docs:
        desired_class = doc["spec"].get("ingressClassName")
        for ingress in existing.items:
            if ingress.metadata.namespace == namespace and ingress.metadata.name == doc["metadata"]["name"]:
                continue
            existing_class = ingress.spec.ingress_class_name or (ingress.metadata.annotations or {}).get(
                "kubernetes.io/ingress.class"
            )
            if desired_class and existing_class and desired_class != existing_class:
                continue
            for rule in ingress.spec.rules or []:
                for desired in doc["spec"].get("rules", []):
                    host = desired.get("host", "")
                    overlaps_host = (
                        not rule.host
                        or rule.host == host
                        or (rule.host.startswith("*.") and host.endswith(rule.host[1:]))
                    )
                    if not overlaps_host or not rule.http:
                        continue
                    for path in rule.http.paths or []:
                        for target in desired.get("http", {}).get("paths", []):
                            a, b = (path.path or "/").rstrip("/"), target.get("path", "/").rstrip("/")
                            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                                raise ValueError(
                                    "The publication host/path overlaps an existing Ingress: "
                                    f"{ingress.metadata.namespace}/{ingress.metadata.name}"
                                )


async def publish(db, job, attempt, dep):
    from app.clients.litellm import LiteLLMClient
    from app.db.models.custom_recipe_validation import CustomValidationCandidate
    from app.jobs.reconcile_deployments import _ensure_catalog
    from app.services.litellm_registration import registration

    k8s = await k8s_for_cluster(db, job.cluster_id)
    candidate = await db.get(CustomValidationCandidate, attempt.candidate_id)
    status, evidence = await observe(db, job, attempt, dep, candidate)
    if not status["ready"]:
        raise ValueError("The measured deployment revision is no longer ready for publication")
    attempt.diagnostics = evidence
    attempt.state = {
        **attempt.state,
        "effective_manifests": scrub(
            manifests(dep, job.conditions["time_limits"], assets=attempt.state.get("assets")), unpack(candidate)
        ),
    }
    # Only this final attempt is exposed. No existing alias registration is replaced.
    if dep.router_stack_id:
        stack = await db.get(CustomLlmdStack, dep.router_stack_id)
        docs = [llmd_stacks.ingress_for(stack)]
    else:
        docs = [d for d in manifests(dep, job.conditions["time_limits"], public=True) if d["kind"] == "Ingress"]
    await ensure_route_available(k8s, job.namespace, docs)
    await k8s.create_or_patch(job.namespace, docs)
    litellm = LiteLLMClient()
    model_id = str(stable_id(attempt, "litellm"))
    entry = next((m for m in await litellm.get_model_info() if (m.get("model_info") or {}).get("id") == model_id), None)
    if entry is None:
        pool = pd_serving.pool_row(dep, "decode") if pd_serving.is_pd(dep) else dep
        payload = {**registration(dep), "api_key": serving_api_key(engine_flags(pool), pool.env)}
        response = await litellm.create_model(**payload, model_id=model_id)
        if (response.get("model_info") or {}).get("id") != model_id:
            raise ValueError("LiteLLM did not preserve the requested registration identity")
    smoke = await litellm.smoke_chat(model_id)
    if not smoke.get("ok"):
        raise ValueError(smoke.get("reason", "Published deployment smoke test failed"))
    await _ensure_catalog(db, job.model_name, job.created_by)
    from app.jobs.observe_kv_capacity import retain_attempt

    await retain_attempt(attempt, dep)
    dep.litellm_model_id, dep.validation_state = model_id, "published"
    if dep.router_stack_id:
        stack.validation_private = False
    return smoke
