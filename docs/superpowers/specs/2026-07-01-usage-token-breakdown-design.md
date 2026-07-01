# Usage token breakdown (input/output + cache read) — Design

**Date:** 2026-07-01
**Status:** Approved (brainstorming) — ready for implementation plan

## Goal

Everywhere the portal shows usage tokens, split the single "total tokens" figure into **Input**
and **Output**, and break the input down to show the **cache-read** portion. So a usage cell that
today reads `165,000` becomes `Input 120,000 (cache 30,000)` + `Output 45,000`.

## Data — already available (no schema change)

All usage is aggregated from **`LiteLLM_DailyUserSpend`**, which already has:
- `prompt_tokens` — total input tokens
- `completion_tokens` — output tokens
- `cache_read_input_tokens` — cache-read (cached) input tokens
- `cache_creation_input_tokens` — cache-write (not used by this feature)

**Token semantics (confirmed against `litellm/proxy/db/db_spend_update_writer.py`):**
`cache_read_input_tokens` comes from OpenAI's `prompt_tokens_details.cached_tokens` (a subset of
`prompt_tokens`) or Anthropic's top-level field; LiteLLM normalizes `prompt_tokens` to the full
input. So:
- **Input** = `prompt_tokens`
- **Cache read** = `cache_read_input_tokens` (a portion of Input)
- **Uncached input** = `max(0, prompt_tokens − cache_read_input_tokens)` (clamped; derived, not stored)
- **Output** = `completion_tokens`

The queries today compute only `total_tokens = SUM(prompt_tokens + completion_tokens)`.

## Buckets (decided)

Input / Output, with Input showing the **cache-read** portion only. `cache_creation` is **not**
surfaced (it stays inside Input). No separate "uncached" column — the parenthetical cache figure
implies it.

## Display (decided)

- **Tables** (team Usage tab, member-model usage, admin usage): replace the single `Tokens` column
  with two columns — **`Input (cache rd)`** and **`Output`**.
  - Input cell: `{input.toLocaleString()} ({cache_read.toLocaleString()})`, the parenthetical
    muted (`text-muted-foreground`). If `cache_read == 0`, omit the parenthetical → just `{input}`.
  - Totals rows use the same format.
- **Usage calendar (heatmap)**: the day cell keeps its single aggregate; the **tooltip** shows
  `Input {n} (cache {n}) · Output {n}`.

## Backend changes

Each usage row/total gains `input_tokens`, `output_tokens`, `cache_read_tokens`. Keep `total_tokens`
(used for sort + backward compatibility). Change the SQL `SUM(prompt_tokens + completion_tokens)`
sites to also `SUM(prompt_tokens)`, `SUM(completion_tokens)`, `SUM(cache_read_input_tokens)`.

- **`backend/app/api/teams.py`** — three aggregation queries:
  - team member usage (per-user rollup)
  - team usage series (per-date)
  - member usage by model/model_group
- **`backend/app/api/admin_usage.py`** — two aggregation queries:
  - global per-(user, team) rows + totals
  - per-user daily series

Serialize `input_tokens`, `output_tokens`, `cache_read_tokens` (ints, default 0) on every row and
totals object alongside the existing `total_tokens`.

## Frontend changes

- **Types** (`frontend/src/types/index.ts`, `frontend/src/hooks/use-api.ts`): add
  `input_tokens: number`, `output_tokens: number`, `cache_read_tokens: number` to the usage row and
  totals types (`TeamUsageResponse`, `AdminUsageRow`/`AdminUsageResponse.totals`, the member-by-model
  item, and any daily-series item).
- **Shared Input rendering** (DRY across all three tables), split so the logic stays testable:
  - Pure formatter in `frontend/src/lib/usage.ts`:
    `formatInputTokens(input: number, cacheRead: number, localeTag: string) → { input: string; cache: string | null }`
    — clamps `cache` to `min(cacheRead, input)`; returns `cache: null` when `cacheRead === 0`.
  - Thin shared component `frontend/src/components/input-tokens.tsx` (`<InputTokens input cacheRead />`)
    that calls the formatter and renders `{input}` + a muted `(cache)` span; used by every table so
    the markup isn't duplicated.
- **Tables**:
  - `frontend/src/app/(app)/teams/[teamId]/page.tsx` (UsageTab member table + series)
  - `frontend/src/components/member-model-usage.tsx`
  - `frontend/src/app/(app)/admin/usage/page.tsx`
  Replace the `Tokens` column header + cell with `Input (cache rd)` and `Output`.
- **Calendar**: `frontend/src/components/usage-calendar.tsx` — extend the tooltip with the breakdown.
- **i18n** (`frontend/messages/{en,ko}.json`): `colInput`, `colOutput`, and a short `cache` label,
  under the relevant usage namespaces. Keep en/ko key parity.

## Edge cases

- `cache_read == 0` → no parenthetical (cleaner for non-caching models).
- `cache_read > input` (shouldn't happen given normalization, but guard): clamp the displayed cache
  figure to `min(cache_read, input)` so the parenthetical never exceeds Input.
- Missing columns / null sums → coalesce to 0.

## Testing

- **Backend**: extend the existing usage tests (`backend/tests/test_teams.py`, and admin-usage tests
  if present) — assert each endpoint returns `input_tokens`/`output_tokens`/`cache_read_tokens` and
  that they sum correctly (e.g. mock two daily rows and check the rollup). Verify `total_tokens`
  still equals input+output.
- **Frontend**: `cd frontend && tsc --noEmit` clean; en/ko `usage`/`adminUsage`/`teamDetail` key
  parity; the `formatInputTokens` helper unit-tested (cache=0 → `cache: null`; cache>0 → formatted;
  `cache > input` clamps to input).

## Out of scope (YAGNI)

- `cache_creation` (cache-write) as a separate figure.
- New sort options (e.g. sort by input/cache) — keep sorting by `total_tokens`.
- Per-cache **cost** breakdown (that's the cost feature, separate).
- Model-detail pricing tab (shows cost, not usage tokens).

## Open item to confirm during implementation

- Re-confirm `prompt_tokens` includes `cache_read` in the actual `LiteLLM_DailyUserSpend` rows for
  the providers in use (spot-check a real row). The clamp makes the display safe either way, but the
  "uncached" interpretation depends on it.
