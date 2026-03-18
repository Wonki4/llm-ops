{{/*
Expand the name of the chart.
*/}}
{{- define "litellm-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "litellm-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "litellm-platform.labels" -}}
helm.sh/chart: {{ include "litellm-platform.name" . }}
{{ include "litellm-platform.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "litellm-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "litellm-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Database host for internal/external mode.
*/}}
{{- define "litellm-platform.databaseHost" -}}
{{- if .Values.database.enabled -}}
{{ include "litellm-platform.fullname" . }}-db
{{- else -}}
{{ .Values.database.external.host }}
{{- end -}}
{{- end }}

{{/*
Database port for internal/external mode.
*/}}
{{- define "litellm-platform.databasePort" -}}
{{- if .Values.database.enabled -}}
{{ .Values.database.service.port | toString }}
{{- else -}}
{{ .Values.database.external.port | toString }}
{{- end -}}
{{- end }}

{{/*
Database name for internal/external mode.
*/}}
{{- define "litellm-platform.databaseName" -}}
{{- if .Values.database.enabled -}}
{{ .Values.database.auth.database }}
{{- else -}}
{{ .Values.database.external.database }}
{{- end -}}
{{- end }}

{{/*
Database username for internal/external mode.
*/}}
{{- define "litellm-platform.databaseUser" -}}
{{- if .Values.database.enabled -}}
{{ .Values.database.auth.username }}
{{- else -}}
{{ .Values.database.external.username }}
{{- end -}}
{{- end }}

{{/*
Database password for internal/external mode.
*/}}
{{- define "litellm-platform.databasePassword" -}}
{{- if .Values.database.enabled -}}
{{ .Values.database.auth.password }}
{{- else -}}
{{ .Values.database.external.password }}
{{- end -}}
{{- end }}

{{/*
Backend internal service URL.
*/}}
{{- define "litellm-platform.backendUrl" -}}
http://{{ include "litellm-platform.fullname" . }}-backend:{{ .Values.backend.service.port }}
{{- end }}

{{/*
Keycloak internal issuer URL.
*/}}
{{- define "litellm-platform.keycloakInternalIssuer" -}}
http://{{ include "litellm-platform.fullname" . }}-keycloak:{{ .Values.keycloak.service.port }}/realms/{{ .Values.keycloak.realm }}
{{- end }}

{{/*
Keycloak internal JWKS URL.
*/}}
{{- define "litellm-platform.keycloakInternalJwksUri" -}}
{{ include "litellm-platform.keycloakInternalIssuer" . }}/protocol/openid-connect/certs
{{- end }}
