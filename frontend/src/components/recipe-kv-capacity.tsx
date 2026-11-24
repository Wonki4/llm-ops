"use client";

import { useTranslations } from "next-intl";
import { Input } from "@/components/ui/input";
import type { CapacityScenario, RecipeCapacity } from "@/types";

const number = (n: number) => n.toLocaleString();
const gib = (bytes: number) => `${(bytes / 2 ** 30).toLocaleString(undefined, { maximumFractionDigits: 3 })} GiB`;

function Quantity({ value, range, unknown }: { value?: number | null; range?: number[]; unknown: string }) {
  if (value != null) return <>{number(value)}</>;
  if (range) return <>{number(range[0])} – {number(range[1])}</>;
  return <>{unknown}</>;
}

export function RecipeKvCapacity({ data, scenario, onChange }: {
  data?: RecipeCapacity;
  scenario: CapacityScenario;
  onChange: (value: CapacityScenario) => void;
}) {
  const t = useTranslations("recipeCapacity");
  const fields = ["context_tokens", "requests", "shared_prefix_tokens"] as const;
  return <section className="space-y-2 rounded-md border p-3 text-xs" aria-label={t("title")}>
    <h3 className="text-sm font-semibold">{t("title")}</h3>
    {data?.pools.map(pool => <div key={pool.role} className="space-y-1">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <strong>{pool.role === "aggregated" ? t("replica") : pool.role}</strong>
        {pool.pending ? <span role="status">{t("loading")}</span> : <>
          <span><Quantity value={pool.capacity_tokens} range={pool.capacity_range} unknown="—" /> {t("tokens")}</span>
          <span>{t("whole")} <Quantity value={pool.complete_requests} range={pool.complete_requests_range} unknown="—" /></span>
        </>}
      </div>
      {!!pool.missing?.length && !pool.pending && <p className="text-muted-foreground">{pool.missing.join(" · ")}</p>}
      <details>
        <summary className="cursor-pointer text-muted-foreground">{t("basis")}</summary>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt>{t("engine")}</dt><dd className="break-all">{pool.image} {pool.engine_version && `(${pool.engine_version})`} · {pool.gpu_type || "—"}</dd>
          <dt>{t("rules")}</dt><dd>{data.rule_version} · {pool.revision || pool.metadata_digest?.slice(0, 12) || "—"}</dd>
          {pool.tp != null && <><dt>TP / PP / DCP</dt><dd>{pool.tp} / {pool.pp} / {pool.dcp}</dd>
            <dt>KV / block</dt><dd>{pool.kv_dtype} / {pool.block_tokens ?? "auto"}</dd>
            <dt>max-model-len / max-num-seqs</dt><dd>{pool.max_model_len ?? "auto"} / {pool.max_num_seqs ?? "auto"}</dd></>}
          {pool.required_bytes_per_gpu != null && <><dt>{t("required")}</dt><dd>{gib(pool.required_bytes_per_gpu)}</dd></>}
          {pool.required_bytes_per_replica != null && <><dt>{t("total")}</dt><dd>{gib(pool.required_bytes_per_replica)}</dd></>}
          {pool.shared_required_bytes_per_gpu != null && <><dt>{t("shared")}</dt><dd>{gib(pool.shared_required_bytes_per_gpu)}</dd></>}
          {pool.budget_bytes_per_gpu && <><dt>{t("budget")}</dt><dd>
            {pool.budget_bytes_per_gpu.map(gib).filter((v,i,a) => !i || v !== a[i-1]).join(" – ")} · {t(pool.budget_source!)}
          </dd></>}
        </dl>
        {pool.components?.map((c,i) => <div key={i} className="mt-1 text-muted-foreground">
          PP {c.pipeline_rank} · {c.kind} × {c.layers} · {number(c.bytes_per_gpu)} B / {t(c.unit)} / GPU
        </div>)}
        {pool.assumptions?.map(note => <p key={note} className="mt-1 text-muted-foreground">{note}</p>)}
      </details>
    </div>)}
    <div className="grid grid-cols-3 gap-2">
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

