# llm-d Ingress Companion — Design

**Date:** 2026-08-05
**Status:** Approved (design)

## Goal

When the portal deploys an llm-d router stack, it should also bring up an
Ingress in front of the router's Service, so the router is reachable without a
manual `kubectl` step. The Ingress is created for **every** stack, using
global defaults (no per-stack form fields).

## Background — verified facts

- **The `llm-d-router-standalone` v0.9.0 chart has no ingress values.** Its
  top-level values key is only `router` (`modelServers`, `extraServicePorts`,
  `proxy`, `epp`, `latencyPredictor`). The chart will not render an Ingress, so
  the portal must create one itself.
- **Router entry point (sidecar mode, our default):** the chart renders a
  single Service `<release>-epp` exposing port name `http` on **8081** (the
  Envoy sidecar's inference listener). The Helm release name in ArgoCD is the
  Application name, i.e. `stack.argo_app_name`, so the Service name is
  deterministic: `{argo_app_name}-epp`.
- **The portal already manages Ingresses directly.** The model-deployment path
  builds a `Deployment + Service + Ingress` trio and applies/deletes them on
  the target cluster via `K8sClient.create_or_patch()` / `K8sClient.delete()`.
  We reuse that exact house style.
- **The portal can reach the target cluster directly.**
  `k8s_for_cluster(db, stack.cluster_id)` returns a `K8sClient` bound to the
  cluster where the workload (and thus the Service) lives — distinct from
  `argocd_placement_for(...)`, which resolves the ArgoCD *host* cluster. A null
  `cluster_id` uses the portal's default kubeconfig (all-local).
- **`K8sClient` primitives already fit:** `create_or_patch(ns, [manifest])`
  upserts an `Ingress` (NetworkingV1Api); `delete(ns, {"ingress": name})` skips
  `None` entries and treats HTTP 404 as success.

## Decisions

1. **Lifecycle: the portal manages the Ingress directly** (not ArgoCD/GitOps).
   Applied right after the Application in the same `try/except`, deleted with
   the stack. Matches the model-deployment pattern. The Ingress is *not* pruned
   or self-healed by ArgoCD; the portal owns its lifecycle.
2. **Always created**, from global config defaults — no per-stack form fields.
3. **A non-empty ingress domain is enforced.** Host is always
   `{argo_app_name}.{domain}`, unique per stack, so multiple stacks never
   collide on a shared path. There is no host-less mode.

## Architecture

### New manifest builder — `app/services/llmd_manifests.py`

```python
def llmd_service_name(stack) -> str:
    """Router entry Service the chart renders in sidecar mode: <release>-epp."""
    return f"{stack.argo_app_name}-epp"


def build_llmd_ingress(
    stack,
    *,
    ingress_class: str,
    ingress_domain: str,
    ingress_path: str,
) -> dict:
    """An Ingress fronting the llm-d router's Envoy entry Service.

    Backend: Service ``{argo_app_name}-epp`` port name ``http`` (chart sidecar
    entry, 8081). Host is always ``{argo_app_name}.{domain}``. ``ingress_class``
    empty -> omit ``ingressClassName`` (cluster's default IngressClass).
    """
    host = f"{stack.argo_app_name}.{ingress_domain}"
    spec: dict = {
        "rules": [
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": ingress_path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": llmd_service_name(stack),
                                    "port": {"name": "http"},
                                }
                            },
                        }
                    ]
                },
            }
        ],
    }
    if ingress_class:
        spec["ingressClassName"] = ingress_class
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"{stack.argo_app_name}-ingress",
            "namespace": stack.namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED_BY},
        },
        "spec": spec,
    }
```

### Deterministic release name — `build_argo_application`

Add `"releaseName": stack.argo_app_name` under `spec.source.helm`, so
`<release>-epp` is guaranteed regardless of ArgoCD defaults. For already-applied
stacks this is a no-op (ArgoCD already uses the Application name as the release
name), so it does not trigger a recreate.

### Config — `app/config.py`

```python
# llm-d router Ingress (always created; the portal manages its lifecycle).
llmd_ingress_class: str = ""            # APP_LLMD_INGRESS_CLASS; empty -> cluster default IngressClass
llmd_ingress_domain: str = "llm-d.local"  # APP_LLMD_INGRESS_DOMAIN; host = {app}.{domain}
llmd_ingress_path: str = "/"            # APP_LLMD_INGRESS_PATH; pathType Prefix
```

`llmd_ingress_domain` is enforced non-empty: a blank value (operator sets
`APP_LLMD_INGRESS_DOMAIN=""`) is treated as unset and falls back to the default
via an `effective_ingress_domain` property (`self.llmd_ingress_domain.strip() or
"llm-d.local"`). The Ingress domain can never be empty.

### Lifecycle wiring — `app/api/llmd.py`

A helper mirrors `_values_for`:

```python
def _ingress_for(stack: CustomLlmdStack) -> dict:
    return build_llmd_ingress(
        stack,
        ingress_class=settings.llmd_ingress_class,
        ingress_domain=settings.effective_ingress_domain,
        ingress_path=settings.llmd_ingress_path or "/",
    )
```

- **`create_stack` / `update_stack`:** inside the same `try/except` that applies
  the Application, after `apply_application`, upsert the Ingress on the target
  cluster:
  ```python
  target_k8s = await k8s_for_cluster(db, stack.cluster_id)
  await target_k8s.create_or_patch(stack.namespace, [_ingress_for(stack)])
  ```
  Any failure raises 502 (same as the Application-apply failure path). The
  upsert is idempotent, so retries are safe.
- **`delete_stack`:** after deleting the Application, delete the Ingress
  best-effort on the target cluster:
  ```python
  target_k8s = await k8s_for_cluster(db, stack.cluster_id)
  await target_k8s.delete(stack.namespace, {"ingress": f"{stack.argo_app_name}-ingress"})
  ```
  `delete` already treats 404 as success and skips the `None` service/deployment
  entries.

`_serialize` gains an `ingress_host` field (`{argo_app_name}.{domain}`) so the
UI/API can show the reachable host. (Frontend display wiring is out of scope for
this spec beyond exposing the field.)

## Non-goals

- **No support for proxy `mode: service`.** We target the sidecar-mode entry
  Service `{argo_app_name}-epp:http`. If a user switches the proxy to a separate
  Deployment/Service via `helm_values`, the Ingress backend is not adjusted.
- **No ArgoCD/GitOps management of the Ingress** (no umbrella or raw chart). The
  portal owns create/update/delete.
- **No per-stack Ingress form fields** (host/class/path). Global config only.
- **No TLS / cert-manager annotations** in this iteration.
- **No frontend UI work** beyond the `ingress_host` field on the serializer.

## Multi-stack note

Because the domain is enforced non-empty and the host is
`{argo_app_name}.{domain}`, each stack gets a unique host — multiple stacks
coexist on one ingress controller without path collisions. Operators point a
wildcard `*.{domain}` (or per-host records) at the ingress controller; in
air-gap they override `APP_LLMD_INGRESS_DOMAIN` and `APP_LLMD_INGRESS_CLASS`.

## Testing

**Unit — `backend/tests/test_llmd_manifests.py`:**
- `build_llmd_ingress` → name `{app}-ingress`, namespace `stack.namespace`,
  backend Service `{app}-epp` port name `http`, host `{app}.{domain}`, path/pathType.
- `ingress_class=""` → no `ingressClassName` key; non-empty → set.
- `build_argo_application` now emits `spec.source.helm.releaseName == argo_app_name`.

**Unit — `backend/tests/test_config.py` (or alongside):**
- `effective_ingress_domain` returns default when `llmd_ingress_domain` is blank;
  returns the override otherwise.

**API — `backend/tests/test_llmd.py`:**
- `create_stack` upserts the Ingress: assert `create_or_patch` awaited with
  `stack.namespace` and a single manifest whose `kind == "Ingress"`, backend
  Service `{app}-epp`, host `{app}.<default-domain>`.
- `delete_stack` deletes the Ingress: assert `delete` awaited with
  `{"ingress": "{app}-ingress"}`.
- Mocks reuse the existing `argocd_placement_for` / `k8s_for_cluster` patch
  style already in `test_llmd.py`.

## Verification

- `cd backend && python -m pytest tests/test_llmd_manifests.py tests/test_llmd.py -q`
  — 0 new failures vs the `origin/main` baseline.
- `ruff check app/api/llmd.py app/services/llmd_manifests.py app/config.py`
- Manual (minikube `llmd-test`, ingress addon enabled): create a stack →
  `kubectl get ingress -n <ns>` shows `{app}-ingress` with host
  `{app}.llm-d.local` backed by Service `{app}-epp:http`; `curl` via the ingress
  reaches the router. Delete the stack → the Ingress is gone.
