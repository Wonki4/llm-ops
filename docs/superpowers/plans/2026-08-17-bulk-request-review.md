# Bulk Request Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a team admin / super user select multiple pending requests on the admin requests page and approve or reject them in one gesture, processed serially with per-item error isolation.

**Architecture:** Frontend-only. A pure `runSequential` runner drives the existing single-item `/approve`·`/reject` endpoints one id at a time; a `useBulkReview` hook wires it to `apiFetch` and refetches the list once at the end; the admin requests page gains a checkbox column, a bulk action bar, and a bulk confirm/progress/result dialog.

**Tech Stack:** Next.js (app router, client components), React, @tanstack/react-query, next-intl (en/ko), Tailwind. No backend change.

## Global Constraints

- **No backend change.** Reuse `POST /api/team-requests/{id}/approve` and `.../reject` exactly as they are.
- **No new runtime dependency** (air-gap rule): use a native `<input type="checkbox">` and a Tailwind `<div>` progress bar — no radix/checkbox/progress packages.
- **No frontend test harness exists** (only `next dev/build/lint`). Verification gates for every task: `npx tsc --noEmit` (exit 0) and `npm run lint`, run from `frontend/`. Behavior is verified by manual E2E (Task 4) and, for the pure runner, by inspection. Do not add a test runner.
- **en/ko i18n parity**: every new key added to `messages/en.json` must be added to `messages/ko.json`; the `adminRequests` block key counts must stay equal (currently 44 each → 58 each after Task 3).
- **Serial, continue-on-error, partial success**: the batch processes ids strictly one at a time; a failed item never aborts the batch; succeeded items are deselected and failed items stay selected for retry.
- All work happens on branch `feat/bulk-request-review` (already created off `origin/main`). Never stage the `litellm` submodule.

---

## File Structure

- **Create** `frontend/src/lib/bulk-review.ts` — pure sequential runner + result/progress types. One responsibility: the serial/continue-on-error/progress contract, with zero React or fetch dependency.
- **Modify** `frontend/src/hooks/use-api.ts` — add `useBulkReview()` hook that feeds `apiFetch` calls to `runSequential` and invalidates `["join-requests"]` once.
- **Modify** `frontend/messages/en.json` + `frontend/messages/ko.json` — 14 new `adminRequests` keys.
- **Modify** `frontend/src/app/(app)/admin/requests/page.tsx` — selection state, checkbox column, bulk bar, bulk dialog, and the run handler.

---

## Task 1: Pure sequential runner (`lib/bulk-review.ts`)

**Files:**
- Create: `frontend/src/lib/bulk-review.ts`

**Interfaces:**
- Produces:
  - `interface BulkItemResult { id: string; ok: boolean; error?: string }`
  - `interface BulkProgress { total: number; done: number; results: BulkItemResult[] }`
  - `async function runSequential(ids: string[], perItem: (id: string) => Promise<void>, onProgress?: (p: BulkProgress) => void): Promise<BulkItemResult[]>`

- [ ] **Step 1: Create the file with the full implementation**

Create `frontend/src/lib/bulk-review.ts`:

```ts
/**
 * Sequential, continue-on-error batch runner for request review.
 *
 * Pure: no React, no fetch. Runs `perItem` over `ids` strictly one at a time
 * (never in parallel — parallelizing the slow budget applies does not help and
 * risks racing on the same member's active-boost row). A thrown `perItem` is
 * captured as `{ ok: false, error }` and the loop continues, so one bad item
 * never aborts the batch (partial success is the intended outcome).
 */

export interface BulkItemResult {
  id: string;
  /** false when `perItem` threw. */
  ok: boolean;
  /** server `detail` message (or stringified error) when `ok === false`. */
  error?: string;
}

export interface BulkProgress {
  total: number;
  /** items processed so far (succeeded + failed). */
  done: number;
  results: BulkItemResult[];
}

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
      results.push({
        id,
        ok: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
    onProgress?.({ total: ids.length, done: results.length, results: [...results] });
  }
  return results;
}
```

- [ ] **Step 2: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: exit 0, no errors referencing `bulk-review.ts`.

- [ ] **Step 3: Verify the contract by inspection**

