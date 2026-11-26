"use client";

import { useId, useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSavedRecipeThroughput } from "@/hooks/use-api";
import { useDebounced } from "@/hooks/use-debounced";
import {
  type CacheCondition, defaultThroughputInputs, parseThroughputInputs, throughputFields,
  throughputFieldValue, throughputForCondition, throughputLimits,
} from "@/lib/recipe-throughput-scenario";
import type { RecipeCalibration, SavedRecipeThroughput, ServingRecipe, ThroughputScenario } from "@/types";

function Calibration({ data }: { data: RecipeCalibration }) {
  const t = useTranslations("recipeThroughput");
  const f = useFormatter();
  const range = ([lo, hi]: [number, number]) => {
    const fmt = (n: number) => f.number(n, {maximumSignificantDigits: 3});
    return fmt(lo) === fmt(hi) ? fmt(lo) : `${fmt(lo)} – ${fmt(hi)}`;
  };
  return <details className="text-sm">
    <summary className="cursor-pointer">{t("calibration.title", {n: data.repetitions})}</summary>
    <div className="mt-2 space-y-1">
      <p>{t("calibration.observed", {rpm: range(data.measured_rpm)})}</p>
      {data.forecast_rpm && <p>{t("calibration.forecast", {rpm: range(data.forecast_rpm)})}</p>}
      {data.forecast_version && <p className="text-xs text-muted-foreground">{t("ruleVersion", {version: data.forecast_version})}</p>}
      {data.target_requests_per_second != null && <p>{t("calibration.offer", {rate: data.target_requests_per_second})}
        {data.search_stop_reason && t.has(`calibration.stop.${data.search_stop_reason}`) &&
          <> · {t(`calibration.stop.${data.search_stop_reason}`)}</>}</p>}
      {data.slo_rates && <p>{t("calibration.goodput", {
        rpm: range(data.slo_rates.rpm), input: range(data.slo_rates.input_tpm), output: range(data.slo_rates.output_tpm),
      })}</p>}
      {data.phases ? (["prefill", "decode"] as const).map(key => {
        const phase = data.phases![key];
        return <p key={key}>{t("calibration.timing", {phase: t(`phase.${key}`),
          measured: range(phase.measured_seconds), predicted: range(phase.predicted_seconds)})}
          {phase.deviation_percent != null && <> · {phase.deviation_percent === 0 ? t("calibration.within")
            : t("calibration.deviation", {pct: f.number(phase.deviation_percent / 100, {
              style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero",
            })})}</>}</p>;
      }) : <>
        {data.observed_request_seconds && <p>{t("calibration.perRequest", {
          prefill: range(data.observed_request_seconds.prefill), decode: range(data.observed_request_seconds.decode),
        })}</p>}
        {data.phase_reason && t.has(`calibration.${data.phase_reason}`) && <p>{t(`calibration.${data.phase_reason}`)}</p>}
      </>}
    </div>
  </details>;
}

