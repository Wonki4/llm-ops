# Benchmark Single-Run Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the benchmark sweep feature and reshape the single-run benchmark form so a load preset seeds a live, read-only `vllm bench serve` command with a free-text "additional flags" field appended verbatim, and every run carries an optional label + note.

**Architecture:** Delete all sweep code/table/UI; keep the presets endpoint + service. The existing single-run form (`/admin/benchmarks/new`) keeps its two target modes (endpoint = direct/model; serving = clone) and its accuracy path; its raw perf-params block is replaced by preset cards → command preview → append-flags. Label/note become columns on `custom_benchmark_run`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend); Next.js app router + react-query + next-intl (frontend).

## Global Constraints

- Work on branch `feat/benchmark-single-run-redesign` (already created off `origin/main`; the spec commit `dfef9bd` is on it). Never commit the `litellm` submodule.
- Backend from `backend/`: tests `.venv/bin/python -m pytest <file> -q`; lint `.venv/bin/ruff check <files>`. Frontend from `frontend/`: `npx tsc --noEmit`, `npm run build`.
- Backend suite has ~21 PRE-EXISTING failures (real-PG/event-loop) + 2 Pydantic deprecation warnings on import. Gate = 0 NEW failures vs the pre-task baseline. To baseline-compare: `git stash`-free diff of `pytest tests/ -q | grep FAILED | sort` before/after, or confirm no failing test name is one you touched.
- Every new i18n key goes into BOTH `frontend/messages/en.json` and `frontend/messages/ko.json`.
- Load presets are fixed (from `backend/app/services/benchmark_presets.py`, KEPT): `chat` = input 512 / output 256 / 300 prompts / max_concurrency 32; `long_input` = 4096 / 512 / 120 / 8; `long_output` = 256 / 1024 / 200 / 16; common `seed=0`, `ignore_eos=True`.
- The bench argv is built by `_vllm_bench_argv` in `backend/app/services/benchmark_manifests.py` (UNCHANGED authoritative source). Extra flags flow through `params["extra_args"]` (`shlex.split` appended). The frontend command preview mirrors this but is presentational only.
- `GET /api/benchmarks/presets` must keep working (same URL) after the sweeps module is deleted.

---

### Task 1: Retire the sweep backend (code only; DB untouched)

**Files:**
- Delete: `backend/app/api/benchmark_sweeps.py`, `backend/app/services/benchmark_sweeps.py`, `backend/app/db/models/custom_benchmark_sweep.py`, `backend/tests/test_benchmark_sweep_helpers.py`, `backend/tests/test_benchmark_sweeps_api.py`, `backend/tests/test_reconcile_sweeps.py`
- Modify: `backend/app/main.py`, `backend/app/jobs/reconcile_benchmarks.py`, `backend/app/api/benchmarks.py`, `backend/app/db/models/custom_benchmark_run.py`

**Interfaces:**
- Produces: `GET /api/benchmarks/presets` served from `benchmarks.py` returning `{"presets": LOAD_PRESETS}`; `custom_benchmark_run` model without the 4 sweep columns; `_serialize` without sweep fields. Consumed by Tasks 2–5.
- Note: `benchmark_presets.py` service is KEPT. `custom_benchmark_run` isn't in `models/__init__.py`; nothing else imports the sweep model after this task.

- [ ] **Step 1: Move the presets handler into `benchmarks.py`**

In `backend/app/api/benchmarks.py`, add these imports near the existing service imports:

```python
from app.services.benchmark_presets import LOAD_PRESETS
```

Add this route (place it right after the `router = APIRouter(...)` line so it precedes `GET /{run_id}`):

```python
@router.get("/presets")
async def list_presets(user: CustomUser = Depends(require_super_user)) -> dict:
    """The fixed load presets — the benchmark measurement methodology."""
    return {"presets": LOAD_PRESETS}
```

- [ ] **Step 2: Remove sweep fields from `_serialize`**

In `backend/app/api/benchmarks.py::_serialize` (around line 245), delete these three lines:

```python
        "sweep_id": str(r.sweep_id) if r.sweep_id else None,
        "sweep_index": r.sweep_index,
        "sweep_combo": r.sweep_combo,
```

- [ ] **Step 3: Drop the sweep router from `main.py`**

In `backend/app/main.py`: remove `benchmark_sweeps,` from the `from app.api import (...)` block, and delete the two lines:

