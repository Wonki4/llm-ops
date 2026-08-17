# Bulk Request Review (일괄 승인/거절) — Design

**Date:** 2026-08-17
**Status:** Approved (design)

## Goal

Let a team admin / super user act on **several pending requests at once** from
the admin requests page — select multiple rows and approve (or reject) them in
one gesture — instead of opening the per-request dialog N times.

"Multi" here means **multiple requests**, not multiple approvers per request.

The hard constraint comes from budget approvals: applying a single budget
approval is slow and failure-prone (it calls LiteLLM `update_team_member`, which
can 409 on an active-boost race or 502 on a LiteLLM failure). A batch of them
must therefore:

- **be robust to per-item failure** — one bad item must not roll back or block
  the others (partial success is the desired outcome, not a bug);
- **run serially** — parallelizing the slow applies does not help (they
  serialize on LiteLLM / the DB and can race on the same member's active-boost
  row) and adds risk, so the batch is processed one item at a time.

## Background — verified facts

- **The single-item endpoints already do the hard part.**
  `POST /api/team-requests/{id}/approve` and `.../reject`
  (`backend/app/api/team_requests.py`) each:
  - re-check `require_team_admin` for the request's team,
  - reject non-pending requests with `400 "Request already {status}"`,
  - branch internally on `request_type` (join vs budget) — the caller never
    needs to know the type,
  - for budget, call `apply_member_budget_boost(...)` (temporary) or
    `litellm.update_team_member(...)` (permanent), surfacing `409` on an active
    boost race and `502` on a LiteLLM failure, and **leave the request pending**
    on failure.
  So a batch layer only needs to call these once per selected id and collect the
  results.
- **The frontend already wires these up.** `admin/requests/page.tsx` uses
  `useJoinRequests()` (list), `useApproveRequest()` / `useRejectRequest()`
  (react-query mutations over `apiFetch`, each invalidating `["join-requests"]`
  on success). The table paginates client-side (`pageSize = 20`), filters by
  status/type/search, and renders per-row Approve/Reject buttons for pending
  rows only. A confirmation dialog collects an optional `comment`.
- **`apiFetch` throws on non-2xx**, with `error.message` set to the server's
  `detail` string. That message is what a failed batch item shows.
- **No Checkbox or Progress UI primitive exists**, and there is no radix
  checkbox/progress dependency. To honor the air-gap "no new external runtime
  deps" rule we use a native `<input type="checkbox">` + a Tailwind `<div>`
  progress bar (the page already uses a native `<textarea>`). No new dependency.
- **No frontend test harness exists** (only `next dev/build/lint`); prior
  frontend work is gated by type-check + lint + manual E2E.

## Decisions

1. **Approach A — client-driven sequential.** The batch is a thin frontend
   layer that re-calls the existing single-item endpoints one at a time. **No
   backend change.** No batch endpoint, no background job (rejected as
   over-engineering for the expected batch sizes; each HTTP call is short so
   there is no aggregate-timeout risk).
2. **Both approve and reject** are bulk-able.
3. **Mixed request types** (join + budget) may be selected together — the loop
   is type-agnostic because the endpoints are.
4. **Continue-on-error, partial success.** A failed item is recorded and the
   loop continues. After the run, succeeded items are cleared from the
   selection and failed items stay selected, so re-running retries only the
   failures.
5. **Selection is per-page-select-all but Set-backed across pages.** The header
   checkbox toggles the current page's pending rows; individual selections
   accumulate in a `Set<string>` that survives pagination. Only pending rows are
   selectable.
6. **One shared comment** applies to every item in a batch (approve or reject),
   mirroring the single dialog's optional comment.
7. **One list refetch per batch**, not per item — the batch invalidates
   `["join-requests"]` once, at the end.

## Architecture

Frontend only. Three units:

### `frontend/src/lib/bulk-review.ts` — the sequential runner (pure)

Isolates the "serial + continue-on-error + progress" logic into a pure function
with no React or fetch dependency, so it is correct by inspection (and unit-
testable if a harness is ever added).

```ts
export interface BulkItemResult {
  id: string;
  ok: boolean;
  error?: string; // server `detail` message when ok === false
}

export interface BulkProgress {
  total: number;
  done: number; // items processed so far (succeeded + failed)
  results: BulkItemResult[];
}

/**
 * Run `perItem` over `ids` strictly one at a time. Never rejects: a thrown
 * `perItem` becomes `{ ok: false, error }`. Calls `onProgress` after each item.
 */
export async function runSequential(
  ids: string[],
  perItem: (id: string) => Promise<void>,
  onProgress?: (p: BulkProgress) => void,
): Promise<BulkItemResult[]> {
  const results: BulkItemResult[] = [];
  for (const id of ids) {
    try {
      await perItem(id);
      results.push({ id, ok: true });
    } catch (e) {
      results.push({ id, ok: false, error: e instanceof Error ? e.message : String(e) });
    }
    onProgress?.({ total: ids.length, done: results.length, results: [...results] });
  }
  return results;
}
```

### `frontend/src/hooks/use-api.ts` — `useBulkReview()`

Supplies `perItem` (an `apiFetch` to the right endpoint with the shared comment)
to `runSequential`, and invalidates the list once when the batch finishes.

