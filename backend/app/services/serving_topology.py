"""Read-only routing graph, rooted in LiteLLM's model groups and deployment IDs."""

import asyncio
import json
import logging
from collections import OrderedDict, defaultdict
from time import monotonic
from urllib.parse import urlsplit

from kubernetes_asyncio import client

from app.services.llmd_links import selector_matches
from app.services.llmd_manifests import llmd_service_name, stack_selector
from app.services.model_deployment_manifests import LABEL_MODEL
from app.services.pd_serving import LABEL_ROLE

logger = logging.getLogger(__name__)


def _controller(metadata):
    return next((ref for ref in metadata.owner_references or [] if ref.controller), None)


def _apps_owner(ref, kind):
    return ref is not None and ref.kind == kind and ref.api_version.split("/")[0] in {"apps", "extensions"}


async def _replicaset_owners(api, namespace):
    try:
        sets = await client.AppsV1Api(api).list_namespaced_replica_set(namespace, _request_timeout=8)
    except Exception as exc:
        # Pod routing remains useful if the cluster identity cannot list ReplicaSets.
        logger.info("ReplicaSet owners unavailable in %s (%s)", namespace, type(exc).__name__)
        return {}
    return {rs.metadata.uid: _controller(rs.metadata) for rs in sets.items}


def _pod_owner(metadata, replica_sets):
    ref = _controller(metadata)
    parent = replica_sets.get(ref.uid) if _apps_owner(ref, "ReplicaSet") else ref
    return {
        "controller": f"{ref.kind}/{ref.name}" if ref else None,
        "deployment": (parent.uid, parent.name) if _apps_owner(parent, "Deployment") else None,
    }


async def read_routes(k8s, namespace):
    """Keep only routing fields. Never send pod specs or LiteLLM credentials to the UI."""
    async with await k8s._api_client() as api:
        core = client.CoreV1Api(api)
        net = client.NetworkingV1Api(api)
        ingresses, services = await asyncio.gather(
            net.list_namespaced_ingress(namespace, _request_timeout=8),
            core.list_namespaced_service(namespace, _request_timeout=8),
        )
    paths = []
    for ingress in ingresses.items:
        for rule in ingress.spec.rules or []:
            for path in (rule.http.paths if rule.http else []) or []:
                if path.backend.service:
                    paths.append(
                        (
                            ingress.metadata.name,
                            (rule.host or "").lower(),
                            path.path or "/",
                            path.path_type,
                            path.backend.service.name,
                        )
                    )
    return {
        "paths": paths,
        "services": {s.metadata.name: s.spec.selector or {} for s in services.items},
    }


async def read_workloads(k8s, namespace):
    async with await k8s._api_client() as api:
        pods, replica_sets = await asyncio.gather(
            client.CoreV1Api(api).list_namespaced_pod(namespace, _request_timeout=8),
            _replicaset_owners(api, namespace),
        )
    return {
        "pod_owners": {p.metadata.name: _pod_owner(p.metadata, replica_sets) for p in pods.items},
        "pods": [
            (
                p.metadata.name,
                p.metadata.labels or {},
                p.status.phase,
                any(c.type == "Ready" and c.status == "True" for c in p.status.conditions or []),
                bool(p.metadata.deletion_timestamp),
            )
            for p in pods.items
        ],
    }


def _endpoint(raw):
    if not isinstance(raw, str):
        return None
    try:
        url = urlsplit(raw)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return None
        url.port  # validate before displaying
        return url._replace(query="", fragment="")
    except ValueError:
        return None


class NamespaceSnapshots:
    """Share successful observations briefly across requests within one API worker."""

    def __init__(self, reader):
        self.reader = reader
        self.cache = OrderedDict()
        self.pending = {}
        self.limit = asyncio.Semaphore(4)

    async def read(self, scope):
        revision = scope.cluster.updated_at if scope.cluster else None
        key = (scope.cluster_id, scope.namespace, revision)
        cached = self.cache.get(key)
        if cached and cached[0] > monotonic():
            self.cache.move_to_end(key)
            return cached[1]
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(self._load(key, scope))
        return await asyncio.shield(self.pending[key])

    async def _load(self, key, scope):
        try:
            async with self.limit:
                data = await asyncio.wait_for(self.reader(scope.client(), scope.namespace), timeout=12)
            self.cache[key] = (monotonic() + 30, data)
            self.cache.move_to_end(key)
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return data
        finally:
            self.pending.pop(key, None)