function Diagnostics({ result, onApply }: {
  result: SavedRecipeThroughput; onApply: (workload: ThroughputScenario) => void;
}) {
  const t = useTranslations("recipeThroughput");
  const shorter = result.shorter_workload;
  const reasons = new Set([result.reason, shorter?.reason]);
  const issues = new Set(result.pools.map(p => p.metadata_issue?.code).filter(Boolean));
  const formats = new Set(result.pools.map(p => p.metadata_issue?.format).filter(Boolean));
  const memory = reasons.has("gpu_memory") || reasons.has("weight_budget");
  return <div className="space-y-1.5 text-sm" data-testid="throughput-diagnostics">
    {shorter && <div className="flex flex-wrap items-center gap-2">
      <span>{t("diagnostics.tried", {input: shorter.scenario.input_tokens, output: shorter.scenario.output_tokens})}
        {" · "}{shorter.status === "estimated" ? t("diagnostics.fits")
          : shorter.reason && t.has(`reason.${shorter.reason}`) ? t(`reason.${shorter.reason}`) : t(shorter.status)}</span>
      {shorter.status === "estimated" && <Button size="sm" variant="outline" type="button"
        onClick={() => onApply(shorter.scenario)}>{t("diagnostics.apply")}</Button>}
    </div>}
    {reasons.has("model_unknown") && Array.from(issues).map(code => code && t.has(`diagnostics.${code}`) &&
      <p key={code}>{t(`diagnostics.${code}`)}</p>)}
    {reasons.has("model_unknown") && Array.from(formats).map(fmt => fmt && t.has(`diagnostics.${fmt}`) &&
      <p key={fmt}>{t(`diagnostics.${fmt}`)}</p>)}
    {reasons.has("model_unknown") && result.pools.some(p => p.metadata_issue?.source === "public_audit") &&
      <p className="text-xs text-muted-foreground">{t("diagnostics.publicEvidence")}</p>}
    {reasons.has("model_unknown") && issues.size === 0 && <p>{t("diagnostics.unknownGeometry")}</p>}
    {reasons.has("draft_unknown") && result.draft_missing && t.has(`diagnostics.${result.draft_missing}`) &&
      <p>{result.draft_method && <strong>{result.draft_method.toUpperCase()} · </strong>}{t(`diagnostics.${result.draft_missing}`)}</p>}
    {reasons.has("draft_unknown") && result.draft_missing === "draft_placement" && result.pools.map((p, i) =>
      p.tp && p.pp && p.dcp ? <p key={i}>{t("diagnostics.placement", {tp: p.tp, pp: p.pp, dcp: p.dcp})}</p> : null)}
    {memory && result.pools.map((p, i) => p.gpu_count && p.gpu_vram_gb ? <p key={i}>
      {result.pools.length > 1 && t.has(`pool.${p.role}`) && <>{t(`pool.${p.role}`)} · </>}
      {t("diagnostics.gpu", {gpu: p.gpu_type ?? "GPU", count: p.gpu_count, gb: p.gpu_vram_gb})}
      {p.weight_bytes != null && <> · {t("diagnostics.checkpoint", {gb: Math.round(p.weight_bytes / 1e9)})}</>}
    </p> : null)}
    {memory && <p>{t("diagnostics.memoryAction")}</p>}
    {reasons.has("no_gpu") && <p>{t("diagnostics.allocateGpu")}</p>}
    {reasons.has("unsupported_workload") && <p>{t("diagnostics.taskMetric")}</p>}
  </div>;
}

