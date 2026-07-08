# Benchmark Form Mode Tabs — Design

**Date:** 2026-07-08
**Status:** Approved (user confirmed 4-tab layout; clone tab first/default)

## Problem

The benchmark creation form (`/admin/benchmarks/new`) stacks four different
"start modes" vertically inside one Target card: a load-from-previous-run
select, a serving-deployment select (portal + external optgroups) with a
clone/direct radio underneath, a LiteLLM model select, and a
`serving_overrides` editor. The mode is inferred from a precedence chain of
`deploymentId / externalTarget / modelName / ephemeral`, and cross-field
locking (model select disabled when a deployment is chosen, radio forced by
readiness) makes the form hard to scan and the code hard to follow.

## Decision

Reorganize the Target card into **four tabs** using the existing shadcn
`Tabs` component (`frontend/src/components/ui/tabs.tsx`). The active tab IS
the mode — no more inference chain, no clone/direct radio, no cross-field
disabling.

**Tab order (clone first, default active):**

1. **새로 띄워서 (clone)** — one select with two optgroups: ALL portal
   deployments (Ready or not) + externally discovered servings (hidden for
   accuracy tools, existing rule). `serving_overrides` JSON editor below.
   Submits `deployment_id + ephemeral: true`, or `external_target`
   (+ `serving_overrides`).
2. **실행 중 서빙에 직접 (direct)** — portal deployments with
   `ready_replicas > 0` only. Submits `deployment_id` alone. The old
   "not-Ready forces clone" radio logic disappears naturally (backend 409
   backstop stays).
3. **LiteLLM 모델 (model)** — model-alias select. NFS override fields in the
   params section render only in this mode (same meaning as today's
   "no deployment/external selected" condition). Submits `model_name`.
4. **이전 실행 (from run)** — past-run picker only. Picking a run prefills
   all fields and auto-switches the active tab to the run's mode. A run that
   was direct whose deployment is now not Ready lands on the clone tab
   (parity with today's re-validation). API key is never restored (today's
   rule). With no past runs, the tab shows a muted empty-state line instead
   of the picker (today the select is simply not rendered).

## Scope

Frontend only. Files touched:

- `frontend/src/app/(app)/admin/benchmarks/new/page.tsx` — tab layout +
  explicit `mode` state.
- `frontend/messages/en.json`, `frontend/messages/ko.json` — tab label/hint
  keys; retire radio-only keys that lose their render site.

No backend, API, or type changes. Request bodies are byte-identical to
today's for every mode.

## State model

- New state `mode: "clone" | "direct" | "model" | "fromRun"`, initial
  `"clone"`, bound to the Tabs value.
- `ephemeral` boolean state is removed; clone-ness derives from
  `mode === "clone"`.
- Submit and preview build the body from the **active tab's fields only**;
  leftover selections in inactive tabs are ignored (not cleared). Per-mode
  validation replaces the current combined guard: no target chosen in the
  active tab → toast "대상을 선택하세요" (existing `errorTargetRequired`).
- The cluster select stays in the shared position above the tabs, unchanged:
  auto-filled and disabled when an external serving is chosen on the clone
  tab; hidden nowhere.
- `directModeDisabled`, the `handleTargetChange` ephemeral-forcing, and the
  model-select `disabled={!!deploymentId || !!externalTarget}` coupling are
  all deleted.

## Behavior rules preserved

- Accuracy tool + external target: external optgroup hidden on the clone tab
  and any selected external target cleared (existing `useEffect` kept).
- External target: namespace input auto-filled/disabled; `cluster_id`/
  `namespace` omitted from the outgoing body (backend derives placement).
- Preview debounce (400 ms) and best-effort body construction unchanged;
  preview also keys off the active mode.
- `loadFromRun` field-restoration logic unchanged except it now also sets
  the target tab.

## Non-goals

- No wizard/multi-step flow; single page stays.
- No restoring `external_target` from past runs (BenchmarkRun doesn't carry
  it — known v1 limit).
- No backend validation changes.

## Verification

- `npm run lint` and `npm run build` pass with no NEW errors (baseline:
  4 pre-existing lint errors, 13 warnings).
- Manual pass in the local docker stack: each of the four tabs previews and
  submits its expected body shape (clone-portal, clone-external, direct,
  model, from-run prefill + auto-switch).
