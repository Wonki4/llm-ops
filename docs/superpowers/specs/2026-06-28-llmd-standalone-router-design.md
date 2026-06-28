# llm-d standalone router — design

**Date:** 2026-06-28
**Status:** Approved (pending spec review)
**Branch:** `feat/llmd-standalone-router` (stacked on `feat/llmd-serving-management` / PR #174)

## Problem

The portal has an "llm-d" feature (`custom_llmd_stack`) that, despite its name, does **not** deploy the llm-d project. It deploys the Kubernetes SIG **gateway-api-inference-extension (GIE)** `standalone` chart (`oci://registry.k8s.io/gateway-api-inference-extension/charts`, `standalone`, `v1.5.0`) — currently configured to bring up the GIE Endpoint Picker (EPP / inference scheduler) only, in front of already-running vLLM model servers.

The real **llm-d** project (latest `v0.8.1`) is a separate, larger distributed-inference stack. Its **llm-d-router** is "the intelligent entry point for inference requests" = an **Envoy proxy + an EPP that extends GIE** with llm-d-specific routing intelligence (KV-cache aware, prefix-cache aware, session-aware scorers; P/D disaggregation support). We want our portal's "llm-d" feature to deploy the *real* llm-d router, not vanilla GIE EPP.

## Decisions (from brainstorming)

- **Scope:** gateway layer only — the **router**. No model-serving migration; existing `custom_model_deployment` vLLM servers stay as-is and sit behind the router.
- **Mode:** **standalone** — Envoy proxy + EPP co-located in one pod, communicating over localhost. This removes the need for a Gateway API provider (kgateway/istio) and for `llm-d-infra` entirely. Effective scope is therefore **router-only**, not router+infra.
- **Approach: A — in-place upgrade of the existing GIE-standalone stack.** We already deploy the GIE `standalone` chart; llm-d's EPP is "GIE EPP extended." So the gap to the real router is a **config delta**, not a greenfield build:
  1. EPP image → llm-d's (`ghcr.io/llm-d/llm-d-router-endpoint-picker`)
  2. Envoy sidecar proxy → enabled
  3. llm-d scheduler config (scorer/filter plugins) → supplied
- **Environment:** build and verify on the local **M1 `portal-test` minikube** (no GPU). Air-gap production path documented but not deployed in this work.

## Key risk & gating spike (implementation step 1)

Approach A assumes the GIE `standalone` chart v1.5.0 can host the llm-d EPP image + sidecar proxy + plugin config. **Before any code change**, verify with `helm template` (rendering the chart locally) that the chart's values schema supports:

1. Overriding the EPP container image (registry/repo/tag).
2. Enabling the Envoy sidecar proxy.
3. Injecting an llm-d scheduler/plugins config (inline or mounted config file).

**Outcome gate:**
- All three supported → proceed with Approach A.
- Any one blocked → **fall back to Approach B** (deploy llm-d-router's own Kustomize/images as a separate ArgoCD source) and revisit the design before continuing.

The spike's rendered output becomes a regression fixture (see Testing).

## Architecture

```
inference request
      │
      ▼
┌──────────────────────────────┐   one pod, standalone mode
│  Envoy proxy ──ext-proc──▶ EPP│   (llm-d-router-endpoint-picker)
└──────────────────────────────┘
      │ routes to pods selected by endpointSelector
      ▼
existing vLLM serving deployments  (custom_model_deployment,
labelled  llm-ops/model-name=<target>,  targetPort 8000)
```

- No gateway provider, no `llm-d-infra`, no CRDs beyond what the standalone chart manages.
- Deployment path is **unchanged**: portal → `custom_llmd_stack.helm_values` → ArgoCD `Application` (`helm.valuesObject`) → GIE `standalone` chart. We change only the *values inside* the chart, not the plumbing.
- Router pod is CPU-schedulable → runs on the GPU-less M1 cluster.

## Components & changes

| File | Change |
|---|---|
| `backend/app/config.py` | Add EPP image settings: `llmd_epp_image` (default `ghcr.io/llm-d/llm-d-router-endpoint-picker`) + `llmd_epp_image_tag`. Air-gap overrides via these + existing `llmd_image_registry`. Chart repo/name/version unchanged (GIE `standalone` `v1.5.0`). |
| `backend/app/services/llmd_manifests.py` → `default_llmd_values()` | Replace the starter template with the real-router template: EPP image = llm-d's; Envoy sidecar proxy enabled; default llm-d scheduler config (prefix-cache + KV-cache + load-aware scorers). Keep `endpointSelector` (`llm-ops/model-name=<target>`), `targetPorts: 8000`, `modelServerType: vllm`. |
| `backend/app/services/llmd_manifests.py` → `build_llmd_values()` | Extend the thin base merged under user `helm_values` to also default the EPP image (user values still win). |
| `backend/app/api/llmd.py` | No endpoint changes. `default_values` endpoint returns the new template automatically. |
| `frontend/src/app/(app)/admin/llmd/*` | No schema change. Copy update to "Envoy + EPP standalone router"; detail page shows the EPP image alongside the existing chart name/version. |
| DB | **No migration.** `helm_values` JSONB already holds arbitrary values; only the *default* template changes. Existing stacks (0–few on local) keep their stored values. Optional: a "reset to current default" affordance — out of scope unless asked. |

## Data flow (verification scenario)

1. Create a stack in the portal targeting an existing serving deployment (`cpu-demo`, mock vLLM, `llm-ops/model-name` label).
2. ArgoCD syncs the standalone router Application.
3. Router pod reaches **1/1 (CPU)**.
4. EPP discovers the backend pods via `endpointSelector` on `targetPorts: 8000`.
5. Send an inference request to the router endpoint → Envoy applies EPP scoring → request routes to the vLLM pod.
6. Portal's applied-resources / manifest view renders the router resources.

## Error handling

- Reuse the existing `live_error` surfacing and ArgoCD create/update/sync error messages from the #173/#174 work.
- Plugin-config schema mismatch or image-pull failure surfaces as an ArgoCD sync error and/or router pod status (`ImagePullBackOff`, `CrashLoopBackOff`) — both already visible through existing paths.
- Air-gap: a missing mirrored image surfaces as `ImagePullBackOff` on the router pod.

## Testing

- **Unit:** `default_llmd_values()` and `build_llmd_values()` — assert the EPP image, sidecar-proxy enable, and scorer config land where expected, and that user `helm_values` override the base.
- **Render regression:** snapshot the spike's `helm template` output as a fixture; assert our rendered values produce the expected EPP image + proxy + plugin config.
- **Live (M1):** the §"Data flow" scenario end-to-end.
- **Honest limitation:** mock/CPU vLLM exposes no KV-cache metrics, so **KV-aware scoring efficacy cannot be measured locally**. Local verification covers "router stands up + requests route to the backend." Prefix-cache scoring is request-derived and may partially exercise. Full scorer validation needs real vLLM on GPU (out of scope here).

## Out of scope

- llm-d-modelservice (P/D disaggregation, LeaderWorkerSet model serving) — a future, separate spec gated on GPU availability.
- Gateway-mode router (HTTPRoute / InferencePool on a shared Gateway via `llm-d-infra`) — production path, not this work.
- Migrating existing `custom_model_deployment` serving to llm-d-native serving.

## Air-gap notes (documented, not deployed here)

- Mirror to the internal registry: the GIE `standalone` chart, the llm-d EPP image (`llm-d-router-endpoint-picker`), and the Envoy proxy image.
- Point `llmd_chart_repo`, `llmd_image_registry`, and the new `llmd_epp_image*` settings at the internal mirror. See [[airgap-no-external-runtime-deps]].
