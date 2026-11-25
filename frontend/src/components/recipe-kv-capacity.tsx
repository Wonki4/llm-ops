"use client";

import Link from "next/link";
import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { Input } from "@/components/ui/input";
import { defaultCapacityScenario, initialDSparkScenario } from "@/lib/recipe-capacity-scenario";
import type { CapacityScenario, RecipeCapacity, RecipeDSparkEstimate } from "@/types";

const number = (n: number) => n.toLocaleString();
const gib = (bytes: number) => `${(bytes / 2 ** 30).toLocaleString(undefined, { maximumFractionDigits: 3 })} GiB`;

function Quantity({ value, range, ceiling }: { value?: number | null; range?: number[]; ceiling?: boolean }) {
  if (ceiling) {
    const upper = value ?? range?.[1];
    return upper != null ? <>≤ {number(upper)}</> : <>—</>;
  }
  if (value != null) return <>{number(value)}</>;
  if (range?.length === 2 && range[0] === 0) return <>≤ {number(range[1])}</>;
  if (range) return <>{number(range[0])} – {number(range[1])}</>;
  return <>—</>;
}

type CapacityPool = RecipeCapacity["pools"][number];

export function useRecipeCapacityScenario() {
  const [scenario, setScenario] = useState(defaultCapacityScenario);
  const [followRecipeLimit, setFollowRecipeLimit] = useState(true);
  function syncRecipeLimit(data?: RecipeCapacity) {
    if (!data?.pools.length || data.pools.some(pool => pool.pending) || !followRecipeLimit) return;
    const requests = (initialDSparkScenario(data) ?? defaultCapacityScenario).requests;
    if (requests !== scenario.requests) setScenario({ ...scenario, requests });
  }
  function changeScenario(next: CapacityScenario) {
    if (next.requests !== scenario.requests) setFollowRecipeLimit(false);
    setScenario(next);
  }
  return { scenario, syncRecipeLimit, changeScenario };
}

function DSparkDetails({ draft, pool }: { draft: RecipeDSparkEstimate; pool: CapacityPool }) {
  const t = useTranslations("recipeCapacity.dspark");
  const source = pool.budget_source;
  const contextLimit = draft.context_limited_capacity_tokens;
  return <section className="space-y-1 text-sm" aria-label={t("title")}>
    <h4 className="font-medium">{t("title")}</h4>
    <p className="tabular-nums">{t("candidates", {
      width: draft.draft_tokens, requests: draft.active_requests, candidates: draft.candidate_tokens,
    })}</p>
    {draft.max_num_seqs != null && <p className="text-xs text-muted-foreground">{t("requestLimit", {
      scenario: draft.scenario_requests, limit: draft.max_num_seqs,
    })}</p>}
    <p className="text-xs text-muted-foreground">{t("scope")}</p>
    {contextLimit != null && pool.capacity_range && contextLimit < pool.capacity_range[1] &&
      <p className="text-xs text-muted-foreground">{t("contextLimit", { count: contextLimit })}</p>}
    <details className="pt-1 text-xs">
      <summary className="cursor-pointer text-muted-foreground">{t("memory")}</summary>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        <dt>{t("model")}</dt><dd className="break-all">{draft.draft_model}</dd>
        {draft.revision && <><dt>{t(draft.assumed_revision ? "assumedRevision" : "revision")}</dt><dd className="break-all">{draft.revision}</dd></>}
        <dt>{t("lookahead")}</dt><dd>{t("slots", { perRequest: draft.lookahead_tokens_per_request, total: draft.lookahead_tokens })}</dd>
        <dt>{t("window")}</dt><dd>{t("tokens", { value: draft.draft_window_tokens })}</dd>
        <dt>{t("parallelism")}</dt><dd>TP {draft.draft_tp} · DCP {draft.draft_dcp} · {draft.draft_cache_dtype}</dd>
        <dt>{t("cache")}</dt><dd>≥ {gib(draft.draft_kv_bytes_per_gpu)}</dd>
        {draft.verification_workspace_bytes_per_gpu != null && <><dt>{t("workspace")}</dt><dd>{gib(draft.verification_workspace_bytes_per_gpu)}</dd></>}
        {draft.draft_weight_bytes_per_gpu != null && <><dt>{t("weights")}</dt><dd>≥ {gib(draft.draft_weight_bytes_per_gpu)}</dd></>}
      </dl>
      {source && source !== "unknown" && <p className="mt-2 text-muted-foreground">{
        source !== "profile_upper_bound" ? t("kvBudget") : t(draft.verification_workspace_bytes_per_gpu == null ? "deducted" : "deductedWithWorkspace")
      }</p>}
      <p className="mt-1 text-muted-foreground">{t("pages")}</p>
    </details>
  </section>;
}

