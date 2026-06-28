# llm-d standalone router — spike findings (2026-06-28)

Chart: `oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone` v1.5.0
Digest: `sha256:cf5525592a23a4e95df22eafdc9338802cc43f01ebb1d9519a3f39d8f870b242`

## Confirmed value keys

- **EPP image override key**: `inferenceExtension.image.{registry,repository,tag}` [CONFIRMED]
  - Default: `registry: registry.k8s.io`, `repository: gateway-api-inference-extension/epp`, `tag: v1.5.0`
  - Overriding to `registry: ghcr.io`, `repository: llm-d/llm-d-router-endpoint-picker`, `tag: v0.8.1` renders correctly — confirmed at `image: ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1` in the Deployment.

- **Envoy sidecar proxy**: `inferenceExtension.sidecar.enabled: true` [CONFIRMED — ENABLED BY DEFAULT]
  - The sidecar is on by default (`enabled: true` in the chart's default values). No override needed to enable it.
  - Sidecar image key: `inferenceExtension.sidecar.image` (default `docker.io/envoyproxy/envoy:distroless-v1.33.2`)
  - Config is supplied via `inferenceExtension.sidecar.configMap.data` (inline YAML block keyed by filename).

- **Scheduler/plugins config**: `inferenceExtension.pluginsConfigFile` (filename, default `"default-plugins.yaml"`) [CONFIRMED]
  - The chart creates a ConfigMap named `<release>-epp` whose `data` key is the filename from `pluginsConfigFile`.
  - The ConfigMap data content is an `EndpointPickerConfig` manifest (kind: `EndpointPickerConfig`, apiVersion: `inference.networking.x-k8s.io/v1alpha1`).
  - The EPP container is launched with `--config-file /config/<pluginsConfigFile>`, mounting the ConfigMap at `/config`.
  - To supply llm-d scheduler/scorer plugins, override the ConfigMap data under `inferenceExtension.pluginsConfigFile` and provide a custom `EndpointPickerConfig` block. The chart does NOT expose a top-level `pluginsConfig:` key — the config is the raw content of the named file key inside `inferenceExtension.sidecar.configMap`... **correction**: the plugins ConfigMap is separate from the envoy ConfigMap. The plugins content is baked as a hardcoded template in `templates/inferenceextension.yaml` and populated from `inferenceExtension.metricsDataSource` values for the default scorers. To inject llm-d scorers, supply a custom ConfigMap or use a Helm post-render hook — the chart does not expose a free-form `pluginsConfig` values key for arbitrary plugin entries.

## Actual default plugins ConfigMap content (from `helm show values`)

```yaml
# rendered ConfigMap data section (lines 21-42 of rendered output)
data:
  default-plugins.yaml: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
    - type: queue-scorer
    - type: kv-cache-utilization-scorer
    - type: prefix-cache-scorer
    - type: metrics-data-source
      parameters:
        scheme: "http"
        path: "/metrics"
        insecureSkipVerify: true
    - type: core-metrics-extractor
    schedulingProfiles:
    - name: default
      plugins:
      - pluginRef: queue-scorer
        weight: 2
      - pluginRef: kv-cache-utilization-scorer
        weight: 2
      - pluginRef: prefix-cache-scorer
        weight: 3
```

The chart's template hardcodes this structure from the `metricsDataSource` values block — there is no `pluginsConfig` values key that accepts an arbitrary plugin list. Tasks 3–4 must either:
(a) ship a separate ConfigMap via ArgoCD and reference it, or
(b) use a Helm post-render hook / Kustomize patch to overwrite the ConfigMap data.

## Key excerpts from rendered output

**Image override confirmed** (line 388 of `/tmp/router-rendered.yaml`):
```
- name: epp
  image: ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.8.1
  imagePullPolicy: Always
  args:
      - --endpoint-selector
      - "llm-ops/model-name=opt-125m"
      - --endpoint-target-ports
      - "8000"
      - --config-file
      - "/config/default-plugins.yaml"
```

**Sidecar confirmed** (line 339):
```
- name: envoy-sidecar
  image: docker.io/envoyproxy/envoy:distroless-v1.33.2
```

**Plugins volume wiring** (lines 431–443):
```
volumeMounts:
  - name: plugins-config-volume
    mountPath: "/config"
volumes:
  - name: plugins-config-volume
    configMap:
      name: router-epp
```

## Decision: PROCEED with Approach A (with one caveat)

All three override axes work via the chart's values:
1. EPP image — fully overridable via `inferenceExtension.image.{registry,repository,tag}`. CONFIRMED.
2. Envoy sidecar — already enabled by default; config overridable via `inferenceExtension.sidecar.configMap.data`. CONFIRMED.
3. Scheduler/plugins config — the ConfigMap content is templated from chart values, but the plugin list is not freely injectable via values. The workaround is clean: ship a separate `EndpointPickerConfig` ConfigMap via ArgoCD and reference it, or patch via Kustomize. The `--config-file` flag path is fully controllable. This is a minor values gap, not a showstopper.

**Recommendation: PROCEED with Approach A.** The chart cleanly supports (a) and (b). For (c), use a sibling ArgoCD resource (plain ConfigMap) with the llm-d scorer plugin list, and patch the chart's ConfigMap name via a Kustomize strategic merge or a second Helm values override if the chart exposes `inferenceExtension.endpointsServer.pluginsConfigMapName` (to be verified in Tasks 3–4).

Tasks 3–4 should use these confirmed keys:
- `inferenceExtension.image.registry`
- `inferenceExtension.image.repository`
- `inferenceExtension.image.tag`
- `inferenceExtension.sidecar.enabled` (leave `true`, already default)
- `inferenceExtension.sidecar.configMap.data` (to override Envoy config if needed)
- `inferenceExtension.pluginsConfigFile` (filename reference; pair with a separately managed ConfigMap for llm-d plugin content)