```python
# Must precede benchmarks.router: its /presets and /sweeps paths would
# otherwise be captured by GET /api/benchmarks/{run_id}.
app.include_router(benchmark_sweeps.router)
```

Keep `app.include_router(benchmarks.router)`.

- [ ] **Step 4: Remove `_drive_sweeps` from the reconciler**

In `backend/app/jobs/reconcile_benchmarks.py`:
- Delete the imports `from app.db.models.custom_benchmark_sweep import CustomBenchmarkSweep` and `from app.services.benchmark_sweeps import promote_queued_run`.
- Delete the `ACTIVE = ("provisioning", "pending", "running")` constant and the entire `async def _drive_sweeps(db) -> int:` function (lines ~202–243).
- In `reconcile_once`, delete the block:

```python
            # ❸ Sweeps: promote the next queued combo / complete finished sweeps.
            transitions += await _drive_sweeps(db)
```

- [ ] **Step 5: Remove the 4 sweep columns from the run model**

In `backend/app/db/models/custom_benchmark_run.py`, delete the sweep column block (the comment + `sweep_id`, `sweep_index`, `sweep_combo`, and the `queued_job_manifest` comment + column):

```python
    # Sweep membership: a sweep's combos are ordinary runs ordered by
    # sweep_index; sweep_combo holds the flag->value map for display.
    sweep_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_benchmark_sweep.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sweep_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sweep_combo: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Job manifest prebuilt at submit (freeze-at-submit), created on promotion
    # then cleared. Embeds the bench API key — NEVER serialized by the API.
    queued_job_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

If `Integer` / `ForeignKey` become unused after this, remove them from the `from sqlalchemy import ...` line (check: `ForeignKey` is still used by `cluster_id`; `Integer` is likely now unused — remove it only if no other column uses it).

- [ ] **Step 6: Delete the sweep source + test files**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git rm backend/app/api/benchmark_sweeps.py backend/app/services/benchmark_sweeps.py backend/app/db/models/custom_benchmark_sweep.py backend/tests/test_benchmark_sweep_helpers.py backend/tests/test_benchmark_sweeps_api.py backend/tests/test_reconcile_sweeps.py
```

- [ ] **Step 7: Verify — app imports, presets works, no new failures**