```ts
export function useBulkReview() {
  const qc = useQueryClient();
  const run = async (
    action: "approve" | "reject",
    ids: string[],
    comment: string | undefined,
    onProgress?: (p: BulkProgress) => void,
  ): Promise<BulkItemResult[]> => {
    const body = JSON.stringify(comment?.trim() ? { comment: comment.trim() } : {});
    try {
      return await runSequential(
        ids,
        (id) =>
          apiFetch<{ status: string }>(`/api/team-requests/${id}/${action}`, {
            method: "POST",
            body,
          }).then(() => undefined),
        onProgress,
      );
    } finally {
      qc.invalidateQueries({ queryKey: ["join-requests"] });
    }
  };
  return { run };
}
```

### `frontend/src/app/(app)/admin/requests/page.tsx` — selection + bulk UI

New state and UI, layered onto the existing table without disturbing the
per-row single-action flow:

- **State:** `selectedIds: Set<string>`; `bulkOpen: boolean`;
  `bulkAction: "approve" | "reject"`; `bulkComment: string`;
  `bulkRunning: boolean`; `bulkProgress: BulkProgress | null`;
  `bulkResults: BulkItemResult[] | null`.
- **Checkbox column** (new first column):
  - Per-row checkbox rendered **only for `status === "pending"`** rows (blank
    cell otherwise); toggles the row's id in `selectedIds`.
  - Header checkbox toggles the current page's pending ids
    (`pageRequests.filter(pending).map(id)`) on/off; its checked state reflects
    "all current-page pending are selected".
- **Bulk action bar** (shown when `selectedIds.size > 0`, above the table):
  "N개 선택됨" + `[선택 승인]` + `[선택 거절]` + `[해제]`. The approve/reject
  buttons open the bulk dialog with the chosen action; 해제 clears the Set.
- **Bulk confirmation dialog:**
  - Header: action + count, plus a type breakdown computed from the selected
    requests ("예산 N · 참여 M").
  - A caution line that budget approvals take time and are processed one by one.
  - Shared optional comment `<textarea>` (reuses `commentLabel`/`Placeholder`).
  - **Idle:** Cancel + Confirm.
  - **Running:** Confirm becomes a disabled progress state; the dialog cannot be
    closed (`onOpenChange` guarded by `bulkRunning`). A Tailwind progress bar +
    "`done`/`total` · 실패 `failedCount`".
  - **Done:** a results summary — total succeeded, and a scrollable list of
    failed items (`requester_id`/team + the server error message). Succeeded ids
    are removed from `selectedIds`; failed ids stay in it. Closing the dialog
    leaves the (retryable) failures selected.

### Data flow

```
select rows (Set)
  → 선택 승인/거절 → bulk dialog (count, type breakdown, shared comment)
    → confirm → useBulkReview().run(action, [...selectedIds], comment, onProgress)
       → runSequential: for each id → apiFetch(/{id}/{action})  [serial]
            ↳ ok    → results += {ok:true}
            ↳ throw → results += {ok:false, error: detail}   [loop continues]
         → onProgress updates the bar after each
       → finally: invalidate ["join-requests"] (single refetch)
    → dialog shows summary; drop succeeded from Set, keep failed
```

## Error handling / robustness

- **Per-item isolation:** `runSequential` never rejects; each item's failure is
  captured, the batch always completes over all ids.
- **Serial by construction:** no parallelism, matching the constraint that it
  does not help and adds race risk.
- **Already-handled items:** if another admin resolved a request first, its
  `/approve` returns `400 "Request already approved"`; that item shows as failed
  with the server message, and the end-of-batch refetch drops it from the list
  naturally. (No brittle status-string classification — the message is shown
  as-is.)
- **Retry:** succeeded → deselected, failed → still selected; pressing the bulk
  button again retries only the failures.
- **No aggregate timeout:** each call is a short independent request; batch
  length is irrelevant. Closing/navigating mid-run stops further calls;
  already-processed items are committed server-side, the rest stay pending.
- **Permissions:** each endpoint re-checks `require_team_admin`; an item the
  actor cannot act on returns 403 and is reported as a failure without affecting
  the rest.

## i18n

Add en + ko keys (equal counts, project convention) under `adminRequests`:
`selectAll`, `clearSelection`, `bulkSelectedCount` (`{count}`),
`bulkApprove`, `bulkReject`, `bulkConfirmTitle` (`{action}`),
`bulkTypeBreakdown` (`{budget}`, `{join}`), `bulkBudgetCaution`,
`bulkProgress` (`{done}`, `{total}`, `{failed}`), `bulkResultTitle`,
`bulkSucceeded` (`{count}`), `bulkFailedTitle` (`{count}`), `bulkAllSucceeded`,
`bulkClose`. (The comment field reuses existing `commentLabel` /
`commentPlaceholder`.)

## Testing

- **Gates:** `npx tsc --noEmit` (exit 0) + `npm run lint`, from `frontend/`.
- **`runSequential` is pure** — verified by inspection; the single place the
  serial/continue-on-error contract lives.
- **Manual E2E:**
  - Select several pending budget requests → 선택 승인 → progress advances
    one-by-one; all approved; list refreshes once.
  - Force a failure (e.g. a request already resolved in another tab) → that item
    reported failed with its server message, others still succeed, failed stays
    selected, re-run clears it.
  - Mixed join + budget selection approves both.
  - Bulk reject with a shared comment sets `review_comment` on each.
  - Per-row single Approve/Reject still works unchanged.

## Non-goals

- No backend batch endpoint and no background job (Approach A only).
- No per-item requester-history in the bulk dialog — deep per-request review
  stays in the single-approve path; bulk is the fast path.
- No change to the pre-existing lack of a concurrent-approve row lock on the
  single endpoint (out of scope; the client-serial batch does not worsen it).
- No new frontend test harness.