Confirm all three properties hold in the code above (no harness to assert them):
1. **Serial** — a single `for…of` with `await` inside; the next `perItem` starts only after the previous settles.
2. **Continue-on-error** — the `await perItem` is inside `try/catch`; a throw pushes `{ ok: false }` and the loop proceeds. `runSequential` itself never rejects.
3. **Progress after each** — `onProgress` is called once per iteration with a fresh `results` copy (`[...results]`) so a consumer setting React state sees a new array each time.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/bulk-review.ts
git commit -m "feat(requests): pure serial continue-on-error batch runner"
```

---

## Task 2: `useBulkReview()` hook

**Files:**
- Modify: `frontend/src/hooks/use-api.ts`

**Interfaces:**
- Consumes (Task 1): `runSequential`, `BulkItemResult`, `BulkProgress` from `@/lib/bulk-review`; existing `apiFetch` from `@/lib/api`; `useQueryClient` (already imported).
- Produces:
  - `function useBulkReview(): { run: (action: "approve" | "reject", ids: string[], comment: string | undefined, onProgress?: (p: BulkProgress) => void) => Promise<BulkItemResult[]> }`

- [ ] **Step 1: Add imports**

At the top of `frontend/src/hooks/use-api.ts`, after the existing `import { apiFetch } from "@/lib/api";` line, add:

```ts
import { runSequential } from "@/lib/bulk-review";
import type { BulkItemResult, BulkProgress } from "@/lib/bulk-review";
```

- [ ] **Step 2: Add the hook**

Immediately after the existing `useRejectRequest()` function (it ends with its closing `}` around the `qc.invalidateQueries({ queryKey: ["join-requests"] })` block), add:

```ts
/**
 * Bulk approve/reject: re-calls the single-item endpoint once per id, strictly
 * serially (see runSequential), with one shared comment applied to every item.
 * Never rejects — each item's outcome is in the returned results. The list is
 * refetched exactly once, when the whole batch finishes.
 */
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

- [ ] **Step 3: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4: Lint**

Run (from `frontend/`): `npm run lint`
Expected: no new errors for `use-api.ts`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-api.ts
git commit -m "feat(requests): useBulkReview hook (serial re-call + single refetch)"
```

---

## Task 3: i18n keys (en + ko parity)

**Files:**
- Modify: `frontend/messages/en.json`
- Modify: `frontend/messages/ko.json`

**Interfaces:**
- Produces: 14 new keys under `adminRequests`, consumed by Task 4 via `t("…")`:
  `selectAll`, `clearSelection`, `bulkSelectedCount`, `bulkApprove`, `bulkReject`,
  `bulkConfirmTitle`, `bulkTypeBreakdown`, `bulkBudgetCaution`, `bulkProgress`,
  `bulkResultTitle`, `bulkSucceeded`, `bulkFailedTitle`, `bulkAllSucceeded`, `bulkClose`.

- [ ] **Step 1: Add keys to `en.json`**

In `frontend/messages/en.json`, inside the `"adminRequests"` object, add these entries (place them right after the existing `"processing"` key so the block stays grouped — insert a comma after the previous last entry as needed):

```json
    "selectAll": "Select all pending",
    "clearSelection": "Clear",
    "bulkSelectedCount": "{count} selected",
    "bulkApprove": "Approve selected",
    "bulkReject": "Reject selected",
    "bulkConfirmTitle": "{action} {count} requests",
    "bulkTypeBreakdown": "Budget {budget} · Join {join}",
    "bulkBudgetCaution": "Budget approvals take time and are processed one at a time.",
    "bulkProgress": "{done}/{total} · {failed} failed",
    "bulkResultTitle": "Result",
    "bulkSucceeded": "{count} succeeded",
    "bulkFailedTitle": "{count} failed",
    "bulkAllSucceeded": "All requests processed successfully.",
    "bulkClose": "Close"