Run: `cd backend && .venv/bin/python -c "import app.main; print('ok')" && .venv/bin/python -m pytest tests/test_benchmarks_ephemeral.py tests/test_reconcile_benchmarks.py -q 2>&1 | tail -3 && .venv/bin/ruff check app/main.py app/api/benchmarks.py app/jobs/reconcile_benchmarks.py app/db/models/custom_benchmark_run.py`
Expected: `ok`; the two suites pass; ruff no new findings (the pre-existing `I001` on reconcile imports may remain — confirm it's not newly introduced by comparing to `git show HEAD:backend/app/jobs/reconcile_benchmarks.py | .venv/bin/ruff check --stdin-filename app/jobs/reconcile_benchmarks.py -`).

- [ ] **Step 8: Commit**

```bash
git add -A backend/app backend/tests
git commit -m "refactor(bench): retire sweep backend (code); keep presets endpoint"
```

---

### Task 2: label/note columns + migration 042

**Files:**
- Modify: `backend/app/db/models/custom_benchmark_run.py`
- Create: `backend/migrations/versions/042_benchmark_labels_drop_sweeps.py`
- Modify: `backend/app/api/benchmarks.py` (`CreateBenchmarkRequest`, `create_benchmark`, `preview_benchmark`, `_serialize`)
- Test: `backend/tests/test_benchmark_label_note.py`

**Interfaces:**
- Consumes: Task 1's run model (no sweep columns), `_serialize`.
- Produces: `custom_benchmark_run.label: str|None`, `note: str|None`; `CreateBenchmarkRequest.label/note`; `_serialize` returns `label`/`note`.

- [ ] **Step 1: Add label/note to the model**

In `backend/app/db/models/custom_benchmark_run.py`, add after the `params` column (before `cluster_id`):

```python
    # Optional human metadata for identifying/comparing runs.
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(`String` and `Text` are already imported.)

- [ ] **Step 2: Write migration 042**

```python
# backend/migrations/versions/042_benchmark_labels_drop_sweeps.py
"""Add label/note to benchmark runs; drop the retired sweep table + columns.

Revision ID: 042_benchmark_labels_drop_sweeps
Revises: 041_benchmark_sweeps
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "042_benchmark_labels_drop_sweeps"
down_revision = "041_benchmark_sweeps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_benchmark_run", sa.Column("label", sa.String(128), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("note", sa.Text(), nullable=True))
    op.drop_column("custom_benchmark_run", "queued_job_manifest")
    op.drop_column("custom_benchmark_run", "sweep_combo")
    op.drop_column("custom_benchmark_run", "sweep_index")
    op.drop_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run")
    op.drop_column("custom_benchmark_run", "sweep_id")
    op.drop_table("custom_benchmark_sweep")


def downgrade() -> None:
    op.create_table(
        "custom_benchmark_sweep",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("deployment_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("external_source", JSONB(), nullable=True),
        sa.Column(
            "cluster_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("k8s_namespace", sa.String(128), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("variables", JSONB(), nullable=False),
        sa.Column("serving_overrides", JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running", index=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column(
            "sweep_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_benchmark_sweep.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run", ["sweep_id"])
    op.add_column("custom_benchmark_run", sa.Column("sweep_index", sa.Integer(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("sweep_combo", JSONB(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("queued_job_manifest", JSONB(), nullable=True))
    op.drop_column("custom_benchmark_run", "note")
    op.drop_column("custom_benchmark_run", "label")
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_benchmark_label_note.py
"""Label + note are persisted on create and returned by the serializer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_persists_label_and_note(client_for_user, super_user, mock_db):
    fake_k8s = MagicMock()
    fake_k8s.create_job = AsyncMock()
    body = {
        "tool": "vllm_serving",
        "model_name": "m",
        "params": {"num_prompts": 10},
        "label": "baseline-h100",
        "note": "first pass before tuning",
    }
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=fake_k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 201, resp.text
    run = mock_db.add.call_args.args[0]
    assert run.label == "baseline-h100"
    assert run.note == "first pass before tuning"
    assert resp.json()["label"] == "baseline-h100"
    assert resp.json()["note"] == "first pass before tuning"
```

- [ ] **Step 4: Run — expect FAIL**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_label_note.py -q`
Expected: FAIL (`run.label` AttributeError or 201-body missing `label`).

- [ ] **Step 5: Wire label/note through the API**

In `backend/app/api/benchmarks.py`:

Add to `CreateBenchmarkRequest` (after `api_key`):

```python
    label: str | None = Field(None, description="Short human identifier for the run")
    note: str | None = Field(None, description="Free-form note about the run")
```

In `_serialize`, add (after `"params": r.params,` or anywhere in the dict):

```python
        "label": r.label,
        "note": r.note,
```

In `create_benchmark`, every `CustomBenchmarkRun(...)` constructor must set `label=(body.label or "").strip() or None, note=(body.note or "").strip() or None`. There are multiple constructors (external, ephemeral-perf, ephemeral-accuracy, direct/legacy). Add the two kwargs to each. `preview_benchmark` does NOT persist and needs no change (it builds a throwaway run; label/note are irrelevant to the manifest).

- [ ] **Step 6: Run — expect PASS**

Run: `cd backend && .venv/bin/python -m pytest tests/test_benchmark_label_note.py -q && .venv/bin/ruff check app/api/benchmarks.py app/db/models/custom_benchmark_run.py migrations/versions/042_benchmark_labels_drop_sweeps.py`
Expected: PASS; ruff clean on new file.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models/custom_benchmark_run.py backend/migrations/versions/042_benchmark_labels_drop_sweeps.py backend/app/api/benchmarks.py backend/tests/test_benchmark_label_note.py
git commit -m "feat(bench): label + note on runs (migration 042 also drops retired sweeps)"
```

---

### Task 3: Retire the sweep frontend + sweep types/hooks

**Files:**
- Delete: `frontend/src/app/(app)/admin/benchmarks/sweeps/new/page.tsx`, `frontend/src/app/(app)/admin/benchmarks/sweeps/[id]/page.tsx`
- Modify: `frontend/src/app/(app)/admin/benchmarks/page.tsx`, `frontend/src/types/index.ts`, `frontend/src/hooks/use-api.ts`

**Interfaces:**
- Produces: a benchmarks list page with no sweeps tab; `BenchmarkRun` without `sweep_*` fields and without the `queued` status; `useBenchmarkPresets` KEPT, all other sweep hooks removed. Consumed by Tasks 4–5.

- [ ] **Step 1: Delete the sweep pages**

```bash
cd /Users/wongibaek/Documents/litellm-ops
git rm "frontend/src/app/(app)/admin/benchmarks/sweeps/new/page.tsx" "frontend/src/app/(app)/admin/benchmarks/sweeps/[id]/page.tsx"
rmdir "frontend/src/app/(app)/admin/benchmarks/sweeps/[id]" "frontend/src/app/(app)/admin/benchmarks/sweeps" 2>/dev/null || true
```

- [ ] **Step 2: Remove sweep types**

In `frontend/src/types/index.ts`:
- Remove `"queued"` from the `BenchmarkStatus` union (line ~511).
- Remove `sweep_id`, `sweep_index`, `sweep_combo` from `BenchmarkRun` (lines ~553–555).
- Add to `BenchmarkRun` (after `params`): `label: string | null;` and `note: string | null;`.
- Delete `SweepStatus`, `SweepVariable`, `BenchmarkSweep`, `CreateBenchmarkSweepRequest` interfaces/types. Keep `LoadPreset`.

- [ ] **Step 3: Remove sweep hooks**

In `frontend/src/hooks/use-api.ts`: delete `useBenchmarkSweeps`, `useBenchmarkSweep`, `useCreateBenchmarkSweep`, `useCancelBenchmarkSweep`. KEEP `useBenchmarkPresets`. Remove now-unused type imports (`BenchmarkSweep`, `CreateBenchmarkSweepRequest`) from the `@/types` import; keep `LoadPreset`.

- [ ] **Step 4: De-sweep the list page**

In `frontend/src/app/(app)/admin/benchmarks/page.tsx`:
- Remove the `Tabs`/`TabsList`/`TabsTrigger`/`TabsContent` wrapper that split runs/sweeps; render the filter bar + runs table directly (unwrap the `runs` TabsContent to top level).
- Delete the `SweepsTable` component and its `useBenchmarkSweeps`/`BenchmarkSweep`/`Layers` imports and the "new sweep" header button.
- Remove `"queued"` from `STATUS_STYLES` and `STATUS_OPTIONS`.

- [ ] **Step 5: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean (no dangling sweep imports/types). If tsc flags a leftover reference, remove it.

- [ ] **Step 6: Commit**

```bash
git add -A "frontend/src/app/(app)/admin/benchmarks" frontend/src/types/index.ts frontend/src/hooks/use-api.ts
git commit -m "refactor(frontend): retire sweep UI, types, and hooks; keep presets"
```

---

### Task 4: Preset cards → live command preview → append-flags on the run form

**Files:**
- Create: `frontend/src/lib/bench-command.ts`
- Modify: `frontend/src/app/(app)/admin/benchmarks/new/page.tsx`
- Test: none (frontend has no unit runner; verified via tsc + build + a hand-checked preview string)

**Interfaces:**
- Consumes: `useBenchmarkPresets` (Task 3), `LoadPreset` type.
- Produces: `buildBenchCommand(preset: LoadPreset, extraFlags: string, opts: { model?: string }): string` in `bench-command.ts`; the perf section of the new-run form now emits `params` from the selected preset + `extra_args` from the flags field.

- [ ] **Step 1: Command-preview helper**

```ts
// frontend/src/lib/bench-command.ts
import type { LoadPreset } from "@/types";

/**
 * Render the `vllm bench serve` command a performance run will execute, for a
 * live read-only preview. Mirrors backend `_vllm_bench_argv`; the backend
 * remains authoritative — this is presentational. Target-derived flags
 * (base-url/model/tokenizer/result-*) are shown as placeholders when unknown.
 */
export function buildBenchCommand(
  preset: LoadPreset,
  extraFlags: string,
  opts: { model?: string } = {},
): string {
  const model = opts.model?.trim() || "<model>";
  const parts = [
    "vllm bench serve",
    "--backend openai-chat",
    "--base-url <target>",
    "--endpoint /v1/chat/completions",
    `--model ${model}`,
    `--tokenizer ${model}`,
    "--dataset-name random",
    `--random-input-len ${preset.random_input_len}`,
    `--random-output-len ${preset.random_output_len}`,
    `--num-prompts ${preset.num_prompts}`,
    "--percentile-metrics ttft,tpot,itl,e2el",
    "--metric-percentiles 90,99",
    "--seed 0",
    "--save-result --result-dir /tmp --result-filename r.json",
    `--max-concurrency ${preset.max_concurrency}`,
    "--ignore-eos",
  ];
  const extra = extraFlags.trim();
  if (extra) parts.push(extra);
  return parts.join(" \\\n  ");
}
```

- [ ] **Step 2: Rework the perf section of the form**

In `frontend/src/app/(app)/admin/benchmarks/new/page.tsx`:
- Add imports: `import { buildBenchCommand } from "@/lib/bench-command";` and `import { useBenchmarkPresets } from "@/hooks/use-api";`.
- Add state: `const [preset, setPreset] = useState("chat");` and `const { data: presets } = useBenchmarkPresets();`. Keep `extraArgsText` (the append-flags field); REMOVE `perfParams`/`DEFAULT_PERF_PARAMS`/`extraParamsText` and the raw perf inputs JSX block.
- Render (only when `kind === "performance"`): preset cards (map `presets`, same card style as the retired sweep form used) + a monospace `<pre>` preview from `buildBenchCommand(presets[preset], extraArgsText, { model: modelName || selectedDeployment?.model_name })` + the "Additional flags" `<Input>` bound to `extraArgsText`.
- In `handleSubmit`, build perf `params` from the selected preset expansion instead of `perfParams`:

```ts
const p = presets?.[preset];
const params: Record<string, unknown> = {
  preset,
  random_input_len: p?.random_input_len,
  random_output_len: p?.random_output_len,
  num_prompts: p?.num_prompts,
  max_concurrency: p?.max_concurrency,
  seed: 0,
  ignore_eos: true,
};
if (extraArgsText.trim()) params.extra_args = extraArgsText.trim();
// keep the existing tokenizer/NFS advanced fields if the form still exposes them
```

Keep the accuracy branch (`accParams`) exactly as it is. Keep `loadFromRun` working for accuracy; for performance, restore `preset` from `run.params.preset` (fallback `"chat"`) and `extraArgsText` from `run.params.extra_args`.

- [ ] **Step 3: i18n keys**

Add to `benchmarkForm` namespace in BOTH `en.json` and `ko.json`:
- en: `"presetLabel": "Load preset"`, `"presetChat": "Chat — short in/out"`, `"presetLongInput": "Long input — RAG"`, `"presetLongOutput": "Long output — generation"`, `"commandPreview": "Command"`, `"additionalFlags": "Additional flags"`, `"additionalFlagsHint": "Appended to the command as-is"`.
- ko: `"presetLabel": "부하 프리셋"`, `"presetChat": "채팅 — 짧은 입출력"`, `"presetLongInput": "긴 입력 — RAG"`, `"presetLongOutput": "긴 출력 — 생성"`, `"commandPreview": "명령어"`, `"additionalFlags": "추가 flag"`, `"additionalFlagsHint": "명령어 뒤에 그대로 덧붙습니다"`.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | grep -E "Compiled|Failed|error" | head`
Expected: tsc clean; build compiles. Hand-check: selecting `chat` renders a preview containing `--random-input-len 512 ... --num-prompts 300 ... --max-concurrency 32`, and typing `--request-rate 8` appends it at the end.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/bench-command.ts "frontend/src/app/(app)/admin/benchmarks/new/page.tsx" frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): preset cards + live vllm bench serve preview + append-flags"
```

---

### Task 5: label/note in the form, list, detail, compare

**Files:**
- Modify: `frontend/src/app/(app)/admin/benchmarks/new/page.tsx`, `frontend/src/app/(app)/admin/benchmarks/page.tsx`, `frontend/src/app/(app)/admin/benchmarks/[id]/page.tsx`, `frontend/src/app/(app)/admin/benchmarks/compare/page.tsx`, `frontend/messages/en.json`, `frontend/messages/ko.json`
- Modify: `frontend/src/hooks/use-api.ts` (`CreateBenchmarkRequest` type / mutation body), `frontend/src/types/index.ts` (`CreateBenchmarkRequest`)

**Interfaces:**
- Consumes: backend `label`/`note` (Task 2), `BenchmarkRun.label/note` (Task 3).
- Produces: form submits `label`/`note`; list shows a Label column; detail shows label + note; compare headers show label.

- [ ] **Step 1: Add label/note to the request type + form**

In `frontend/src/types/index.ts`, add `label?: string;` and `note?: string;` to `CreateBenchmarkRequest`.

In `frontend/src/app/(app)/admin/benchmarks/new/page.tsx`: add `const [label, setLabel] = useState("");` and `const [note, setNote] = useState("");`; render a **Label** `<Input>` and a **Note** `<textarea>` (shown for both kinds, near the top of the form); include `label: label.trim() || undefined, note: note.trim() || undefined` in the submit body. In `loadFromRun`, restore `setLabel(run.label ?? "")` and `setNote(run.note ?? "")`.

- [ ] **Step 2: Label column in the list**

In `frontend/src/app/(app)/admin/benchmarks/page.tsx`: add a `<TableHead>{t("colLabel")}</TableHead>` (after `colModel`) and a `<TableCell>{r.label || "-"}</TableCell>` in the row. Add `"colLabel": "Label"` (en) / `"라벨"` (ko) to the `adminBenchmarks` namespace.

- [ ] **Step 3: label + note on the detail page**

In `frontend/src/app/(app)/admin/benchmarks/[id]/page.tsx`: show `run.label` in the header near the model name (when set), and `run.note` in a small block (when set), using existing detail-row styling. Add `"note": "Note"` (en) / `"메모"` (ko) and reuse `colLabel`.

- [ ] **Step 4: label in compare headers**

In `frontend/src/app/(app)/admin/benchmarks/compare/page.tsx`: where each run's column header renders (currently model/id), prefer `run.label` when set, falling back to the model name. No new i18n needed.

- [ ] **Step 5: i18n keys for the form**

Add to `benchmarkForm` in BOTH locales: `"labelLabel": "Label"` / `"라벨"`, `"labelPlaceholder": "e.g. baseline-h100"` / `"예: baseline-h100"`, `"noteLabel": "Note"` / `"메모"`, `"notePlaceholder": "Optional note"` / `"선택 메모"`.

- [ ] **Step 6: Verify**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | grep -E "Compiled|Failed|error" | head && node -e "const en=require('./messages/en.json'),ko=require('./messages/ko.json');const dk=(o,p)=>Object.entries(o).flatMap(([k,v])=>typeof v==='object'?dk(v,p+k+'.'):[p+k]);const e=new Set(dk(en,'')),k=new Set(dk(ko,''));console.log('en-only:',[...e].filter(x=>!k.has(x)),'ko-only:',[...k].filter(x=>!e.has(x)))"`
Expected: tsc clean; build compiles; i18n parity both arrays empty.

