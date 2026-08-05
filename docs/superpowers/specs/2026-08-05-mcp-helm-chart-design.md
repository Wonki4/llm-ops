# MCP Server in the litellm-platform Helm Chart — Design

**Date:** 2026-08-05
**Status:** Approved (design)

## Goal

Deploy the standalone budget/usage MCP server (the `mcp/` service) as part of
the `litellm-platform` Helm chart, mirroring how `backend`/`frontend` are
templated, so a chart install brings the MCP up alongside the rest of the
platform. Deployment configuration only — no change to the MCP application code.

## Background — verified facts

- **Chart:** `deploy/helm/litellm-platform`. Per-component pattern: each
  component has a `{{- if .Values.<comp>.enabled }}` guard, a Deployment
  template, an entry in `templates/services.yaml`, an optional entry in
  `templates/ingress.yaml`, and a block in `values.yaml`
  (`enabled`, `replicaCount`, `image.{repository,tag,pullPolicy}`,
  `service.{type,port}`, `env`, `resources`).
- **MCP container:** `mcp/Dockerfile` runs `uvicorn app.main:app --host
  0.0.0.0 --port 8000` and `EXPOSE 8000`. The container listens on **8000**;
  the `8005` seen in `docker-compose.yml` is only a host→container port
  mapping (`"8005:8000"`), irrelevant in Kubernetes.
- **MCP ASGI app:** `mcp/app/main.py` exposes `app` (a Starlette
  `streamable_http_app`) with the MCP endpoint at `/mcp` and a health route at
  `/health` returning `{"status": "ok"}`.
- **MCP config** (`mcp/app/config.py`, `APP_` env prefix):
  - `APP_DATABASE_URL` — portal DB (`custom_*` tables).
  - `APP_LITELLM_DATABASE_URL` — LiteLLM DB (`LiteLLM_*` tables); **empty →
    falls back to `APP_DATABASE_URL`**.
  - `APP_KEY_JWT_SECRET` — must match the backend's `keys.py`
    `_KEY_JWT_SECRET` (default `"litellm-portal-key-sign"`).
- **DB in the chart:** the shared `database` deployment creates a single
  database named per `database.auth.database` (= `"litellm"` in `values.yaml`);
  the `backend` connects to it via the `litellm-platform.database*` helpers.
  The `litellm-helm` subchart owns the LiteLLM tables. In the default
  all-in-one install both table sets are reachable via the shared DB, so the
  MCP's `APP_LITELLM_DATABASE_URL` can default empty (fall back to
  `APP_DATABASE_URL`); split deployments override it.
- **Image naming:** `backend`/`frontend` use `llm-ops/backend` /
  `llm-ops/frontend` with tag `v0.0.1-rc1`, prefixed by `global.imageRegistry`
  via the `litellm-platform.image` helper.

## Decisions

1. **Port unified at 8000** — Service `port: 8000` → `targetPort: 8000`,
   `containerPort: 8000`. Every other service in this chart has
   `service.port == targetPort`; the MCP follows suit. In-cluster URL:
   `http://{fullname}-mcp:8000/mcp`.
2. **Ingress entry included but off by default** — an `ingress.mcp` block,
   rendered only when `ingress.enabled` (master, default `false`) **and**
   `ingress.mcp.enabled` are true. Unlike `frontend`/`backend` (which default
   their ingress on), `ingress.mcp.enabled` defaults **false**: the MCP is a
   sensitive authenticated endpoint, so enabling the platform ingress must not
   silently expose it. External exposure is an explicit opt-in; the MCP is
   always reachable in-cluster via its Service.
3. **Env kept as plaintext `values`** — the chart's existing convention (backend
   env lives in `values`, not a `Secret`); the MCP follows it. No new `Secret`
   resource in this iteration.

## Architecture

### New: `templates/mcp-deployment.yaml`

A Deployment mirroring `backend-deployment.yaml`:

```yaml
{{- if .Values.mcp.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "litellm-platform.fullname" . }}-mcp
  labels:
    app.kubernetes.io/component: mcp
    {{- include "litellm-platform.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.mcp.replicaCount }}
  selector:
    matchLabels:
      app.kubernetes.io/component: mcp
      {{- include "litellm-platform.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        app.kubernetes.io/component: mcp
        {{- include "litellm-platform.selectorLabels" . | nindent 8 }}
    spec:
      {{- with .Values.global.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: mcp
          image: {{ include "litellm-platform.image" (dict "registry" .Values.global.imageRegistry "repository" .Values.mcp.image.repository "tag" .Values.mcp.image.tag) | quote }}
          imagePullPolicy: {{ .Values.mcp.image.pullPolicy }}
          ports:
            - containerPort: 8000
              protocol: TCP
          env:
            - name: APP_DATABASE_URL
              value: {{ default (printf "postgresql+asyncpg://%s:%s@%s:%s/%s" (include "litellm-platform.databaseUser" .) (include "litellm-platform.databasePassword" .) (include "litellm-platform.databaseHost" .) (include "litellm-platform.databasePort" .) (include "litellm-platform.databaseName" .)) (index .Values.mcp.env "APP_DATABASE_URL") | quote }}
            - name: APP_LITELLM_DATABASE_URL
              value: {{ index .Values.mcp.env "APP_LITELLM_DATABASE_URL" | quote }}
            - name: APP_KEY_JWT_SECRET
              value: {{ index .Values.mcp.env "APP_KEY_JWT_SECRET" | quote }}
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            {{- toYaml .Values.mcp.resources | nindent 12 }}
{{- end }}
```

