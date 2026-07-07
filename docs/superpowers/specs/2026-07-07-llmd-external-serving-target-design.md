# External Servings as llm-d Target Models — Design

**Date:** 2026-07-07
**Status:** Approved

## Problem

The llm-d stack creation form (`admin/llmd/new`) only offers **portal-created**
serving deployments (`useModelDeployments()`) as the target model the EPP router
fronts. Externally-deployed vLLM/SGLang servings — surfaced by the serving
discovery feature (`useExternalServings()` → `/api/model-deployments/external`)
— cannot be selected. Operators who run their servings outside the portal have
no way to put an llm-d router in front of them.

## Key mechanism (why this is small)

The llm-d router selects backend pods via `endpointSelector` in the Helm
`valuesObject`. `default_llmd_values` prefills it as
`llm-ops/model-name=<target_model_name>` — the label portal deployments carry.
Crucially, `endpointSelector` is **only a form-prefill default**:
`build_llmd_values` (rendered on every create/update) merges just a thin base
(EPP image) under the user's `helm_values` and never re-derives the selector.
So `target_model_name` is effectively **display/metadata + prefill seed**; the
real pod selection lives in the user-editable `helm_values` (values.yaml).

External servings do NOT carry the `llm-ops/model-name` label — they have their
own arbitrary labels (already exposed as `ExternalServing.labels`). So enabling
external targets is a **form-prefill change plus one backend parameter**, with
no schema/migration.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Pod selection for external servings | User picks which of the serving's labels to select on; the chosen `key=value` becomes the `endpointSelector` |
| Target-model picker UI | One dropdown with two `<optgroup>`s: "Portal deployments" / "External servings" |
| Namespace/cluster for external target | Auto-fill the stack's namespace + cluster from the picked external serving (router must run where the pods are) — user-overridable |

## Architecture

Mostly frontend. One backend addition: `default_llmd_values` (and the
`/default-values` endpoint) accept an optional explicit `endpoint_selector`; when
provided it is used verbatim for `endpointSelector`, else the existing
`llm-ops/model-name=<target_model_name>` default applies. No new DB column: the
stack stores `target_model_name` (display) as today, and the selector lives in
the rendered `helm_values`.

## Components

### Backend

**`services/llmd_manifests.py`** — `default_llmd_values` gains a keyword-only
`endpoint_selector: str | None = None`. When truthy, set
`endpointsServer.endpointSelector = endpoint_selector`; otherwise keep
`f"{LABEL_MODEL}={target_model_name}" if target_model_name else ""`.

**`api/llmd.py`** — `DefaultValuesRequest` gains `endpoint_selector: str | None =
None`; the `default_values` handler passes it into `default_llmd_values`.

No change to `create_stack`/`build_llmd_values`/models/migrations — the selector
arrives inside `values_yaml` (helm_values) exactly as a portal target does.

### Frontend (`admin/llmd/new/page.tsx`)

- Fetch `useExternalServings()` alongside `useModelDeployments()`.
- Target-model `<select>`: `<optgroup label="Portal deployments">` (existing
  options) + `<optgroup label="External servings">` where each option's value
  encodes the serving identity `ext::<cluster_id|"">::<namespace>::<deployment_name>`
  and its display text is `deployment_name` (+ engine badge text). A leading
  disabled placeholder option stays.
- New form state: `target_kind: "portal" | "external"`, `endpoint_selector: string`
  (the chosen `key=value`), and a transient reference to the selected external
  serving.
- On selecting a **portal** option: unchanged — `target_model_name = d.model_name`,
  `target_kind="portal"`, `endpoint_selector=""`.
- On selecting an **external** option: resolve the `ExternalServing` from the
  `useExternalServings()` list by the encoded identity, then:
  - `target_model_name = serving.registration?.model_name || serving.deployment_name`
  - `target_kind="external"`
  - auto-fill `namespace = serving.namespace` and `cluster_id = serving.cluster_id ?? ""`
  - render a **label `<select>`** listing `Object.entries(serving.labels)` as
    `key=value`, default-selecting a sensible label: prefer `app`, then
    `app.kubernetes.io/name`, else the first entry; its value sets
    `form.endpoint_selector`.
- Default-values fetch: pass `endpoint_selector` when `target_kind==="external"`,
  so the prefilled values.yaml already targets the external pods. Change
  `useLlmdDefaultValues` to take an object `{ target_model_name, endpoint_selector? }`
  instead of a bare string, and update the `useEffect`/`resetDefaults` callers.
- Submit body is unchanged in shape (`name`, `target_model_name`, `cluster_id`,
  `namespace`, `values_yaml`) — the external selector is already baked into
  `values_yaml`.
- i18n (en + ko): optgroup labels (`targetGroupPortal`, `targetGroupExternal`),
  the label-select label + hint (`endpointLabelLabel`, `endpointLabelHint`), and
  a "no labels" hint (`endpointLabelNone`).

### Detail/edit page

No change. `admin/llmd/[id]` shows `target_model_name` and edits
namespace/values only; it does not re-pick the model.

## Data flow

```
User opens llm-d/new
  → dropdown shows portal deployments + external servings (grouped)
Pick external serving X
  → target_model_name = X.model_name || X.deployment_name
  → namespace = X.namespace, cluster_id = X.cluster_id   (auto-fill, editable)
  → label <select> from X.labels → endpoint_selector = "app=my-vllm"
  → POST /default-values { target_model_name, endpoint_selector }
       → values.yaml prefilled with endpointSelector: app=my-vllm
Submit → create_stack (values_yaml carries the selector) → Application CR
```

## Error handling & edge cases

- **External serving with no labels** (`labels` empty): the label `<select>` is
  empty/disabled; show `endpointLabelNone` hint telling the user to set the
  selector in values.yaml directly. Submit is still allowed (values.yaml is
  authoritative and editable).
- **Unregistered external serving** (`registration` null): use `deployment_name`
  as `target_model_name`.
- **External serving on a different cluster**: the auto-filled `cluster_id`
  ensures the Application deploys to that cluster; if the user overrides it to a
  cluster where the pods don't exist, the router simply finds no endpoints
  (visible in the `/applied` view) — same failure surface as any bad selector.
- If the user later edits values.yaml, that always wins (existing behavior).

## Testing

- Backend: `default_llmd_values(..., endpoint_selector="app=x")` sets
  `endpointSelector` to `app=x`; without it, keeps the `llm-ops/model-name=<name>`
  default; blank `target_model_name` + no selector → empty string. Add to
  `tests/test_llmd_manifests.py`. (The `/default-values` passthrough is a thin
  wire; covered by the pure-function test + tsc.)
- Frontend: `npx tsc --noEmit` exit 0; `npm run lint` no new errors.

## Out of scope

- Persisting `target_kind`/selector as new stack columns (values.yaml is the
  source of truth).
- Registering the external serving with LiteLLM (separate existing feature).
- Multi-label AND/OR selector builder — a single `key=value` (plus free editing
  of values.yaml) is enough for v1.
- Changing the detail/edit page's target picker.