- [ ] **Step 7: Commit**

```bash
git add -A "frontend/src/app/(app)/admin/benchmarks" frontend/src/types/index.ts frontend/src/hooks/use-api.ts frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(frontend): label + note on the run form, list, detail, and compare"
```

---

### Task 6: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite + lint (0 new vs baseline)**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3 && .venv/bin/ruff check app tests migrations/versions/042_benchmark_labels_drop_sweeps.py 2>&1 | tail -3`
Expected: no NEW failures vs the origin/main baseline (21 pre-existing); ruff 0 new. No `test_benchmark_sweep*` / `test_reconcile_sweeps` remain.

- [ ] **Step 2: Migration reversibility (scratch DB, if available)**

Run: `cd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head` (only if a dev DB is configured; otherwise inspect 042 up/down symmetry by eye — every `add_column` has a matching `drop_column` and vice versa, `drop_table` ↔ `create_table`).

- [ ] **Step 3: Frontend typecheck + build + i18n parity**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | grep -E "Compiled|Failed" | head`
Expected: clean; compiles. Confirm no `/admin/benchmarks/sweeps` route remains in the build output.

- [ ] **Step 4: Grep for dangling sweep references**

Run: `cd /Users/wongibaek/Documents/litellm-ops && grep -rniE "sweep" backend/app frontend/src | grep -v node_modules`
Expected: no matches (all sweep code/types/routes gone).

- [ ] **Step 5: Manual QA (needs a cluster)**

- Performance run, endpoint mode (existing deployment or LiteLLM model): pick `chat`, add `--request-rate 8`, set a label + note. Confirm the Job's actual command matches the preview and label/note appear in list/detail/compare.
- Performance run, serving mode (clone): same, confirm the self-serving Job runs.
- `alembic upgrade head` applied cleanly.

- [ ] **Step 6: Wrap up**

Use the superpowers:finishing-a-development-branch skill (PR to `main` from `feat/benchmark-single-run-redesign`).
