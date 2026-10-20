"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FileCode2, Loader2, ScrollText } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateServingRecipe, useGpuTypes, useRouterDefaults, useUpdateServingRecipe } from "@/hooks/use-api";
import type { PdConfig, PdFramework, PdRoleOverride, PoolConfig, ServingEngine, ServingMode, ServingRecipe, ServingRecipeInput, StorageEntry } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DEFAULT_ENGINE_ARGS, ENGINES, ENGINE_DEFAULT_IMAGE, ENGINE_LABEL_KEY, missingToolCallParser } from "@/lib/serving-engines";
import { Field } from "@/components/engine-args-fields";
import { PoolConfigCards } from "@/components/pool-config-cards";
import { PdRolePanel } from "@/components/pd-role-panel";
import { PdRouterPanel } from "@/components/pd-router-panel";
import { RecipeYamlPanel, RecipeYamlPreview } from "@/components/recipe-yaml-preview";
import { FormCard, FormSection } from "@/components/form-section";
import { splitEngineArgs } from "@/lib/recipe-drafts";
import { DEFAULT_PD_FRAMEWORK, defaultPdExtras, defaultSidecar, retargetPdExtras, PD_FRAMEWORKS, PD_ROLES, POOL_KEYS, effectiveRole, poolOf, type PdRole } from "@/lib/pd-serving";

export const RECIPES_HREF = "/admin/recipes";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  storages: [], vllm_extra_args: null, env: null,
  // Startup + liveness on (empty spec = backend defaults): liveness only starts once startup has passed.
  engine: "vllm", engine_args: DEFAULT_ENGINE_ARGS.vllm, probes: { liveness: {}, startup: {} }, gpu_type: null,
  serving_mode: "aggregated", pd_config: null, runtime: { shm_size_gi: 1 },
};

const MODES: ServingMode[] = ["aggregated", "pd"];
const MODE_LABEL_KEY: Record<ServingMode, "modeAggregated" | "modePd"> = { aggregated: "modeAggregated", pd: "modePd" };
type PdTab = PdRole | "router";
const PD_TABS: PdTab[] = ["prefill", "decode", "router"];
const PD_TAB_LABEL: Record<PdTab, "pdTabPrefill" | "pdTabDecode" | "pdTabRouter"> = {
  prefill: "pdTabPrefill", decode: "pdTabDecode", router: "pdTabRouter",
};

function toInput(r: ServingRecipe): ServingRecipeInput {
  const { id, created_by, updated_by, created_at, updated_at, ...rest } = r;
  void id; void created_by; void updated_by; void created_at; void updated_at;
  return rest;
}

/** P/D recipes edit each pool's *effective* config (row + role), so older partial rows load complete. */
function withFullRoles(input: ServingRecipeInput): ServingRecipeInput {
  if (input.serving_mode !== "pd") return input;
  const pd: PdConfig = input.pd_config ?? {};
  return { ...input, pd_config: { ...pd, prefill: effectiveRole(input, pd.prefill), decode: effectiveRole(input, pd.decode) } };
}

/** Engine args without a form field (e.g. dropped from the list) move to the extra-args lines, base and pools alike. */
function withListedArgs(input: ServingRecipeInput): ServingRecipeInput {
  const engine: ServingEngine = input.engine === "sglang" ? "sglang" : "vllm";
  const out: ServingRecipeInput = { ...input, ...splitEngineArgs(engine, input.engine_args, input.vllm_extra_args).value };
  if (input.serving_mode !== "pd" || !input.pd_config) return out;
  const pd: PdConfig = { ...input.pd_config };
  for (const role of PD_ROLES) {
    const pool = pd[role];
    if (pool) pd[role] = { ...pool, ...splitEngineArgs("vllm", pool.engine_args, pool.vllm_extra_args).value };
  }
  return { ...out, pd_config: pd };
}

/** A pool entering P/D for the first time starts with the default P/D settings; one that has its own keeps them. */
function withPdDefaults(pool: PdRoleOverride, role: PdRole, pd: PdConfig): PdRoleOverride {
  const out = pool.pd_extras ? pool : { ...pool, pd_extras: defaultPdExtras(role, pd) };
  return role === "decode" && !out.sidecar ? { ...out, sidecar: defaultSidecar(pd) } : out;
}