```

- [ ] **Step 2: Add the same keys to `ko.json`**

In `frontend/messages/ko.json`, inside `"adminRequests"`, add (same placement):

```json
    "selectAll": "대기 전체 선택",
    "clearSelection": "선택 해제",
    "bulkSelectedCount": "{count}건 선택됨",
    "bulkApprove": "선택 승인",
    "bulkReject": "선택 거절",
    "bulkConfirmTitle": "{count}건 {action}",
    "bulkTypeBreakdown": "예산 {budget} · 가입 {join}",
    "bulkBudgetCaution": "예산 승인은 시간이 걸려 한 건씩 순차 처리됩니다.",
    "bulkProgress": "{done}/{total} · 실패 {failed}",
    "bulkResultTitle": "결과",
    "bulkSucceeded": "{count}건 성공",
    "bulkFailedTitle": "실패 {count}건",
    "bulkAllSucceeded": "모든 요청이 정상 처리되었습니다.",
    "bulkClose": "닫기"
```

- [ ] **Step 3: Verify valid JSON + key parity**

Run (from `frontend/`):

```bash
node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const a=Object.keys(en.adminRequests),b=Object.keys(ko.adminRequests);const miss=a.filter(k=>!b.includes(k)).concat(b.filter(k=>!a.includes(k)));console.log('en',a.length,'ko',b.length,'mismatch',miss)"
```

Expected: `en 58 ko 58 mismatch []` (both parse without error, equal counts, no mismatched keys).

- [ ] **Step 4: Commit**

```bash
git add frontend/messages/en.json frontend/messages/ko.json
git commit -m "i18n(requests): bulk review strings (en/ko)"
```

---

## Task 4: Selection + bulk dialog on the admin requests page

**Files:**
- Modify: `frontend/src/app/(app)/admin/requests/page.tsx`

**Interfaces:**
- Consumes (Task 2): `useBulkReview` from `@/hooks/use-api`.
- Consumes (Task 1): `BulkItemResult`, `BulkProgress` (type-only) from `@/lib/bulk-review`.
- Consumes (Task 3): the 14 new `adminRequests` i18n keys.

- [ ] **Step 1: Add imports**

In the import block of `page.tsx`:

Add `useBulkReview` to the existing `@/hooks/use-api` import (which currently lists `useJoinRequests, useApproveRequest, useRejectRequest, useRequesterHistory, useMe`):

```ts
import {
  useJoinRequests,
  useApproveRequest,
  useRejectRequest,
  useRequesterHistory,
  useMe,
  useBulkReview,
} from "@/hooks/use-api";
```

Add a type-only import after the existing `@/lib/locale` import:

```ts
import type { BulkItemResult, BulkProgress } from "@/lib/bulk-review";
```

- [ ] **Step 2: Add bulk state + the run handler + derived selectors**

Inside `AdminRequestsPage`, after the existing `const [comment, setComment] = useState("");` line, add:

```ts
  const { run: runBulkReview } = useBulkReview();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkAction, setBulkAction] = useState<"approve" | "reject">("approve");
  const [bulkComment, setBulkComment] = useState("");
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null);
  const [bulkResults, setBulkResults] = useState<BulkItemResult[] | null>(null);
```

Then, after the existing `const pageRequests = …` line (the one that slices `filteredRequests` for the current page), add the derived selectors and handlers:

```ts
  const requestById = useMemo(
    () => new Map((requests ?? []).map((r) => [r.id, r] as const)),
    [requests],
  );
  const pendingPageIds = useMemo(
    () => pageRequests.filter((r) => r.status === "pending").map((r) => r.id),
    [pageRequests],
  );
  const allPagePendingSelected =
    pendingPageIds.length > 0 && pendingPageIds.every((id) => selectedIds.has(id));
  const somePagePendingSelected = pendingPageIds.some((id) => selectedIds.has(id));

  const selectedReqs = useMemo(
    () => (requests ?? []).filter((r) => selectedIds.has(r.id)),
    [requests, selectedIds],
  );
  const bulkBudgetCount = selectedReqs.filter(
    (r) => (r.request_type ?? "join") === "budget",
  ).length;
  const bulkJoinCount = selectedReqs.length - bulkBudgetCount;

  function toggleOne(id: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  function toggleAllPage(checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) pendingPageIds.forEach((id) => next.add(id));
      else pendingPageIds.forEach((id) => next.delete(id));
      return next;
    });
  }
  function openBulk(action: "approve" | "reject") {
    setBulkAction(action);
    setBulkComment("");
    setBulkProgress(null);
    setBulkResults(null);
    setBulkOpen(true);
  }
  async function runBulk() {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    setBulkRunning(true);
    setBulkResults(null);
    setBulkProgress({ total: ids.length, done: 0, results: [] });
    const results = await runBulkReview(
      bulkAction,
      ids,
      bulkComment,
      (p) => setBulkProgress(p),
    );
    setBulkRunning(false);
    setBulkResults(results);
    // Drop succeeded from the selection; keep failures selected for retry.
    setSelectedIds(new Set(results.filter((r) => !r.ok).map((r) => r.id)));
  }

  const bulkActionLabel = (a: "approve" | "reject") =>
    a === "approve" ? t("statusApproved") : t("statusRejected");
