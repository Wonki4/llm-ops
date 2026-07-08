# Benchmark Form Mode Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the benchmark form's inferred start-mode (deployment/external/model/ephemeral precedence chain) with four explicit tabs — clone (first, default), direct, LiteLLM model, from-previous-run.

**Architecture:** Frontend-only refactor of `admin/benchmarks/new/page.tsx`. A new `mode` state drives a shadcn `Tabs` block inside the existing Target card; submit/preview build the request body from the active tab's fields only. Request bodies stay byte-identical to today's for every mode. Spec: `docs/superpowers/specs/2026-07-08-benchmark-form-mode-tabs-design.md`.

**Tech Stack:** Next.js App Router, React 19, radix-ui Tabs via `frontend/src/components/ui/tabs.tsx`, next-intl messages.

## Global Constraints

- Tab order and default: **clone first and default active** (`"clone"`), then direct, model, fromRun (user-mandated).
- No backend/API/type changes; outgoing `CreateBenchmarkRequest` bodies identical to current behavior per mode.
- Clone tab target list: ALL portal deployments (Ready or not, `statusNotReady` suffix kept) + external servings optgroup hidden when the tool kind is `accuracy`.
- Direct tab target list: portal deployments with `ready_replicas > 0` ONLY.
- NFS override fields render only in model mode; NFS params sent only in model mode.
- API key is never restored by loadFromRun (existing rule — do not change).
- Lint gate: `cd frontend && npm run lint` — 0 NEW errors vs the baseline you record before editing (repo baseline: 4 pre-existing errors, 13 warnings).
- Build gate: `cd frontend && npm run build` succeeds.
- Work on branch `feat/bench-form-mode-tabs` cut from current `main`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: i18n keys for the tab layout (en + ko)

**Files:**
- Modify: `frontend/messages/en.json` (inside the `"benchmarkForm"` object)
- Modify: `frontend/messages/ko.json` (inside the `"benchmarkForm"` object)

**Interfaces:**
- Consumes: nothing.
- Produces: message keys `tabClone`, `tabDirect`, `tabModel`, `tabFromRun`, `tabCloneHint`, `tabDirectHint`, `fromRunEmpty` under the `benchmarkForm` namespace, referenced by Task 2 as `t("tabClone")` etc. Removes keys `modeCloneLabel`, `modeCloneHint`, `modeDirectLabel`, `modeDirectHint`, `modeDirectUnavailable`, `modelHintDisabled` (their render sites disappear in Task 2).

There is no frontend test framework; the verification cycle for this task is a JSON key check script (below) run before and after the edit.

- [ ] **Step 1: Record the "failing" state**

Run from the repo root:

```bash
python3 - <<'EOF'
import json, sys
missing = []
for lang in ("en", "ko"):
    bf = json.load(open(f"frontend/messages/{lang}.json"))["benchmarkForm"]
    for key in ("tabClone","tabDirect","tabModel","tabFromRun","tabCloneHint","tabDirectHint","fromRunEmpty"):
        if key not in bf:
            missing.append(f"{lang}:{key}")
    for key in ("modeCloneLabel","modeCloneHint","modeDirectLabel","modeDirectHint","modeDirectUnavailable","modelHintDisabled"):
        if key in bf:
            missing.append(f"{lang}:{key} STILL PRESENT")
print("FAIL: " + ", ".join(missing) if missing else "PASS")
EOF
```

Expected: `FAIL:` listing all 7 new keys missing in both languages and all 6 old keys still present.

- [ ] **Step 2: Add the new keys and remove the retired keys**

In `frontend/messages/en.json`, inside the `"benchmarkForm": { ... }` object, add:

```json
"tabClone": "Spin up & bench",
"tabDirect": "Direct to running serving",
"tabModel": "LiteLLM model",
"tabFromRun": "From previous run",
"tabCloneHint": "Clones the selected serving into a temporary replica-1 deployment, benchmarks it, then tears it down. Not-Ready deployments and externally discovered servings can be benchmarked this way.",
"tabDirectHint": "Sends benchmark load to the already-running serving. No new resources are created — live pods take the traffic. Only Ready portal deployments are listed.",
"fromRunEmpty": "No previous runs yet — run a benchmark once and it will appear here.",
```