/** Page chrome shared by the create and edit routes. */
export function RecipePageHeader({ title, description }: { title: string; description?: string }) {
  const t = useTranslations("servingRecipes");
  return (
    <div>
      <Link href={RECIPES_HREF} className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
        <ArrowLeft className="size-3.5" />{t("backToList")}
      </Link>
      <h1 className="text-2xl font-bold mt-2 flex items-center gap-2"><ScrollText className="size-5" />{title}</h1>
      {description && <p className="text-muted-foreground mt-1">{description}</p>}
    </div>
  );
}

/**
 * Full-page serving recipe form. Creates when `recipe` is absent, updates
 * otherwise. Navigates back to the list on success.
 *
 * The recipe card (mode, engine, name, description) sits on top. An
 * aggregated recipe is followed by one set of pool cards; a prefill/decode
 * recipe by Prefill / Decode / Router tabs, each pool carrying its complete
 * config — nothing is shared between the pools but the recipe identity.
 *
 * `initial` pre-fills a new recipe (captured from a deployment); the caller
 * remounts with a `key` when it changes. `sourceDeploymentId` is sent on
 * create so the backend can point that deployment at the new recipe.
 */
export function ServingRecipeForm({
  recipe, initial, sourceDeploymentId, children,
}: {
  recipe?: ServingRecipe;
  initial?: ServingRecipeInput;
  sourceDeploymentId?: string | null;
  /** Rendered above the form: import notice, parser warnings. */
  children?: React.ReactNode;
}) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const ts = useTranslations("storages");
  const router = useRouter();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();
  const { data: gpuTypes } = useGpuTypes();
  const { data: routerDefaults } = useRouterDefaults();

  // Drafts from the reverse parser predate some fields; BLANK fills the gaps.
  const seed: ServingRecipeInput = withListedArgs(withFullRoles({ ...BLANK, ...(recipe ? toInput(recipe) : initial ?? {}) }));
  const [form, setForm] = useState<ServingRecipeInput>(seed);
  const [kvExtraText, setKvExtraText] = useState(() => {
    const extra = seed.pd_config?.kv_transfer_extra;
    return extra && Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
  });
  const [pdTab, setPdTab] = useState<PdTab>("prefill");
  // Bumped when a pool's config is replaced wholesale (mode switch, copy) so its cards re-read their text areas.
  const [poolNonce, setPoolNonce] = useState<Record<PdRole | "base", number>>({ prefill: 0, decode: 0, base: 0 });
  // The request body being previewed as YAML (null = closed) and a nonce so each open remounts the dialog.
  const [previewBody, setPreviewBody] = useState<ServingRecipeInput | null>(null);
  const [previewNonce, setPreviewNonce] = useState(0);

  const saving = createMut.isPending || updateMut.isPending;
  const isPd = form.serving_mode === "pd";
  const pd: PdConfig = form.pd_config ?? {};
  const gpuTypeList = gpuTypes ?? [];

  function switchEngine(next: ServingEngine) {
    setForm((f) => {
      if (f.engine === next) return f;
      const otherDefault = ENGINE_DEFAULT_IMAGE[f.engine];
      const image = !f.image.trim() || f.image === otherDefault ? ENGINE_DEFAULT_IMAGE[next] : f.image;
      // Structured args are engine-specific (reset to the engine's defaults); free-text extra args are the user's and stay.
      const engineArgs = DEFAULT_ENGINE_ARGS[next];
      return { ...f, engine: next, engine_args: engineArgs ? { ...engineArgs } : null, image };
    });
    setPoolNonce((n) => ({ ...n, base: n.base + 1 }));
  }

  /** P/D needs vLLM: switching to it also switches the engine and seeds both pools from the aggregated cards. */
  function switchMode(next: ServingMode) {
    if (next === "pd") switchEngine("vllm");
    setForm((f) => {
      if (next !== "pd") return { ...f, serving_mode: next };
      const pdc: PdConfig = f.pd_config ?? {};
      return {
        ...f,
        serving_mode: "pd",
        pd_config: {
          ...pdc,
          framework: pdc.framework ?? DEFAULT_PD_FRAMEWORK,
          // A recipe entering P/D starts with the router's values.yaml (an older P/D recipe keeps its fields).
          router: pdc.router?.values_yaml != null || !routerDefaults ? pdc.router : { ...(pdc.router ?? {}), values_yaml: routerDefaults.values_yaml },
          prefill: withPdDefaults(effectiveRole(f, pdc.prefill), "prefill", pdc),
          decode: withPdDefaults(effectiveRole(f, pdc.decode), "decode", pdc),
        },
      };
    });
    setPoolNonce((n) => ({ ...n, prefill: n.prefill + 1, decode: n.decode + 1 }));
    setPdTab("prefill");
  }

  function setPd(patch: Partial<PdConfig>) {
    setForm((f) => ({ ...f, pd_config: { ...(f.pd_config ?? {}), ...patch } }));
  }
  function setRole(role: PdRole, value: PdRoleOverride) {
    setPd({ [role]: value });
  }
  function copyRole(into: PdRole) {
    const from: PdRole = into === "prefill" ? "decode" : "prefill";
    const src = pd[from] ?? {};
    setRole(into, {
      ...src,
      replicas: pd[into]?.replicas ?? src.replicas ?? 1,
      pd_extras: src.pd_extras ? retargetPdExtras(src.pd_extras, into) : src.pd_extras,
      sidecar: pd[into]?.sidecar ?? null, // the sidecar belongs to the decode pool; copying never moves it
    });
    setPoolNonce((n) => ({ ...n, [into]: n[into] + 1 }));
    toast.success(t("pdCopied", { role: from }));
  }

  // The live side panel renders what the form would send, a moment after typing stops.
  const draft = useMemo(() => recipeRequestBody(form, kvExtraText), [form, kvExtraText]);
  const liveRecipe = useDebounced(draft.ok ? draft.body : null, 600);

  /** The request body for the current form, or null after toasting what is missing (the name only matters on save). */
  function buildBody({ requireName }: { requireName: boolean }): ServingRecipeInput | null {
    if (requireName && !form.name.trim()) {
      toast.error(t("requiredError"));
      return null;
    }
    if (draft.ok) return draft.body;
    toast.error(t(DRAFT_ERROR_KEY[draft.error]));
    if (draft.tab) setPdTab(draft.tab);
    return null;
  }

  function openPreview() {
    const body = buildBody({ requireName: false });
    if (!body) return;
    setPreviewBody(body);
    setPreviewNonce((n) => n + 1);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const body = buildBody({ requireName: true });
    if (!body) return;
    const opts = {
      onSuccess: (saved: ServingRecipe) => {
        toast.success(recipe ? t("saveSuccess") : t("createSuccess"));
        for (const code of saved.warnings ?? []) toast.warning(ts(`warn_${code}`));
        router.push(RECIPES_HREF);
      },
      onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("saveError")),
    };
    if (recipe) updateMut.mutate({ id: recipe.id, body }, opts);
    else createMut.mutate({ ...body, source_deployment_id: sourceDeploymentId ?? null }, opts);
  }

  const segment = (active: boolean) =>
    "rounded px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 " +
    (active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground");

  const recipeSection = (
    <FormSection title={t("sectionRecipe")}>
      <Field id="recipe-mode" label={`${t("servingMode")} · ${t("engine")}`} hint={isPd ? `${t("modePdHint")} ${t("modeEngineLocked")}` : undefined}>
        <div className="flex flex-wrap items-center gap-2">
          <div id="recipe-mode" role="radiogroup" aria-label={t("servingMode")} className="inline-flex rounded-md border p-0.5">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={form.serving_mode === m}
                data-testid={`recipe-mode-${m}`}
                onClick={() => switchMode(m)}
                className={segment(form.serving_mode === m)}
              >
                {t(MODE_LABEL_KEY[m])}
              </button>
            ))}
          </div>
          <div id="recipe-engine" role="radiogroup" aria-label={t("engine")} className="inline-flex rounded-md border p-0.5">
            {ENGINES.map((e) => {
              const locked = isPd && e !== "vllm";
              return (
                <button
                  key={e}
                  type="button"
                  role="radio"
                  aria-checked={form.engine === e}
                  aria-disabled={locked}
                  disabled={locked}
                  onClick={() => switchEngine(e)}
                  className={segment(form.engine === e)}
                >
                  {t(ENGINE_LABEL_KEY[e])}
                </button>
              );
            })}
          </div>
        </div>
      </Field>
      {isPd && (
        <Field id="recipe-pd-framework" label={t("pdFramework")} hint={t("pdFrameworkHint")}>
          <select
            id="recipe-pd-framework"
            className="h-9 w-48 rounded-md border border-input bg-transparent px-3 text-sm"
            value={pd.framework ?? DEFAULT_PD_FRAMEWORK}
            onChange={(e) => setPd({ framework: e.target.value as PdFramework })}
          >
            {PD_FRAMEWORKS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </Field>
      )}
      <Field id="recipe-name" label={t("recipeName")} hint={t("recipeNameHint")} required>
        <Input id="recipe-name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
      </Field>
      <Field id="recipe-description" label={t("description")}>
        <Input id="recipe-description" value={form.description ?? ""} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value || null }))} />
      </Field>
    </FormSection>
  );

  return (
    <div className="xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:items-start xl:gap-6">
      <form onSubmit={handleSubmit} className={`space-y-4 ${COMPACT_CONTROLS}`}>
        {children}
        {isPd ? (
          <>
            <FormCard>{recipeSection}</FormCard>
            <Tabs value={pdTab} onValueChange={(v) => setPdTab(v as PdTab)} data-testid="recipe-pd-section">
              <TabsList className="w-full">
                {PD_TABS.map((tab) => (
                  <TabsTrigger key={tab} value={tab} data-testid={`recipe-pd-tab-${tab}`}>{t(PD_TAB_LABEL[tab])}</TabsTrigger>
                ))}
              </TabsList>
              {PD_ROLES.map((role) => (
                <TabsContent key={role} value={role} forceMount className="data-[state=inactive]:hidden">
                  <PdRolePanel
                    key={`${role}-${poolNonce[role]}`}
                    role={role}
                    value={pd[role] ?? {}}
                    gpuTypes={gpuTypeList}
                    onChange={(v) => setRole(role, v)}
                    onCopyFrom={() => copyRole(role)}
                  />
                </TabsContent>
              ))}
              <TabsContent value="router" forceMount className="data-[state=inactive]:hidden">
                <PdRouterPanel
                value={pd} onChange={setPd} kvExtraText={kvExtraText} onKvExtraChange={setKvExtraText}
                poolsHaveSettings={PD_ROLES.every((r) => !!pd[r]?.pd_extras)}
                decodeHasSidecar={!!pd.decode?.sidecar}
              />
              </TabsContent>
            </Tabs>
          </>
        ) : (
          <FormCard>
            {recipeSection}
            <PoolConfigCards
              key={`base-${poolNonce.base}`}
              idPrefix="recipe"
              engine={form.engine}
              value={poolOf(form)}
              gpuTypes={gpuTypeList}
              onChange={({ storages, ...patch }) => setForm((f) => ({ ...f, ...patch, ...(storages !== undefined && { storages: storages ?? [] }) }))}
            />
          </FormCard>
        )}

        <div className="sticky bottom-0 z-10 -mx-1 flex items-center justify-end gap-2 bg-background/90 px-1 py-3 backdrop-blur">
          <Button type="button" variant="outline" size="sm" className="mr-auto xl:hidden" onClick={openPreview} data-testid="recipe-preview-yaml">
            <FileCode2 className="size-4" />{t("previewYaml")}
          </Button>
          <Button asChild type="button" variant="outline" size="sm" disabled={saving}>
            <Link href={RECIPES_HREF}>{tc("cancel")}</Link>
          </Button>
          <Button type="submit" size="sm" disabled={saving}>
            {saving && <Loader2 className="size-4 animate-spin" />}
            {recipe ? t("save") : t("create")}
          </Button>
        </div>
        <RecipeYamlPreview key={previewNonce} recipe={previewBody} onClose={() => setPreviewBody(null)} />
      </form>

      <aside
        className="hidden xl:sticky xl:top-4 xl:flex xl:h-[calc(100vh-2rem)] xl:flex-col xl:gap-2 xl:rounded-xl xl:border xl:bg-card xl:p-4 xl:shadow-sm"
        data-testid="recipe-yaml-side"
      >
        <h2 className="flex items-center gap-2 text-sm font-semibold"><FileCode2 className="size-4" />{t("previewYaml")}</h2>
        <RecipeYamlPanel
          recipe={liveRecipe}
          emptyText={draft.ok ? undefined : t(draft.error === "required" ? "previewLiveEmpty" : DRAFT_ERROR_KEY[draft.error])}
          fill
          className="flex-1"
        />
      </aside>
    </div>
  );
}

