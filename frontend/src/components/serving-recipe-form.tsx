"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, ScrollText } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateServingRecipe, useGpuTypes, useUpdateServingRecipe } from "@/hooks/use-api";
import type { PdConfig, PdRoleOverride, PoolConfig, ServingEngine, ServingMode, ServingRecipe, ServingRecipeInput } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ENGINES, ENGINE_DEFAULT_IMAGE, ENGINE_LABEL_KEY } from "@/lib/serving-engines";
import { Field } from "@/components/engine-args-fields";
import { PoolConfigCards } from "@/components/pool-config-cards";
import { PdRolePanel } from "@/components/pd-role-panel";
import { PdRouterPanel } from "@/components/pd-router-panel";
import { PD_ROLES, POOL_KEYS, effectiveRole, poolOf, type PdRole } from "@/lib/pd-serving";

export const RECIPES_HREF = "/admin/recipes";

const BLANK: ServingRecipeInput = {
  name: "", description: null, model_path: "", image: "", gpu_count: 1,
  gpu_resource_key: "nvidia.com/gpu", cpu_request: null, cpu_limit: null,
  memory_request: null, memory_limit: null, node_selector: null, tolerations: null,
  pvc_name: null, pvc_mount_path: null, vllm_extra_args: null, env: null,
  engine: "vllm", engine_args: null, probes: null, gpu_type: null,
  serving_mode: "aggregated", pd_config: null, runtime: null,
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
  const router = useRouter();
  const createMut = useCreateServingRecipe();
  const updateMut = useUpdateServingRecipe();
  const { data: gpuTypes } = useGpuTypes();

  // Drafts from the reverse parser predate some fields; BLANK fills the gaps.
  const seed: ServingRecipeInput = withFullRoles({ ...BLANK, ...(recipe ? toInput(recipe) : initial ?? {}) });
  const [form, setForm] = useState<ServingRecipeInput>(seed);
  const [kvExtraText, setKvExtraText] = useState(() => {
    const extra = seed.pd_config?.kv_transfer_extra;
    return extra && Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
  });
  const [pdTab, setPdTab] = useState<PdTab>("prefill");
  // Bumped when a pool's config is replaced wholesale (mode switch, copy) so its cards re-read their text areas.
  const [poolNonce, setPoolNonce] = useState<Record<PdRole | "base", number>>({ prefill: 0, decode: 0, base: 0 });

  const saving = createMut.isPending || updateMut.isPending;
  const isPd = form.serving_mode === "pd";
  const pd: PdConfig = form.pd_config ?? {};
  const gpuTypeList = gpuTypes ?? [];

  function switchEngine(next: ServingEngine) {
    setForm((f) => {
      if (f.engine === next) return f;
      const otherDefault = ENGINE_DEFAULT_IMAGE[f.engine];
      const image = !f.image.trim() || f.image === otherDefault ? ENGINE_DEFAULT_IMAGE[next] : f.image;
      // Structured args are engine-specific; free-text extra args are the user's and stay.
      return { ...f, engine: next, engine_args: null, image };
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
        pd_config: { ...pdc, prefill: effectiveRole(f, pdc.prefill), decode: effectiveRole(f, pdc.decode) },
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
    setRole(into, { ...src, replicas: pd[into]?.replicas ?? src.replicas ?? 1 });
    setPoolNonce((n) => ({ ...n, [into]: n[into] + 1 }));
    toast.success(t("pdCopied", { role: from }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error(t("requiredError"));
      return;
    }
    let body: ServingRecipeInput = { ...form, pd_config: null };
    if (isPd) {
      const prefill = pd.prefill ?? {};
      const decode = pd.decode ?? {};
      for (const [role, pool] of [["prefill", prefill], ["decode", decode]] as const) {
        if (!pool.image?.trim() || !pool.model_path?.trim()) {
          toast.error(t("requiredError"));
          setPdTab(role);
          return;
        }
      }
      let kvExtra: Record<string, unknown> | undefined;
      if (kvExtraText.trim()) {
        try {
          const parsed: unknown = JSON.parse(kvExtraText);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
          kvExtra = parsed as Record<string, unknown>;
        } catch {
          toast.error(t("pdKvExtraInvalid"));
          setPdTab("router");
          return;
        }
      }
      // The row keeps the prefill pool's values as its representative copy (required
      // columns, list views); each pool's own config is what gets rendered.
      const representative: Partial<PoolConfig> = {};
      for (const k of POOL_KEYS) (representative as Record<string, unknown>)[k] = prefill[k] ?? null;
      body = {
        ...body,
        ...representative,
        image: prefill.image ?? "",
        model_path: prefill.model_path ?? "",
        gpu_resource_key: prefill.gpu_resource_key ?? "nvidia.com/gpu",
        gpu_count: Math.max(prefill.gpu_count ?? 0, decode.gpu_count ?? 0),
        pd_config: { ...pd, prefill, decode, kv_transfer_extra: kvExtra ?? {}, sidecar_image: pd.sidecar_image?.trim() || null },
      };
    } else if (!form.image.trim() || !form.model_path.trim()) {
      toast.error(t("requiredError"));
      return;
    }
    const opts = {
      onSuccess: () => {
        toast.success(recipe ? t("saveSuccess") : t("createSuccess"));
        router.push(RECIPES_HREF);
      },
      onError: (err: unknown) => toast.error(err instanceof Error ? err.message : t("saveError")),
    };
    if (recipe) updateMut.mutate({ id: recipe.id, body }, opts);
    else createMut.mutate({ ...body, source_deployment_id: sourceDeploymentId ?? null }, opts);
  }

  const recipeCard = (
    <Card>
      <CardHeader><CardTitle className="text-base">{t("sectionRecipe")}</CardTitle></CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field id="recipe-mode" label={t("servingMode")} hint={isPd ? t("modePdHint") : undefined}>
          <div id="recipe-mode" role="radiogroup" className="inline-flex rounded-md border p-0.5">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={form.serving_mode === m}
                data-testid={`recipe-mode-${m}`}
                onClick={() => switchMode(m)}
                className={
                  "rounded px-3 py-1 text-sm transition-colors " +
                  (form.serving_mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                }
              >
                {t(MODE_LABEL_KEY[m])}
              </button>
            ))}
          </div>
        </Field>
        <Field id="recipe-engine" label={t("engine")} hint={isPd ? t("modeEngineLocked") : undefined}>
          <div id="recipe-engine" role="radiogroup" className="inline-flex rounded-md border p-0.5">
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
                  className={
                    "rounded px-3 py-1 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 " +
                    (form.engine === e ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {t(ENGINE_LABEL_KEY[e])}
                </button>
              );
            })}
          </div>
        </Field>
        <Field id="recipe-name" label={t("name")} required>
          <Input id="recipe-name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </Field>
        <Field id="recipe-description" label={t("description")}>
          <Input id="recipe-description" value={form.description ?? ""} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value || null }))} />
        </Field>
      </CardContent>
    </Card>
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {children}
      {recipeCard}
      {isPd ? (
        <Tabs value={pdTab} onValueChange={(v) => setPdTab(v as PdTab)} data-testid="recipe-pd-section">
          <TabsList className="w-full">
            {PD_TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab} data-testid={`recipe-pd-tab-${tab}`}>{t(PD_TAB_LABEL[tab])}</TabsTrigger>
            ))}
          </TabsList>
          {PD_ROLES.map((role) => (
            <TabsContent key={role} value={role} forceMount className="pt-2 data-[state=inactive]:hidden">
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
          <TabsContent value="router" forceMount className="pt-2 data-[state=inactive]:hidden">
            <PdRouterPanel value={pd} onChange={setPd} kvExtraText={kvExtraText} onKvExtraChange={setKvExtraText} />
          </TabsContent>
        </Tabs>
      ) : (
        <PoolConfigCards
          key={`base-${poolNonce.base}`}
          idPrefix="recipe"
          engine={form.engine}
          value={poolOf(form)}
          gpuTypes={gpuTypeList}
          onChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
        />
      )}

      <div className="flex items-center justify-end gap-3">
        <Button asChild type="button" variant="outline" disabled={saving}>
          <Link href={RECIPES_HREF}>{tc("cancel")}</Link>
        </Button>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          {recipe ? t("save") : t("create")}
        </Button>
      </div>
    </form>
  );
}