# A provider protocol alone (notably openai/*) does not identify the host of a custom endpoint.
_CLOUD_PROVIDERS = {
    "openai": "OpenAI", "text-completion-openai": "OpenAI", "anthropic": "Anthropic",
    "azure": "Azure OpenAI", "azure_ai": "Azure AI", "bedrock": "Amazon Bedrock",
    "bedrock_converse": "Amazon Bedrock", "sagemaker": "Amazon SageMaker", "vertex_ai": "Vertex AI",
    "gemini": "Google Gemini", "groq": "Groq", "mistral": "Mistral AI", "cohere": "Cohere",
    "cohere_chat": "Cohere", "deepseek": "DeepSeek", "openrouter": "OpenRouter",
    "together_ai": "Together AI", "fireworks_ai": "Fireworks AI", "xai": "xAI",
}
_CLOUD_HOSTS = {
    "api.openai.com": "OpenAI", "api.anthropic.com": "Anthropic", "api.groq.com": "Groq",
    "api.mistral.ai": "Mistral AI", "generativelanguage.googleapis.com": "Google Gemini",
    "api.cohere.com": "Cohere", "api.deepseek.com": "DeepSeek", "openrouter.ai": "OpenRouter",
    "api.together.xyz": "Together AI", "api.fireworks.ai": "Fireworks AI", "api.x.ai": "xAI",
}


def _cloud_provider(params, info, url):
    if params.get("api_base"):
        if url is None:
            return None
        host = url.hostname.rstrip(".")
        if host.endswith(".openai.azure.com"):
            return "Azure OpenAI"
        if host.endswith("-aiplatform.googleapis.com") or host == "aiplatform.googleapis.com":
            return "Vertex AI"
        if host.startswith("bedrock-runtime.") and host.endswith(".amazonaws.com"):
            return "Amazon Bedrock"
        return _CLOUD_HOSTS.get(host)
    prefix, separator, _ = (params.get("model") or "").partition("/")
    provider = params.get("custom_llm_provider") or info.get("litellm_provider") or (prefix if separator else None)
    return _CLOUD_PROVIDERS.get(provider)


def _ingress_rank(host, path, rule_host, rule_path, path_type):
    if not rule_host:
        # A catch-all ingress alone is no evidence that LiteLLM points at this cluster.
        return None
    if rule_host.startswith("*."):
        if host.partition(".")[2] != rule_host[2:]:
            return None
    elif rule_host and host != rule_host:
        return None
    if path_type == "Exact" and path != rule_path:
        return None
    if path_type == "Prefix":
        prefix = rule_path.rstrip("/")
        if path != prefix and not path.startswith(prefix + "/"):
            return None
    elif path_type != "Exact":
        return None
    return (int(host == rule_host), len(rule_path.rstrip("/")), int(path_type == "Exact"))


def _pod_status(phase, ready, terminating):
    if terminating:
        return "Terminating"
    if ready:
        return "Ready"
    return "notReady" if phase == "Running" else phase or "Unknown"


def _route_indexes(snapshots):
    by_host = defaultdict(list)
    by_service = defaultdict(list)
    for (cid, ns), data in snapshots.items():
        for ingress, rule_host, path, typ, svc in data["paths"]:
            if rule_host:
                by_host[rule_host].append((cid, ns, ingress, path, typ, svc))
        for svc in data["services"]:
            by_service[svc, ns].append(cid)
    return by_host, by_service


def _route_targets(url, by_host, by_service):
    host = url.hostname.rstrip(".")
    parts = host.split(".")
    if parts[2:] in (["svc"], ["svc", "cluster", "local"]):
        direct = {(cid, parts[1], None, parts[0], None) for cid in by_service.get((parts[0], parts[1]), ())}
        if direct:
            return direct
    matches = []
    wildcard = "*." + host.partition(".")[2]
    for rule_host in (host, wildcard):
        for cid, ns, ingress, path, typ, svc in by_host.get(rule_host, ()):
            rank = _ingress_rank(host, url.path or "/", rule_host, path, typ)
            if rank is not None:
                matches.append((rank, cid, ns, ingress, svc, f"{rule_host}{path}"))
    best = max((m[0] for m in matches), default=None)
    return {(cid, ns, ing, svc, rule) for rank, cid, ns, ing, svc, rule in matches if rank == best}


class TopologyGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = set()
        self.ownership_edges = set()

    def add(self, kind, identity, label, *, detail=None, status=None, scope=None, ref=None):
        key = json.dumps([kind, *identity], separators=(",", ":"))
        if key not in self.nodes:
            self.nodes[key] = dict(id=key, kind=kind, label=label, detail=detail, status=status, scope=scope, ref=ref)
        return key

    def result(self):
        return {
            "nodes": list(self.nodes.values()), "edges": sorted(self.edges),
            "ownership_edges": sorted(self.ownership_edges),
        }