function Estimate({ result, cacheCondition, onCacheChange, onApply }: {
  result: SavedRecipeThroughput; cacheCondition: CacheCondition; onCacheChange: (condition: CacheCondition) => void;
  onApply: (workload: ThroughputScenario) => void;
}) {
  const t = useTranslations("recipeThroughput");
  const format = useFormatter();
  const number = (n: number) => format.number(n, { maximumSignificantDigits: 3 });
  const reason = result.reason;
  const { rates, condition, canChoose, scenario } = throughputForCondition(result, cacheCondition);
  const phases = scenario?.phase_seconds;
  const hasRates = rates.rpm && rates.input_tpm && rates.output_tpm;
  const groupName = useId();
  const sources = (result.sources ?? []).filter(source => /^https?:\/\//i.test(source.url));
  const contextLimits = result.pools.map(pool => pool.max_model_len).filter(
    (limit): limit is number => typeof limit === "number" && limit > 0,
  );
  return <div className="space-y-3">
    {result.pending ? <p role="status" className="text-sm text-muted-foreground">{t("metadataPending")}</p> : <>
      {canChoose && <fieldset className="space-y-1.5">
        <legend className="text-sm font-medium">{t("cacheCondition")}</legend>
        <div className="inline-flex max-w-full flex-wrap gap-0.5 rounded-md border p-0.5">
          {(["warm", "cold", "unknown"] as const).map(option => <label key={option} className="cursor-pointer">
            <input type="radio" className="peer sr-only" name={groupName} value={option} checked={condition === option}
              onChange={() => onCacheChange(option)} />
            <span className="block rounded px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted/60 peer-checked:bg-muted peer-checked:font-medium peer-checked:text-foreground peer-focus-visible:ring-2 peer-focus-visible:ring-ring">
              {t(`condition.${option}`)}
            </span>
          </label>)}
        </div>
      </fieldset>}
      {condition && <p className="text-sm text-muted-foreground">
        {condition === "warm" && scenario ? t("warmInput", {
          reused: scenario.reused_input_tokens, fresh: result.scenario.input_tokens - scenario.reused_input_tokens,
        }) : condition === "unknown" ? t("unknownInput")
          : t(result.assumptions.includes("prefix_disabled") ? "disabledInput" : "coldInput", {
            tokens: result.scenario.input_tokens,
          })}
      </p>}
      {hasRates && <dl className="grid gap-2 sm:grid-cols-3">
        {(["rpm", "input_tpm", "output_tpm"] as const).map(key => {
          const [low, high] = rates[key]!;
          const range = number(low) === number(high) ? number(low) : `${number(low)} – ${number(high)}`;
          return <div className="rounded-md bg-muted/50 px-3 py-2" key={key}>
            <dt className="text-xs text-muted-foreground">{t(key)}</dt>
            <dd data-testid={`recipe-throughput-${key}`} className="mt-1 font-semibold tabular-nums">{range}</dd>
          </div>;
        })}
      </dl>}
      {result.status === "estimated" && <p className="text-xs text-muted-foreground">
        {result.method === "historical" && result.benchmark_id
          ? <Link href={`/admin/benchmarks/${encodeURIComponent(result.benchmark_id)}`} className="underline underline-offset-2">{t("benchmarkEvidence")}</Link>
          : t(result.scope === "pd_pair" ? "hardwareEvidencePd" : "hardwareEvidence")}
      </p>}
      {result.status !== "estimated" && <p className="text-sm text-muted-foreground">
        {t(result.status)}{reason && <> · {t.has(`reason.${reason}`) ? t(`reason.${reason}`) : reason}</>}
        {reason === "context_limit" && contextLimits.length > 0 && <span className="mt-1 block">
          {t("contextLimitDetails", {
            requested: result.scenario.input_tokens + result.scenario.output_tokens, max: Math.min(...contextLimits),
          })}
        </span>}
      </p>}
      {result.status !== "estimated" && <Diagnostics result={result} onApply={onApply} />}
      {result.calibration && <Calibration data={result.calibration} />}
      {(result.pools.length > 0 || result.assumptions.length > 0 || sources.length > 0) && <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">{t("basis")}</summary>
        <div className="mt-2 space-y-1 text-muted-foreground">
          {result.version && <p>{t("ruleVersion", {version: result.version})}</p>}
          {result.pools.map((pool, index) => <p key={`${pool.role}-${index}`}>
            {t.has(`pool.${pool.role}`) ? t(`pool.${pool.role}`) : pool.role} · {pool.engine}
            {pool.gpu_type && <> · {pool.gpu_type}</>}
            {pool.gpu_count != null && <> × {format.number(pool.gpu_count)}</>}
            {pool.tp && pool.pp && pool.dcp && (pool.tp > 1 || pool.pp > 1 || pool.dcp > 1) &&
              <> · {t("diagnostics.placement", {tp: pool.tp, pp: pool.pp, dcp: pool.dcp})}</>}
            {pool.draft_method && <> · {pool.draft_method.toUpperCase()}{pool.draft_tokens != null &&
              <> · {t("draftWidth", {count: pool.draft_tokens})}</>}</>}
          </p>)}
          {phases && scenario ? <div className="space-y-0.5">
            <p>{t("batchTiming", { count: scenario.effective_concurrency })}</p>
            <dl className="flex flex-wrap gap-x-4 gap-y-1">
              {(["prefill", "decode"] as const).map(phase => {
                const [secondsLow, secondsHigh] = phases[phase];
                const low = number(secondsLow), high = number(secondsHigh);
                return <div key={phase} className="flex gap-1.5">
                  <dt>{t(`phase.${phase}`)}</dt>
                  <dd className="tabular-nums">{t("seconds", { value: low === high ? low : `${low} – ${high}` })}</dd>
                </div>;
              })}
            </dl>
          </div> : scenario ? <p>{t("conditionConcurrency", { count: scenario.effective_concurrency })}</p>
            : result.effective_concurrency && <p>{t("effectiveConcurrency", {
            cold: result.effective_concurrency[0], warm: result.effective_concurrency[1],
          })}</p>}
          {result.efficiency && <p>{t("efficiency", {
            computeLow: result.efficiency.compute[0] * 100, computeHigh: result.efficiency.compute[1] * 100,
            memoryLow: result.efficiency.memory[0] * 100, memoryHigh: result.efficiency.memory[1] * 100,
          })}</p>}
          {result.assumptions.filter(note => !condition || !["prefix_reuse", "prefix_disabled"].includes(note))
            .map(note => <p key={note}>{t.has(`assumption.${note}`) ? t(`assumption.${note}`) : note}</p>)}
          {sources.length > 0 && <ul className="list-inside list-disc">
            {sources.map(source => <li key={`${source.url}-${source.label}`}>
              <a href={source.url} rel="noreferrer" target="_blank" className="underline underline-offset-2">{source.label}</a>
            </li>)}
          </ul>}
        </div>
      </details>}
    </>}
  </div>;
}

export function RecipeThroughput({ recipe, clusterId, namespace }: {
  recipe: ServingRecipe; clusterId: string | null; namespace: string | null;
}) {
  const t = useTranslations("recipeThroughput");
  const [inputs, setInputs] = useState(defaultThroughputInputs);
  const [cacheCondition, setCacheCondition] = useState<CacheCondition>("warm");
  const inputId = useId();
  const scenario = useMemo(() => parseThroughputInputs(inputs), [inputs]);
  const settled = useDebounced(scenario, 350);
  const current = scenario !== null && scenario === settled;
  const query = useSavedRecipeThroughput(recipe, clusterId, namespace, current ? scenario : null);
  return <section aria-label={t("title")} data-testid="recipe-throughput" className="space-y-4 rounded-lg border bg-card p-4">
    <div>
      <h3 className="font-semibold">{t("title")}</h3>
      <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {throughputFields.map(field => {
        const [min, max] = throughputLimits[field];
        const invalid = throughputFieldValue(field, inputs[field]) === null;
        const id = `${inputId}-${field}`;
        return <div className="min-w-0 space-y-1 text-sm" key={field}>
          <label htmlFor={id}>{t(field)}</label>
          <Input id={id} type="number" inputMode="numeric" min={min} max={max} step="1" required
            name={field} value={inputs[field]} aria-invalid={invalid}
            aria-describedby={invalid ? id + "-error" : undefined}
            onChange={event => setInputs({ ...inputs, [field]: event.target.value })} />
          {invalid && <p id={id + "-error"} className="text-xs text-destructive">{t("inputRange", { min, max })}</p>}
        </div>;
      })}
    </div>
    <div aria-live="polite" aria-busy={scenario !== null && (!current || query.isPending)}>
      {scenario === null ? <p role="status" className="text-sm text-muted-foreground">{t("invalid")}</p>
        : !current || query.isPending ? <p role="status" className="text-sm text-muted-foreground">{t("loading")}</p>
        : query.isError ? <p role="alert" className="text-sm text-destructive">{query.error.message}</p>
        : <Estimate result={query.data} cacheCondition={cacheCondition} onCacheChange={setCacheCondition}
          onApply={w => setInputs({input_tokens: String(w.input_tokens), output_tokens: String(w.output_tokens),
            concurrency: String(w.concurrency), shared_prefix_percent: String(w.shared_prefix_percent)})} />}
    </div>
  </section>;
}
