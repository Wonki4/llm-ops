# Per-Stack llm-d Chart Source + EPP Image Override — Design

**Date:** 2026-07-10
**Status:** Approved (user: per-stack input; include EPP image override)

## Problem

The llm-d Helm chart source and the EPP image come only from global env-backed
settings:

- `settings.llmd_chart_repo` = `oci://registry.k8s.io/gateway-api-inference-extension/charts`,
  `llmd_chart_name` = `standalone`, `llmd_chart_version` = `v1.5.0`
- `settings.llmd_epp_image_registry` = `ghcr.io`,
  `llmd_epp_image_repository` = `llm-d/llm-d-router-endpoint-picker`,
  `llmd_epp_image_tag` = `v0.9.0`

In an air-gapped cluster both hosts are unreachable, so the ArgoCD Application
fails to pull the chart (and the EPP image), and llm-d never comes up. There is
no way to point these at an internal mirror without redeploying the portal with
new env vars.

## Decision

Make the chart source and EPP image **per-stack overrides**, entered on the
llm-d stack form, defaulting to the global settings values. Null/empty = use
the global default (today's behavior). Existing stacks and non-air-gapped
installs are unaffected.

### Data model (migration 039, revises 038_member_budget_boost)

Six nullable columns on `custom_llmd_stack`:

- `chart_repo VARCHAR(512) NULL`, `chart_name VARCHAR(256) NULL`,
  `chart_version VARCHAR(128) NULL`
- `epp_registry VARCHAR(256) NULL`, `epp_repository VARCHAR(256) NULL`,
  `epp_tag VARCHAR(128) NULL`

All nullable, no server defaults — NULL means "fall back to the corresponding
`settings.*`".

> **Migration ordering:** origin/main's head is now `038_member_budget_boost`,
> so this migration is `039_llmd_stack_chart_source` revising
> `038_member_budget_boost`. No collision remains.

### Rendering (`backend/app/api/llmd.py`)

- `_application_for(stack, ...)` passes
  `chart_repo = stack.chart_repo or settings.llmd_chart_repo` (and the same for
  name/version) into `build_argo_application` — signature unchanged (already
  kwargs).
- `_values_for(stack)` passes
  `epp_registry = stack.epp_registry or settings.llmd_epp_image_registry` (and
  repository/tag) into `build_llmd_values` — signature unchanged.
- `default_llmd_values` (the /default-values starter) keeps using the global
  EPP defaults (the form has no stack yet at that point; the override fields
  prefill from `chart-defaults` below).

### API (`backend/app/api/llmd.py`)

- `CreateLlmdStackRequest` gains six optional `str | None` fields
  (`chart_repo`, `chart_name`, `chart_version`, `epp_registry`,
  `epp_repository`, `epp_tag`); create stores each `(value or "").strip() or
  None`.
- `UpdateLlmdStackRequest` gains the same six; update applies each with the
  `if body.X is not None` guard (empty string clears → back to default), then
  re-renders `values_snapshot` and re-applies the Application (existing flow).
- `_serialize` returns the **effective** values (stack value or settings
  default) under the existing `chart_repo`/`chart_name`/`chart_version` keys and
  a new `epp_image` breakdown, so the detail/list shows what is actually
  deployed. Also return the raw per-stack overrides under `chart_overrides`
  (nullable) so the edit form can distinguish "using default" from "overridden".
- New `GET /api/admin/llmd-stacks/chart-defaults` → the six global defaults from
  settings `{chart_repo, chart_name, chart_version, epp_registry,
  epp_repository, epp_tag}`, so the create form can prefill.

### Frontend (`admin/llmd/new` + edit form)

- A collapsible **"차트 소스 / EPP 이미지 (에어갭)"** section with the six text
  inputs, prefilled from `chart-defaults` (create) or the stack's effective
  values (edit). Left at the defaults → the common case still submits nothing
  (or the defaults, harmlessly). For air-gap, the admin overwrites the repo/
  image with the internal mirror URL.
- `CreateLlmdStackBody` / `UpdateLlmdStackBody` and the hooks gain the six
  optional fields; a `useLlmdChartDefaults()` query hits the new endpoint.
- en/ko i18n for the section label, the six field labels, and a hint.

## Non-goals

- No per-cluster or global-UI chart config (per-stack only, per user choice).
- No mirroring of the model-server images the chart itself pulls beyond the EPP
  image the portal controls (the user's `values.yaml` covers any others).
- No validation that the URL is reachable (air-gap DNS/proxy is theirs to run);
  a bad URL surfaces as the ArgoCD Application's sync error, already shown.

## Compatibility

- Six columns nullable, NULL = global default → existing 4 stacks and
  non-air-gapped installs behave exactly as today.
- Overrides resolve at every create/update/render, so editing a stack's chart
  source re-applies on the next save.

## Verification

- Backend: new tests — `_application_for`/`_values_for` use the stack override
  when set and fall back to settings when null; create stores the overrides
  (empty→null); update clears on empty; `_serialize` returns effective values;
  `chart-defaults` returns the settings values. pytest 0 NEW failures
  (baseline 21), ruff 0 NEW (baseline 78). Migration applies (alembic → 039).
- Frontend: lint 0 NEW (baseline 4 errors/13 warnings), build passes; the
  section prefills and round-trips.