function CapacityIssue({ pool }: { pool: CapacityPool }) {
  const t = useTranslations("recipeCapacity");
  const ts = useTranslations("savedRecipeCapacity");
  const missing = pool.missing ?? [];
  const ranks = (pool.tp ?? 1) * (pool.pp ?? 1);
  if (pool.weights_exceed_vram) return t("weightsExceedVram");
  if (pool.budget_exceeds_vram) return t("budgetExceedsVram");
  if (pool.gpu_count === 0) return ts("noGpu");
  if (pool.gpu_count != null && pool.gpu_count < ranks) {
    return ts("insufficientGpus", { assigned: pool.gpu_count, required: ranks });
  }
  if (pool.pending) return <span role="status">{t("loading")}</span>;
  if (!pool.metadata_source && !pool.metadata_digest) return ts("metadataNeeded");
  if (missing.includes("DSpark draft token count exceeds checkpoint width")) return t("dspark.invalidWidth");
  if (missing.includes("DSpark draft checkpoint metadata or revision")) return t("dspark.missingDraft");
  const unknownLayout = missing.find(reason => reason.startsWith("unrecognized cache layout: "));
  if (unknownLayout) return ts("unsupportedLayout", { model: unknownLayout.slice("unrecognized cache layout: ".length) });
  if (pool.budget_bytes_per_gpu == null) {
    if (pool.gpu_type && pool.gpu_profile_resolved === false) return ts("profileMissing", { gpu: pool.gpu_type });
    if (!pool.gpu_type || pool.gpu_vram_gb == null) return ts("vramNeeded");
    return t("budgetNeeded");
  }
  return t("layoutNeeded");
}