/** Text inputs and selects one step shorter than the app default, for the dense recipe form. */
const COMPACT_CONTROLS = "[&_input[data-slot=input]]:h-8 [&_select]:h-8";

type DraftError = "required" | "kv_extra" | "tool_parser";
const DRAFT_ERROR_KEY: Record<DraftError, "requiredError" | "pdKvExtraInvalid" | "toolParserRequired"> = {
  required: "requiredError", kv_extra: "pdKvExtraInvalid", tool_parser: "toolParserRequired",
};

type RequestBody =
  | { ok: true; body: ServingRecipeInput }
  | { ok: false; error: DraftError; tab?: PdTab };

/**
 * The create/update request body for the form, or why it cannot be built yet
 * (and which P/D tab holds the problem). Pure: the save path toasts, the live
 * preview just waits.
 */
function recipeRequestBody(form: ServingRecipeInput, kvExtraText: string): RequestBody {
  const keepStorages = (list: StorageEntry[] | null | undefined) => (list ?? []).filter((s) => s.storage_id);
  const body: ServingRecipeInput = { ...form, storages: keepStorages(form.storages), pd_config: null };
  if (form.serving_mode !== "pd") {
    if (!form.image.trim() || !form.model_path.trim()) return { ok: false, error: "required" };
    if (form.engine === "vllm" && missingToolCallParser(form.engine_args, form.vllm_extra_args)) return { ok: false, error: "tool_parser" };
    return { ok: true, body };
  }
  const pd: PdConfig = form.pd_config ?? {};
  const prefill = pd.prefill ?? {};
  const decode = pd.decode ?? {};
  for (const [role, pool] of [["prefill", prefill], ["decode", decode]] as const) {
    if (!pool.image?.trim() || !pool.model_path?.trim()) return { ok: false, error: "required", tab: role };
    if (missingToolCallParser(pool.engine_args, pool.vllm_extra_args)) return { ok: false, error: "tool_parser", tab: role };
  }
  let kvExtra: Record<string, unknown> | undefined;
  if (kvExtraText.trim()) {
    try {
      const parsed: unknown = JSON.parse(kvExtraText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
      kvExtra = parsed as Record<string, unknown>;
    } catch {
      return { ok: false, error: "kv_extra", tab: "router" };
    }
  }
  // The row keeps the prefill pool's values as its representative copy (required
  // columns, list views); each pool's own config is what gets rendered.
  const representative: Partial<PoolConfig> = {};
  for (const k of POOL_KEYS) (representative as Record<string, unknown>)[k] = prefill[k] ?? null;
  // Each pool always carries its own explicit storage list; the row keeps the prefill pool's list.
  const withStorages = (pool: PdRoleOverride): PdRoleOverride => ({ ...pool, storages: keepStorages(pool.storages) });
  return {
    ok: true,
    body: {
      ...body,
      ...representative,
      image: prefill.image ?? "",
      model_path: prefill.model_path ?? "",
      gpu_resource_key: prefill.gpu_resource_key ?? "nvidia.com/gpu",
      storages: keepStorages(prefill.storages),
      gpu_count: Math.max(prefill.gpu_count ?? 0, decode.gpu_count ?? 0),
      pd_config: { ...pd, prefill: withStorages(prefill), decode: withStorages(decode), kv_transfer_extra: kvExtra ?? {}, sidecar_image: pd.sidecar_image?.trim() || null },
    },
  };
}

/** `value`, updated only after it has stopped changing for `ms` (pass a memoized value). */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