```

- [ ] **Step 3: Add the bulk action bar**

Directly above the status-tabs block (the `<Tabs value={statusTab} …>` element), insert:

```tsx
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border bg-muted/40 px-4 py-2">
          <span className="text-sm font-medium">
            {t("bulkSelectedCount", { count: selectedIds.size })}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              className="bg-green-600 text-white hover:bg-green-700"
              onClick={() => openBulk("approve")}
            >
              {t("bulkApprove")}
            </Button>
            <Button size="sm" variant="destructive" onClick={() => openBulk("reject")}>
              {t("bulkReject")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelectedIds(new Set())}
            >
              {t("clearSelection")}
            </Button>
          </div>
        </div>
      )}
```

- [ ] **Step 4: Add the checkbox column to the table header**

In the `<TableHeader>` `<TableRow>`, add a new first `<TableHead>` before `<TableHead>{t("colType")}</TableHead>`:

```tsx
                    <TableHead className="w-8">
                      <input
                        type="checkbox"
                        aria-label={t("selectAll")}
                        className="size-4 cursor-pointer"
                        checked={allPagePendingSelected}
                        ref={(el) => {
                          if (el)
                            el.indeterminate =
                              somePagePendingSelected && !allPagePendingSelected;
                        }}
                        onChange={(e) => toggleAllPage(e.target.checked)}
                      />
                    </TableHead>
```

- [ ] **Step 5: Add the checkbox cell to each body row**

In the `pageRequests.map((req) => ( … ))` body, add a new first `<TableCell>` before the `<TableCell>` that renders `<TypeBadge …>`:

```tsx
                      <TableCell className="w-8">
                        {req.status === "pending" ? (
                          <input
                            type="checkbox"
                            aria-label={req.requester_id}
                            className="size-4 cursor-pointer"
                            checked={selectedIds.has(req.id)}
                            onChange={(e) => toggleOne(req.id, e.target.checked)}
                          />
                        ) : null}
                      </TableCell>