function PoolCapacity({ pool, ruleVersion }: {
  pool: CapacityPool;
  ruleVersion: string;
}) {
  const t = useTranslations("recipeCapacity");
  const ts = useTranslations("savedRecipeCapacity");
  const format = useFormatter();
  const source = pool.budget_source;
  const budget = pool.budget_bytes_per_gpu;
  const hasBudget = !!source && source !== "unknown" && budget?.length === 2;
  const insufficientMemory = pool.budget_exceeds_vram || pool.weights_exceed_vram;
  const hasEstimate = !insufficientMemory && (pool.capacity_tokens != null || pool.capacity_range?.length === 2);
  const hasRequestCount = pool.complete_requests != null || pool.complete_requests_range?.length === 2;
  const lowerBound = pool.payload_lower_bound === true;
  const requiredMemory = (bytes: number) => (lowerBound ? "≥ " : "") + gib(bytes);
  const missing = (pool.missing ?? []).filter(item => item !== "KV memory budget");
  const observed = pool.observed;
  const observedOnly = !insufficientMemory && !hasEstimate && observed?.capacity_tokens != null;
  const deviceOnly = source === "profile_upper_bound" && pool.device_only_weight_bound;
  const hostCache = pool.host_kv_offload;
  const weights = pool.weight_offload;
  const delta = observed?.capacity_tokens != null && hasEstimate && !lowerBound && pool.capacity_tokens != null
    ? observed.capacity_tokens - pool.capacity_tokens : null;
  return <article className="space-y-3 rounded-lg border bg-card p-4" data-testid={`recipe-kv-pool-${pool.role}`}>
    <header className="flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="font-semibold">{pool.role === "aggregated" ? ts("aggregated") : pool.role === "prefill" ? "Prefill" : "Decode"}</h3>
      <span className="text-sm text-muted-foreground">
        {[
          pool.gpu_type || ts("gpuUnset"),
          pool.gpu_count != null && ts("gpus", {count: pool.gpu_count}),
          pool.gpu_vram_gb != null && ts("vram", {size: pool.gpu_vram_gb}),
          pool.gpu_type && pool.gpu_profile_resolved === false && ts("unregisteredProfile"),
        ].filter(Boolean).join(" · ")}
      </span>
    </header>
    <dl className="grid gap-4 border-y py-3 sm:grid-cols-3">
      <div><dt className="text-xs text-muted-foreground">{insufficientMemory && source === "explicit" ? t("configuredGpuSpace") : ts("gpuSpace")}</dt>
        <dd className="mt-1 text-lg font-semibold tabular-nums">
          {hasBudget ? <>{source === "profile_upper_bound" && "≤ "}{gib(budget[1])}</> : ts("unknown")}
        </dd>
        {hasBudget && <dd className="text-xs text-muted-foreground">{t(deviceOnly ? "deviceOnly" : source)}</dd>}
      </div>
      <div><dt className="text-xs text-muted-foreground">{ts("replicaTokens")}</dt>
        {hasEstimate || observedOnly ? <>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            <Quantity value={observedOnly ? observed?.capacity_tokens : pool.capacity_tokens} range={pool.capacity_range} ceiling={lowerBound && !observedOnly} /> {t("tokens")}
          </dd>
          <dd className="text-xs text-muted-foreground">
            {observedOnly ? ts("observed") : deviceOnly ? t("deviceOnly") : lowerBound ? t("capacityBound") : pool.capacity_tokens != null ? t("capacityEstimate") :
              t(pool.capacity_range?.[0] === 0 ? "capacityBound" : "capacityRange")}
            {hasEstimate && hasRequestCount && <> · {t("whole")} <Quantity value={pool.complete_requests} range={pool.complete_requests_range} ceiling={lowerBound} /></>}
          </dd>
          {source === "profile_upper_bound" && budget?.[1] === 0 && <dd className="mt-1 text-xs text-muted-foreground">
            {t("noMemoryAfterWeights")}
          </dd>}
        </> : <dd className="mt-1 text-sm text-muted-foreground"><CapacityIssue pool={pool} /></dd>}
      </div>
      <div><dt className="text-xs text-muted-foreground">{t("required")}</dt>
        <dd className="mt-1 text-lg font-semibold tabular-nums">
          {pool.required_bytes_per_gpu != null ? requiredMemory(pool.required_bytes_per_gpu) : ts("unavailable")}
        </dd>
        {pool.shared_required_bytes_per_gpu != null && <dd className="text-xs text-muted-foreground">{t("shared")} {requiredMemory(pool.shared_required_bytes_per_gpu)}</dd>}
      </div>
    </dl>
    {hasEstimate && pool.weight_fit_unverified && <p className="text-sm text-muted-foreground">{t(source === "explicit" ? "weightFitUnverified" : deviceOnly ? "deviceOnlyDetail" : "weightBoundsOnly")}</p>}
    {pool.weight_offload && <p className="text-sm">
      {t("cpuWeights", { size: gib(pool.weight_offload.requested_bytes_per_gpu) })}
    </p>}
    {hostCache && <div className="space-y-1 text-sm">
      <p><span className="font-medium">{t("cpuKv")}</span>{" · "}
        {hostCache.configured_bytes != null ? t(hostCache.scope === "per_worker" ? "hostPerWorker" : "hostPerEngine", {
          size: gib(hostCache.configured_bytes),
        }) : hostCache.device_pool_ratio != null ? t("hostRatio", { ratio: hostCache.device_pool_ratio }) : t("hostSizeUnknown")}
      </p>
      <p className="text-xs text-muted-foreground">{t("hostActiveCapacity")}</p>
    </div>}
    {pool.speculation && <DSparkDetails draft={pool.speculation} pool={pool} />}
    {pool.integrated_speculation && <section className="space-y-1 text-sm">
      <h4 className="font-medium">{t("integratedDraft")}</h4>
      <p>{t("dspark.candidates", { width: pool.integrated_speculation.draft_tokens,
        requests: pool.integrated_speculation.active_requests, candidates: pool.integrated_speculation.candidate_tokens })}</p>
    </section>}
    {lowerBound && !pool.speculation && <p className="text-sm text-muted-foreground">{t("allocatorPadding")}</p>}
    <div className="space-y-1 text-sm">
      <h4 className="font-medium">{ts("observed")}</h4>
      {observed ? <>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {!observedOnly && observed.capacity_tokens != null && <span>{ts("replicaTokens")} <b>{format.number(observed.capacity_tokens)} {t("tokens")}</b></span>}
          {observed.budget_bytes_per_gpu != null && <span>{ts("gpuSpace")} <b>{gib(observed.budget_bytes_per_gpu)}</b></span>}
          {delta != null && <span>{ts("delta")} {format.number(delta, {signDisplay:"exceptZero"})} {t("tokens")}</span>}
        </div>
        <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          <Link className="underline underline-offset-2" href={`/admin/deployments/${observed.deployment_id}`}>{ts("deployment")}</Link>
          {observed.pod_name && <span className="break-all">{observed.pod_name}</span>}
          {observed.observed_at && <time dateTime={observed.observed_at}>{format.dateTime(new Date(observed.observed_at), {dateStyle:"medium", timeStyle:"short"})}</time>}
        </div>
      </> : <p className="text-muted-foreground">{ts("noObservation")}</p>}
    </div>
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">{t("basis")}</summary>
      {!!missing.length && !pool.pending && <p className="mt-2 text-muted-foreground">{missing.join(" · ")}</p>}
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        <dt>{t("engine")}</dt><dd className="break-all">{pool.image} {pool.engine_version && `(${pool.engine_version})`} · {pool.gpu_type || ts("gpuUnset")}</dd>
        <dt>{t("rules")}</dt><dd className="break-all">{ruleVersion} · {pool.revision || pool.metadata_digest?.slice(0, 12) || "—"}</dd>
        {pool.metadata_source && <><dt>{ts("metadata")}</dt><dd>{ts.has(`source.${pool.metadata_source}`) ? ts(`source.${pool.metadata_source}`) : pool.metadata_source}</dd></>}
        {pool.tp != null && <><dt>TP / PP / DCP</dt><dd>{pool.tp} / {pool.pp} / {pool.dcp}</dd>
          <dt>KV / block</dt><dd>{pool.kv_dtype} / {pool.block_tokens ?? "auto"}</dd>
          <dt>max-model-len / max-num-seqs</dt><dd>{pool.max_model_len ?? "auto"} / {pool.max_num_seqs ?? "auto"}</dd></>}
        {pool.required_bytes_per_replica != null && <><dt>{t("total")}</dt><dd>{requiredMemory(pool.required_bytes_per_replica)}</dd></>}
        {pool.resident_weight_floor_bytes_per_gpu != null && <><dt>{t("residentWeightFloor")}</dt><dd>≥ {gib(pool.resident_weight_floor_bytes_per_gpu)}</dd></>}
        {weights?.min_bytes_per_gpu != null && weights.max_bytes_per_gpu != null && <>
          <dt>{t("offloadedWeights")}</dt>
          <dd>{gib(weights.min_bytes_per_gpu)}{weights.max_bytes_per_gpu !== weights.min_bytes_per_gpu && <> – {gib(weights.max_bytes_per_gpu)}</>}</dd>
        </>}
        {pool.weight_residency && <>
          <dt>{t("residentWeights")}</dt><dd>{gib(pool.weight_residency.resident_weight_bytes)}</dd>
          {(["engram_embeddings", "routed_experts", "draft", "vision", "audio"] as const).map(kind => {
            const bytes = pool.weight_residency?.excluded_bytes[kind];
            return bytes ? <div className="contents" key={kind}><dt>{t(`excludedWeights.${kind}`)}</dt><dd>{gib(bytes)}</dd></div> : null;
          })}
        </>}
      </dl>
      {pool.components?.map((c,i) => <div key={i} className="mt-1 text-muted-foreground">
        PP {c.pipeline_rank} · {c.kind} × {c.layers} · {number(c.bytes_per_gpu)} B / {t(c.unit)} / GPU
      </div>)}
      {pool.assumptions?.map(note => <p key={note} className="mt-1 text-muted-foreground">{note}</p>)}
    </details>
  </article>;
}