`APP_DATABASE_URL`'s default uses the exact same `database*` helper expression
as the backend (shared portal DB), overridable via `mcp.env.APP_DATABASE_URL`.
`APP_LITELLM_DATABASE_URL` defaults to the `values` entry (empty → MCP falls
back to `APP_DATABASE_URL`). `APP_KEY_JWT_SECRET` comes from `values` and must
equal the backend's signing secret.

### Modify: `templates/services.yaml`

Append an MCP Service block with a **leading `---` inside the `mcp.enabled`
guard**:

```yaml
{{- if .Values.mcp.enabled }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "litellm-platform.fullname" . }}-mcp
  labels:
    app.kubernetes.io/component: mcp
    {{- include "litellm-platform.labels" . | nindent 4 }}
spec:
  type: {{ .Values.mcp.service.type }}
  ports:
    - port: {{ .Values.mcp.service.port }}
      targetPort: 8000
      protocol: TCP
      name: http
  selector:
    app.kubernetes.io/component: mcp
    {{- include "litellm-platform.selectorLabels" . | nindent 4 }}
{{- end }}
```

Rationale for the leading `---` rather than the file's existing pairwise
`{{- if and .Values.A.enabled .Values.B.enabled }}---{{- end }}` idiom: the
pairwise idiom concatenates two documents without a separator if a middle
component is disabled (e.g. `prometheus` off while an earlier block is the last
rendered one), producing invalid multi-doc YAML. A leading `---` that renders
whenever `mcp.enabled` is safe in every case — it separates the MCP Service
from whatever precedes it, and a document that merely begins with `---` (when
the MCP Service is the only/first rendered doc) is valid YAML. This adds the
robust form for the new block without restructuring the existing blocks.

### Modify: `templates/ingress.yaml`

Append an MCP Ingress mirroring the backend one, gated on
`ingress.enabled && mcp.enabled && ingress.mcp.enabled`, with a **leading `---`
inside the guard** (same robustness rationale as the Service above):

```yaml
{{- if and .Values.ingress.enabled .Values.mcp.enabled .Values.ingress.mcp.enabled }}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "litellm-platform.fullname" . }}-mcp
  labels:
    app.kubernetes.io/component: mcp
    {{- include "litellm-platform.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- if .Values.ingress.className }}
  ingressClassName: {{ .Values.ingress.className | quote }}
  {{- end }}
  {{- with .Values.ingress.tls }}
  tls:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.mcp.host | quote }}
      http:
        paths:
          - path: {{ .Values.ingress.mcp.path | quote }}
            pathType: {{ .Values.ingress.mcp.pathType }}
            backend:
              service:
                name: {{ include "litellm-platform.fullname" . }}-mcp
                port:
                  number: {{ .Values.mcp.service.port }}
{{- end }}
```

### Modify: `values.yaml`

Add an `mcp` block (placed after `backend`, before `worker` for locality) and
an `ingress.mcp` sub-block (after `ingress.backend`):

```yaml
mcp:
  enabled: true
  replicaCount: 1
  image:
    repository: llm-ops/mcp
    tag: v0.0.1-rc1
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
    port: 8000
  env:
    APP_DATABASE_URL: ""            # empty -> shared portal DB (same as backend)
    APP_LITELLM_DATABASE_URL: ""    # empty -> MCP falls back to APP_DATABASE_URL
    APP_KEY_JWT_SECRET: "litellm-portal-key-sign"  # MUST match backend keys.py
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

```yaml
  # under ingress:, alongside frontend/backend
  mcp:
    # Off by default (unlike frontend/backend): the MCP is a sensitive
    # authenticated endpoint, so turning on the platform ingress must not
    # auto-expose it. In-cluster reachable via its Service regardless.
    enabled: false
    host: "portal-mcp.example.com"
    path: "/"
    pathType: Prefix
```

## Non-goals

- No change to the MCP application code (`mcp/`).
- No dedicated `Secret` for MCP env (the chart stores component env as plaintext
  `values`; the MCP follows that convention).
- No changes to `values-allinone.yaml` / `values-external.yaml` beyond what is
  needed — the MCP `values.yaml` defaults work in the all-in-one profile; a
  split-DB or external profile can override `mcp.env.APP_LITELLM_DATABASE_URL`.
- No proxy/gateway routing changes; the MCP is reached by its own Service (and
  optional Ingress).
- No autoscaling / PodDisruptionBudget (none of the existing components have
  them).

## Testing / Verification

- `helm dependency build deploy/helm/litellm-platform` (subchart present) then
  `helm lint deploy/helm/litellm-platform` — passes.
- `helm template t deploy/helm/litellm-platform` renders exactly one MCP
  Deployment and one MCP Service with `containerPort: 8000`, Service
  `port: 8000 → targetPort: 8000`, image `.../llm-ops/mcp:v0.0.1-rc1`, and the
  three `APP_*` env vars (with `APP_DATABASE_URL` defaulting to the shared-DB
  URL when `mcp.env.APP_DATABASE_URL` is blank).
- `helm template t deploy/helm/litellm-platform --set ingress.enabled=true`
  does **not** render the MCP Ingress (off by default); adding
  `--set ingress.mcp.enabled=true` renders it with host `portal-mcp.example.com`,
  backend Service `{release}-mcp` port `8000`.
- `helm template t deploy/helm/litellm-platform --set mcp.enabled=false` renders
  no MCP Deployment/Service/Ingress.
- YAML sanity: piping `helm template` output to a parser yields no document
  errors (correct `---` separators).