and delete the six entries `"modeCloneLabel"`, `"modeCloneHint"`, `"modeDirectLabel"`, `"modeDirectHint"`, `"modeDirectUnavailable"`, `"modelHintDisabled"`.

In `frontend/messages/ko.json`, inside `"benchmarkForm": { ... }`, add:

```json
"tabClone": "새로 띄워서 벤치",
"tabDirect": "실행 중 서빙에 직접",
"tabModel": "LiteLLM 모델",
"tabFromRun": "이전 실행",
"tabCloneHint": "선택한 서빙을 임시 복제본(replica 1)으로 새로 띄워 벤치마크한 뒤 자동으로 정리합니다. Not Ready 배포와 외부에서 감지된 서빙도 이 방식으로 벤치할 수 있습니다.",
"tabDirectHint": "이미 실행 중인 서빙에 벤치마크 부하를 보냅니다. 새 리소스를 만들지 않으며 실제 파드가 트래픽을 받습니다. Ready 상태의 포털 배포만 표시됩니다.",
"fromRunEmpty": "이전 실행이 없습니다 — 벤치마크를 한 번 실행하면 여기에 표시됩니다.",
```

and delete the same six entries.

Keep JSON valid (watch trailing commas — match the file's existing style; keys in these files are not alphabetized, so append the new keys next to the existing `loadFrom*` keys for locality).

- [ ] **Step 3: Verify the key check passes**

Re-run the Step 1 script. Expected: `PASS`.

- [ ] **Step 4: Verify no dangling references to removed keys**

```bash
grep -rn "modeCloneLabel\|modeCloneHint\|modeDirectLabel\|modeDirectHint\|modeDirectUnavailable\|modelHintDisabled" frontend/src frontend/messages || echo "CLEAN"
```

Expected at this point: matches ONLY in `frontend/src/app/(app)/admin/benchmarks/new/page.tsx` (Task 2 removes those render sites — that is fine and expected; do NOT edit page.tsx in this task). No matches in `frontend/messages`.

- [ ] **Step 5: Commit**

```bash
git add frontend/messages/en.json frontend/messages/ko.json
git commit -m "feat(i18n): benchmark form tab labels; retire radio-mode keys

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Note for the reviewer: between Task 1 and Task 2 the app would throw on the benchmark form (page.tsx still references removed keys). That is acceptable mid-branch state; Task 2 lands in the same PR.

---

### Task 2: Tab-based mode layout in the benchmark form

**Files:**
- Modify: `frontend/src/app/(app)/admin/benchmarks/new/page.tsx` (whole component; current file is 1074 lines)

**Interfaces:**
- Consumes: message keys from Task 1 (`tabClone`, `tabDirect`, `tabModel`, `tabFromRun`, `tabCloneHint`, `tabDirectHint`, `fromRunEmpty`); `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` from `@/components/ui/tabs`.
- Produces: nothing consumed downstream; this is the final task.

Read the current file fully before editing. The steps below give complete replacement code for each region; everything not mentioned stays as-is (params cards, extras card, advanced card, preview aside, `PerfParamsFields`, `AccuracyParamsFields`, `NumberField`, `OptionalNumberField`).

- [ ] **Step 1: Baseline lint**

```bash
cd frontend && npm run lint 2>&1 | tail -5
```

Record the error/warning counts (expected baseline: 4 errors, 13 warnings, none in this file).

- [ ] **Step 2: Imports and state**

Add to the imports from `@/components/ui/tabs`:

```tsx
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
```

Immediately above the `export default function NewBenchmarkPage()` line (after the `DEFAULT_ACCURACY_PARAMS` const), add:

```tsx
// The active tab IS the start mode — no inference from which fields are set.
type BenchMode = "clone" | "direct" | "model" | "fromRun";
```

Inside the component, replace the state line

```tsx
  const [ephemeral, setEphemeral] = useState(true);
```

with

```tsx
  const [mode, setMode] = useState<BenchMode>("clone");
```

(`ephemeral` state is gone; clone-ness now derives from `mode === "clone"`.)

- [ ] **Step 3: Derived values and the clone-tab change handler**

Replace this block (currently lines 178–203):

```tsx
  const allDeployments = deployments ?? [];
  const selectedDeployment = allDeployments.find((d) => d.id === deploymentId) ?? null;

  // Clone (ephemeral) mode is forced when the portal deployment isn't Ready, or
  // when the target is an external serving — the live pod isn't ours to hit.
  const directModeDisabled =
    !!externalTarget || (!!selectedDeployment && selectedDeployment.ready_replicas === 0);

  const handleTargetChange = (value: string) => {
    if (value.startsWith("ext::")) {
      const serving = servings.find((s) => externalKey(s) === value) ?? null;
      setExternalTarget(serving);
      setDeploymentId("");
      setEphemeral(true);
    } else if (value) {
      setExternalTarget(null);
      setDeploymentId(value);
      const dep = allDeployments.find((d) => d.id === value);
      if (dep && dep.ready_replicas === 0) {
        setEphemeral(true);
      }
    } else {
      setExternalTarget(null);
      setDeploymentId("");
    }
  };
```

with:

```tsx
  const allDeployments = deployments ?? [];
  const readyDeployments = allDeployments.filter((d) => d.ready_replicas > 0);
  const selectedDeployment = allDeployments.find((d) => d.id === deploymentId) ?? null;

  // The external target only participates while the clone tab is active;
  // leftover selections on inactive tabs never leak into cluster/namespace
  // autofill or the outgoing body.
  const activeExternal = mode === "clone" ? externalTarget : null;

  // deploymentId is shared between the clone and direct tabs (same target,
  // different mode); the direct tab simply won't resolve a not-Ready id.
  const directDeploymentId = readyDeployments.some((d) => d.id === deploymentId)
    ? deploymentId
    : "";

  const handleCloneTargetChange = (value: string) => {
    if (value.startsWith("ext::")) {
      const serving = servings.find((s) => externalKey(s) === value) ?? null;
      setExternalTarget(serving);
      setDeploymentId("");
    } else {
      setExternalTarget(null);
      setDeploymentId(value);
    }
  };
```

Keep the accuracy `useEffect` that clears `externalTarget` exactly as-is (lines 205–211).

- [ ] **Step 4: loadFromRun sets the tab**

In `loadFromRun` (currently lines 98–170), replace the target-restoration branch

```tsx
    if (run.deployment_id) {
      setDeploymentId(run.deployment_id);
      setModelName("");
      // Restore the run's mode, but force clone when the deployment is no
      // longer Ready — direct mode is disabled for it (keeps radio state
      // consistent with directModeDisabled).
      const dep = (deployments ?? []).find((d) => d.id === run.deployment_id);
      setEphemeral(dep && dep.ready_replicas === 0 ? true : run.ephemeral);
    } else {
      setDeploymentId("");
      setEphemeral(false);
      setModelName(run.model_name);
    }
```

with:

```tsx
    if (run.deployment_id) {
      setDeploymentId(run.deployment_id);
      setModelName("");
      // Restore the run's mode, but land on the clone tab when the
      // deployment is no longer Ready — the direct tab won't list it.
      const dep = (deployments ?? []).find((d) => d.id === run.deployment_id);
      const forceClone = dep ? dep.ready_replicas === 0 : false;
      setMode(run.ephemeral || forceClone ? "clone" : "direct");
    } else {
      setDeploymentId("");
      setModelName(run.model_name);
      setMode("model");
    }
```

Everything else in `loadFromRun` (tool, cluster, namespace, image, params restoration, `setExternalTarget(null)`, no API-key restore) stays unchanged.

- [ ] **Step 5: NFS params keyed off mode**

In `buildNamedParams`, replace

```tsx
      // NFS override only applies to a raw model_name target; deployment and
      // external-clone targets mount their own PVC.
      const usesOwnPvc = !!deploymentId || !!externalTarget;
```

with

```tsx
      // NFS override only applies to a raw model_name target; deployment and
      // external-clone targets mount their own PVC.
      const usesOwnPvc = mode !== "model";
```

- [ ] **Step 6: Per-mode preview body**

Replace the `previewBody` memo (currently lines 320–365) with:

```tsx
  // Best-effort body for the live YAML preview (never throws on bad JSON).
  // Built from the ACTIVE tab's fields only — leftovers on other tabs are ignored.
  const previewBody = useMemo((): CreateBenchmarkRequest | null => {
    const hasTarget =
      mode === "clone"
        ? !!(deploymentId || externalTarget)
        : mode === "direct"
          ? !!directDeploymentId
          : mode === "model"
            ? !!modelName.trim()
            : false;
    if (!hasTarget) return null;
    const extras = parseExtras();
    const body: CreateBenchmarkRequest = {
      tool,
      params: {
        ...buildNamedParams(),
        ...(extras.ok ? extras.value : {}),
        ...(kind === "performance" && extraArgsText.trim() ? { extra_args: extraArgsText.trim() } : {}),
      },
    };
    if (mode === "clone" && externalTarget) {
      // Perf-only clone of a discovered serving; the backend derives
      // placement (cluster/namespace) from external_target itself.
      body.external_target = {
        cluster_id: externalTarget.cluster_id,
        namespace: externalTarget.namespace,
        deployment_name: externalTarget.deployment_name,
      };
      const overrides = parseServingOverrides();
      if (overrides.ok && overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "clone") {
      body.deployment_id = deploymentId;
      body.ephemeral = true;
      const overrides = parseServingOverrides();
      if (overrides.ok && overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "direct") {
      body.deployment_id = directDeploymentId;
    } else {
      body.model_name = modelName.trim();
    }
    if (!(mode === "clone" && externalTarget)) {
      if (clusterId) body.cluster_id = clusterId;
      if (namespace.trim()) body.namespace = namespace.trim();
    }
    if (image.trim()) body.image = image.trim();
    if (apiKey.trim()) body.api_key = apiKey.trim();
    return body;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    mode, deploymentId, directDeploymentId, externalTarget, modelName, tool, perfParams,
    accParams, extraParamsText, extraArgsText, servingOverridesText, clusterId,
    namespace, image, apiKey,
  ]);
```

- [ ] **Step 7: Per-mode submit**

Replace the body of `handleSubmit` from its start through the `if (apiKey.trim())` line (currently lines 377–445) with:

```tsx
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "fromRun") return; // submit is disabled on this tab
    if (mode === "clone" && !deploymentId && !externalTarget) {
      toast.error(t("errorTargetRequired"));
      return;
    }
    if (mode === "direct" && !directDeploymentId) {
      toast.error(t("errorTargetRequired"));
      return;
    }
    if (mode === "model" && !modelName.trim()) {
      toast.error(t("errorModelRequired"));
      return;
    }
    if (kind === "accuracy") {
      const tasks = accParams.tasks
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (tasks.length === 0) {
        toast.error(t("errorTasksRequired"));
        return;
      }
    }
    const extras = parseExtras();
    if (!extras.ok) {
      toast.error(extras.error);
      return;
    }

    const body: CreateBenchmarkRequest = {
      tool,
      // Extras override named so users can correct any field via JSON.
      params: {
        ...buildNamedParams(),
        ...extras.value,
        ...(kind === "performance" && extraArgsText.trim() ? { extra_args: extraArgsText.trim() } : {}),
      },
    };
    if (mode === "clone" && externalTarget) {
      body.external_target = {
        cluster_id: externalTarget.cluster_id,
        namespace: externalTarget.namespace,
        deployment_name: externalTarget.deployment_name,
      };
      const overrides = parseServingOverrides();
      if (!overrides.ok) {
        toast.error(overrides.error);
        return;
      }
      if (overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "clone") {
      body.deployment_id = deploymentId;
      body.ephemeral = true;
      const overrides = parseServingOverrides();
      if (!overrides.ok) {
        toast.error(overrides.error);
        return;
      }
      if (overrides.value) body.serving_overrides = overrides.value;
    } else if (mode === "direct") {
      body.deployment_id = directDeploymentId;
    } else {
      body.model_name = modelName.trim();
    }
    // The backend derives placement from external_target itself; keep the
    // outgoing body honest and skip these for external runs.
    if (!(mode === "clone" && externalTarget)) {
      if (clusterId) body.cluster_id = clusterId;
      if (namespace.trim()) body.namespace = namespace.trim();
    }
    if (image.trim()) body.image = image.trim();
    if (apiKey.trim()) body.api_key = apiKey.trim();
```

The trailing `createMutation.mutate(body, { ... })` call stays unchanged.

- [ ] **Step 8: Target card render — tabs**

Replace the Target card's `<CardContent>` (currently lines 477–655: the load-from block, cluster block, deployment block, radio block, model block, tool block) with the code below. The cluster select stays above the tabs; the tool select stays below them; both remain inside the same card.

```tsx
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="cluster">{t("clusterLabel")}</Label>
              <select
                id="cluster"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                value={activeExternal ? activeExternal.cluster_id ?? "" : clusterId}
                onChange={(e) => setClusterId(e.target.value)}
                disabled={!!activeExternal}
              >
                <option value="">{t("clusterDefault")}</option>
                {(clusters ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.is_default ? " ★" : ""}
                    {c.api_server ? ` — ${c.api_server}` : ""}
                  </option>
                ))}
                {activeExternal &&
                  activeExternal.cluster_id &&
                  !(clusters ?? []).some((c) => c.id === activeExternal.cluster_id) && (
                    <option value={activeExternal.cluster_id}>{activeExternal.cluster_name}</option>
                  )}
              </select>
              <p className="text-xs text-muted-foreground">{t("clusterHint")}</p>
            </div>

            <Tabs value={mode} onValueChange={(v) => setMode(v as BenchMode)}>
              <TabsList className="w-full">
                <TabsTrigger value="clone">{t("tabClone")}</TabsTrigger>
                <TabsTrigger value="direct">{t("tabDirect")}</TabsTrigger>
                <TabsTrigger value="model">{t("tabModel")}</TabsTrigger>
                <TabsTrigger value="fromRun">{t("tabFromRun")}</TabsTrigger>
              </TabsList>

              <TabsContent value="clone" className="space-y-4 pt-2">
                <p className="text-xs text-muted-foreground">{t("tabCloneHint")}</p>
                <div className="space-y-1.5">
                  <Label htmlFor="clone_target">{t("deploymentLabel")}</Label>
                  <select
                    id="clone_target"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    value={externalTarget ? externalKey(externalTarget) : deploymentId}
                    onChange={(e) => handleCloneTargetChange(e.target.value)}
                  >
                    <option value="">{t("deploymentNone")}</option>
                    <optgroup label={t("targetGroupPortal")}>
                      {allDeployments.map((d) => {
                        const gpu = d.node_selector?.["gpu-type"] ?? d.gpu_resource_key;
                        return (
                          <option key={d.id} value={d.id}>
                            {d.model_name} — {d.gpu_count}×{gpu}
                            {d.memory_limit ? ` · ${d.memory_limit}` : ""}
                            {d.ready_replicas > 0 ? "" : ` · ${t("statusNotReady")}`}
                          </option>
                        );
                      })}
                    </optgroup>
                    {kind !== "accuracy" && servings.length > 0 && (
                      <optgroup label={t("targetGroupExternal")}>
                        {servings.map((s) => (
                          <option key={externalKey(s)} value={externalKey(s)}>
                            {s.deployment_name} ({s.engine} · {s.namespace})
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("deploymentHint")}</p>
                  {!externalTarget && selectedDeployment && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {selectedDeployment.model_path}
                    </p>
                  )}
                  {externalTarget && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {externalTarget.model_path ?? externalTarget.deployment_name}
                    </p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="serving_overrides">{t("servingOverridesLabel")}</Label>
                  <JsonEditor
                    id="serving_overrides"
                    value={servingOverridesText}
                    onChange={setServingOverridesText}
                    placeholder='{"gpu_count": 2, "gpu_type": "NVIDIA-H100"}'
                    minHeight="min-h-20"
                  />
                  <p className="text-xs text-muted-foreground">{t("servingOverridesHint")}</p>
                </div>
              </TabsContent>

              <TabsContent value="direct" className="space-y-4 pt-2">
                <p className="text-xs text-muted-foreground">{t("tabDirectHint")}</p>
                <div className="space-y-1.5">
                  <Label htmlFor="direct_target">{t("deploymentLabel")}</Label>
                  <select
                    id="direct_target"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    value={directDeploymentId}
                    onChange={(e) => {
                      setExternalTarget(null);
                      setDeploymentId(e.target.value);
                    }}
                  >
                    <option value="">{t("deploymentNone")}</option>
                    {readyDeployments.map((d) => {
                      const gpu = d.node_selector?.["gpu-type"] ?? d.gpu_resource_key;
                      return (
                        <option key={d.id} value={d.id}>
                          {d.model_name} — {d.gpu_count}×{gpu}
                          {d.memory_limit ? ` · ${d.memory_limit}` : ""}
                        </option>
                      );
                    })}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("deploymentHint")}</p>
                  {directDeploymentId && selectedDeployment && (
                    <p className="font-mono text-xs text-muted-foreground">
                      {selectedDeployment.model_path}
                    </p>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="model" className="space-y-4 pt-2">
                <div className="space-y-1.5">
                  <Label htmlFor="model_name">{t("modelLabel")}</Label>
                  <select
                    id="model_name"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    disabled={modelsLoading}
                  >
                    <option value="">{t("modelPlaceholder")}</option>
                    {modelOptions.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">{t("modelHint")}</p>
                </div>
              </TabsContent>

              <TabsContent value="fromRun" className="space-y-4 pt-2">
                {(pastRuns?.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground py-4 text-center">
                    {t("fromRunEmpty")}
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    <Label htmlFor="load_from">{t("loadFromLabel")}</Label>
                    <select
                      id="load_from"
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      value={loadFromId}
                      onChange={(e) => loadFromRun(e.target.value)}
                    >
                      <option value="">{t("loadFromNone")}</option>
                      {(pastRuns ?? []).map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.model_name} · {r.tool} · {r.status}
                          {r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString()}` : ""}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">{t("loadFromHint")}</p>
                  </div>
                )}
              </TabsContent>
            </Tabs>

            <div className="space-y-1.5">
              <Label htmlFor="tool">{t("toolLabel")}</Label>
              <select
                id="tool"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={tool}
                onChange={(e) => setTool(e.target.value as BenchmarkTool)}
              >
                {TOOL_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value} ({TOOL_TO_KIND[value]})
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{t(`toolHint_${tool}`)}</p>
            </div>
          </CardContent>
```

- [ ] **Step 9: Remaining render tweaks**

1. Params card: change `showNfsOverride={!deploymentId && !externalTarget}` to `showNfsOverride={mode === "model"}`.
2. Submit button: change `disabled={createMutation.isPending}` to `disabled={createMutation.isPending || mode === "fromRun"}`.
3. Advanced card namespace input: an external target autofills/disables it only while the clone tab is active. Replace

```tsx
                value={externalTarget ? externalTarget.namespace : namespace}
                onChange={(e) => setNamespace(e.target.value)}
                disabled={!!externalTarget}
```

with

```tsx
                value={activeExternal ? activeExternal.namespace : namespace}
                onChange={(e) => setNamespace(e.target.value)}
                disabled={!!activeExternal}
```

- [ ] **Step 10: Verify no references to removed pieces remain**

```bash
grep -n "ephemeral\|directModeDisabled\|handleTargetChange\|modelHintDisabled\|modeClone\|modeDirect" "frontend/src/app/(app)/admin/benchmarks/new/page.tsx" || echo "CLEAN"
```

Expected: matches only `run.ephemeral` (inside `loadFromRun`) and `body.ephemeral = true` (clone submit + preview). Anything else is a leftover — remove it.

- [ ] **Step 11: Lint and build gates**

```bash
cd frontend && npm run lint 2>&1 | tail -5
cd frontend && npm run build 2>&1 | tail -15
```

Expected: same error/warning counts as the Step 1 baseline (0 NEW; none attributable to this file), and the build completes successfully (route `/admin/benchmarks/new` compiled).

- [ ] **Step 12: Commit**

```bash
git add "frontend/src/app/(app)/admin/benchmarks/new/page.tsx"
git commit -m "feat(bench): tab-based start modes on the benchmark form, clone-first

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