export function RecipeKvCapacity({ data, scenario, onChange }: {
  data?: RecipeCapacity;
  scenario: CapacityScenario;
  onChange: (value: CapacityScenario) => void;
}) {
  const t = useTranslations("recipeCapacity");
  const ts = useTranslations("savedRecipeCapacity");
  const fields = ["context_tokens", "requests", "shared_prefix_tokens"] as const;
  return <section className="space-y-3" aria-label={t("title")}>
    <h3 className="text-base font-semibold">{t("title")}</h3>
    {data?.pools.length === 0 && <p role="status" className="text-sm text-muted-foreground">{ts("emptyPools")}</p>}
    {data?.pools.map(pool => <PoolCapacity key={pool.role} pool={pool} ruleVersion={data.rule_version} />)}
    <div className="grid max-w-2xl grid-cols-1 gap-3 text-sm sm:grid-cols-3">
      {fields.map(key => <label key={key} className="space-y-1">
        <span>{t(key)}</span>
        <Input className="h-7 text-xs" type="number" min={key === "shared_prefix_tokens" ? 0 : 1}
          max={key === "shared_prefix_tokens" ? scenario.context_tokens : key === "requests" ? 1000000 : 100000000}
          defaultValue={scenario[key]} key={scenario[key]}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }}
          onBlur={e => {
            const n = e.currentTarget.valueAsNumber;
            if (Number.isSafeInteger(n) && e.currentTarget.validity.valid) {
              const next = { ...scenario, [key]: n };
              next.shared_prefix_tokens = Math.min(next.context_tokens, next.shared_prefix_tokens);
              onChange(next);
            } else e.currentTarget.value = String(scenario[key]);
          }} />
      </label>)}
    </div>
  </section>;
}