def build_topology(models, catalog, owners, stacks, snapshots, *, litellm_available=True):
    graph = TopologyGraph()
    add, nodes, edges = graph.add, graph.nodes, graph.edges
    entries = {}

    def group(name):
        return add("model", [name], name, detail=catalog.get(name))

    routers = {(str(s.cluster_id) if s.cluster_id else None, s.namespace, llmd_service_name(s)): s for s in stacks}
    owners_by_id = {}
    for owner in owners:
        if owner["model_id"]:
            owners_by_id.setdefault(owner["model_id"], []).append(owner)
    seen = set()
    expanded = set()
    target_cache = {}
    by_host, by_service = _route_indexes(snapshots)
    for row in models:
        name = row.get("model_name")
        info = row.get("model_info") or {}
        mid = info.get("id")
        if not name or not mid:
            continue
        seen.add(mid)
        params = row.get("litellm_params") or {}
        url = _endpoint(params.get("api_base"))
        records = owners_by_id.get(mid, [])
        record = records[0] if len(records) == 1 else None
        reg = add(
            "registration", [mid], mid, detail=url.geturl() if url else None, ref=record["ref"] if record else None
        )
        nodes[reg]["upstream_model"] = params.get("model")
        nodes[reg]["deployment"] = record["deployment"] if record else None
        edges.add((group(name), reg))
        # Compare the base URL against observed Ingress rules. A qualified Kubernetes
        # Service DNS name can also bypass ingress entirely.
        if url and url not in target_cache:
            target_cache[url] = _route_targets(url, by_host, by_service)
        targets = target_cache.get(url, set())
        provider = None if targets else _cloud_provider(params, info, url)
        if provider:
            nodes[reg].update(origin="cloud", cloud_provider=provider)
            continue
        nodes[reg]["origin"] = "cluster" if targets or records else "unknown"
        locations = {(cid, ns) for cid, ns, *_ in targets}
        if not locations:
            locations = {(r["cluster_id"], r["namespace"]) for r in records if r.get("namespace")}
        nodes[reg]["locations"] = [
            {"cluster_id": cid, "namespace": ns}
            for cid, ns in sorted(locations, key=lambda loc: (loc[0] or "", loc[1]))
        ]
        if len(targets) != 1:
            nodes[reg]["status"] = "ambiguous" if targets else "unresolved"
            continue
        cid, ns, ingress, svc, rule = next(iter(targets))
        data = snapshots[cid, ns]
        scope = f"{data['cluster']} / {ns}"
        parent = reg
        if ingress:
            parent = add("ingress", [cid, ns, ingress, svc, rule], ingress, detail=rule, scope=scope)
            edges.add((reg, parent))
        stack = routers.get((cid, ns, svc))
        service = add_service(graph, cid, ns, svc, stack, data, expanded)
        edges.add((parent, service))
        kind, entry_name = ("ingress", ingress) if ingress else ("service", svc)
        key = (cid, ns, kind, entry_name)
        if key not in entries:
            entries[key] = {
                "cluster": cid or "default", "ns": ns, "scope": scope, "kind": kind, "name": entry_name,
                "root_kind": "ingress" if ingress else nodes[service]["kind"], "registrations": set(),
            }
        entries[key]["registrations"].add(reg)

    for owner in owners:
        mid = owner["model_id"]
        if mid in seen:
            continue
        if mid:
            status = "stale" if litellm_available else "unverified"
        else:
            status = "unregistered"
        stale = add(
            "registration",
            [mid] if mid else ["unregistered", owner.get("owner_id") or owner["ref"] or owner["deployment"]],
            mid or owner["deployment"],
            detail=owner["deployment"] if mid else None,
            status=status,
            ref=owner["ref"],
        )
        nodes[stale]["origin"] = "cluster"
        if owner.get("namespace"):
            location = {"cluster_id": owner["cluster_id"], "namespace": owner["namespace"]}
            locations = nodes[stale].setdefault("locations", [])
            if location not in locations:
                locations.append(location)
        edges.add((group(owner["name"]), stale))
    for name in catalog:
        group(name)
    return {
        **graph.result(),
        "entries": [{**e, "registrations": sorted(e["registrations"])} for e in entries.values()],
    }


def add_service(graph, cid, ns, svc, stack, data, expanded):
    add, nodes, edges = graph.add, graph.nodes, graph.edges
    ownership_edges = graph.ownership_edges
    scope = f"{data['cluster']} / {ns}"
    exists = svc in data["services"]
    service = add(
        "router" if stack else "service",
        [cid, ns, svc],
        stack.name if stack else svc,
        detail=svc if stack else None,
        scope=scope,
        ref=str(stack.id) if stack else None,
    )
    if service in expanded:
        return service
    expanded.add(service)
    if not exists:
        nodes[service]["status"] = "missing"
        return service
    selector = stack_selector(stack.values_snapshot or stack.helm_values or {}) if stack else data["services"][svc]
    if not selector:
        nodes[service]["status"] = "noSelector"
        return service
    if "pods" not in data:
        return service
    count = 0
    for pod, labels, phase, ready, terminating in data["pods"]:
        if selector_matches(selector, labels):
            info = " · ".join(filter(None, [labels.get(LABEL_MODEL), labels.get(LABEL_ROLE)]))
            leaf = add(
                "pod",
                [cid, ns, pod],
                pod,
                scope=scope,
                detail=info or None,
                status=_pod_status(phase, ready, terminating),
            )
            edges.add((service, leaf))
            owner = data["pod_owners"].get(pod)
            if owner:
                nodes[leaf]["controller"] = owner["controller"]
                if owner["deployment"]:
                    uid, deployment_name = owner["deployment"]
                    workload = add("workload", [cid, ns, uid], deployment_name, scope=scope)
                    ownership_edges.add((leaf, workload))
            count += 1
    if not count:
        nodes[service]["status"] = "noPods"
    return service