```

- [ ] **Step 6: Add the bulk dialog**

Immediately before the final closing `</div>` of the component's returned JSX (right after the existing approve/reject confirmation `</Dialog>`), insert:

```tsx
      {/* Bulk approve / reject dialog */}
      <Dialog
        open={bulkOpen}
        onOpenChange={(o) => {
          if (!bulkRunning) setBulkOpen(o);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("bulkConfirmTitle", {
                action: bulkActionLabel(bulkAction),
                count: selectedIds.size,
              })}
            </DialogTitle>
            <DialogDescription>
              {t("bulkTypeBreakdown", { budget: bulkBudgetCount, join: bulkJoinCount })}
            </DialogDescription>
          </DialogHeader>

          {bulkResults ? (
            <div className="space-y-3 text-sm">
              <p className="font-medium">{t("bulkResultTitle")}</p>
              <p className="text-green-700 dark:text-green-400">
                {t("bulkSucceeded", {
                  count: bulkResults.filter((r) => r.ok).length,
                })}
              </p>
              {bulkResults.some((r) => !r.ok) ? (
                <div className="space-y-1">
                  <p className="font-medium text-destructive">
                    {t("bulkFailedTitle", {
                      count: bulkResults.filter((r) => !r.ok).length,
                    })}
                  </p>
                  <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
                    {bulkResults
                      .filter((r) => !r.ok)
                      .map((r) => {
                        const req = requestById.get(r.id);
                        return (
                          <div key={r.id} className="flex justify-between gap-2">
                            <span className="font-medium">
                              {req?.requester_id ?? r.id}
                              {req ? ` · ${req.team_alias || req.team_id}` : ""}
                            </span>
                            <span className="text-muted-foreground">{r.error}</span>
                          </div>
                        );
                      })}
                  </div>
                </div>
              ) : (
                <p className="text-muted-foreground">{t("bulkAllSucceeded")}</p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {bulkBudgetCount > 0 && (
                <p className="text-xs text-muted-foreground">{t("bulkBudgetCaution")}</p>
              )}
              <div className="space-y-2">
                <Label htmlFor="bulk-comment">{t("commentLabel")}</Label>
                <textarea
                  id="bulk-comment"
                  rows={3}
                  value={bulkComment}
                  onChange={(e) => setBulkComment(e.target.value)}
                  placeholder={t("commentPlaceholder")}
                  disabled={bulkRunning}
                  className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                />
              </div>
              {bulkProgress && (
                <div className="space-y-1">
                  <div className="h-2 w-full overflow-hidden rounded bg-muted">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{
                        width: `${
                          bulkProgress.total
                            ? (bulkProgress.done / bulkProgress.total) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t("bulkProgress", {
                      done: bulkProgress.done,
                      total: bulkProgress.total,
                      failed: bulkProgress.results.filter((r) => !r.ok).length,
                    })}
                  </p>
                </div>
              )}
            </div>
          )}

          <DialogFooter>
            {bulkResults ? (
              <Button onClick={() => setBulkOpen(false)}>{t("bulkClose")}</Button>
            ) : (
              <>
                <Button
                  variant="outline"
                  onClick={() => setBulkOpen(false)}
                  disabled={bulkRunning}
                >
                  {tc("cancel")}
                </Button>
                <Button
                  variant={bulkAction === "approve" ? "default" : "destructive"}
                  onClick={runBulk}
                  disabled={bulkRunning || selectedIds.size === 0}
                  className={
                    bulkAction === "approve"
                      ? "bg-green-600 text-white hover:bg-green-700"
                      : undefined
                  }
                >
                  {bulkRunning ? t("processing") : bulkActionLabel(bulkAction)}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
```

- [ ] **Step 7: Type-check + lint**

Run (from `frontend/`):

```bash
npx tsc --noEmit
npm run lint
```

Expected: `tsc` exits 0; lint reports no new errors for `page.tsx`.

- [ ] **Step 8: Manual E2E (dev server)**

Run `npm run dev` (from `frontend/`), sign in as a super user / team admin, open `/admin/requests`, and confirm:
1. **Select + approve:** check several pending budget requests → the bar shows "N selected" → 선택 승인 → confirm → the progress bar advances one item at a time; on completion the result shows all succeeded; the list refetches once and those rows move to Approved.
2. **Partial failure + retry:** open the same request in a second tab and approve it there first; in the first tab bulk-approve a selection that includes it → that item appears under failed with the server message ("Request already approved"), the others succeed, the failed one stays checked, and pressing 선택 승인 again processes only it.
3. **Mixed types:** select one join and one budget request → 선택 승인 approves both.
4. **Bulk reject with comment:** select pending requests, 선택 거절, type a comment, confirm → all rejected and the comment is saved (open a rejected row's detail → the review comment is present).
5. **Select-all header:** the header checkbox selects/clears the current page's pending rows and shows the indeterminate state when only some are selected; selections persist when paging.
6. **No regression:** the per-row single Approve/Reject buttons and their dialog still work; the dialog cannot be dismissed while a bulk run is in progress.
7. **Locale:** toggle to English and back — all new bar/dialog strings render in both locales.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/app/\(app\)/admin/requests/page.tsx
git commit -m "feat(requests): bulk approve/reject with selection + progress dialog"
```

---

## Verification (whole feature)

From `frontend/`:

```bash
npx tsc --noEmit      # exit 0
npm run lint          # no new errors
node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');console.log(Object.keys(en.adminRequests).length===Object.keys(ko.adminRequests).length ? 'i18n parity OK' : 'PARITY MISMATCH')"
```

Then the Task 4 Step 8 manual E2E as the behavioral gate. Backend is untouched, so no backend test run is required for this feature.
