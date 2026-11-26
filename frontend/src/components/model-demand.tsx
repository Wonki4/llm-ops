"use client";

import { useId, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { createRequestKey } from "@/lib/request-key";
import { apiFetch } from "@/lib/api";
import { useModels } from "@/hooks/use-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormSelect, FormSelectItem } from "@/components/ui/form-select";
import { ModelLimitEditor } from "@/components/model-limit-editor";

type Scope = "user" | "personal" | "team" | "key";
type Target = { scope: Scope; subject: string; model: string };
type Rates = { rpm: number; input_tpm: number; output_tpm: number };
type Limits = {
  scope: string;
  rpm: number | null;
  tpm: number | null;
  rpm_inherited?: boolean;
  tpm_inherited?: boolean;
  rpm_starts_key_override?: boolean;
  tpm_starts_key_override?: boolean;
};
type Demand = {
  observed: {
    coverage: string;
    peak: Rates | null;
    p95: Rates | null;
    active_minutes?: number;
    minutes_requested: number;
    first_seen?: string;
    last_seen?: string;
    requests?: number;
    errors?: number;
    missing_tokens?: number;
    unassigned?: number;
    from: string;
    until: string;
  };
  forecasts: {
    rates: Rates;
    submitted_by: string;
    submitted_at: string;
    period_start: string;
    period_end: string;
  }[];
  current_limits: Limits;
};
type Baseline = {
  id: string;
  deployment: string;
  namespace: string;
  aliases: string[];
  preset: string | null;
  rates: Rates;
  created_at: string;
  workload: Record<string, string | number>;
};
type Consumer = Target & {
  source: "submitted" | "peak" | "p95";
  workload_match: boolean;
};
type Recommendation = Consumer & {
  recommended: Rates;
  demand: Rates;
  counted_tpm: number | null;
  current_limits: Limits;
};
type Allocation = {
  basis: string;
  baseline: Baseline;
  available: Rates;
  limiting_dimension: string | null;
  consumers: Recommendation[];
  accounting: string | null;
};
type KeyOption = {
  token: string;
  key_alias?: string | null;
  key_name?: string | null;
};

const metrics = ["rpm", "input_tpm", "output_tpm"] as const;

function RateTable({
  rows,
}: {
  rows: { name: string; rates: Rates | null }[];
}) {
  const t = useTranslations("modelDemand");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums">
        <thead>
          <tr className="border-b text-right">
            <th className="text-left">{t("source")}</th>
            {metrics.map((k) => (
              <th key={k}>{t(k)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, rates }) => (
            <tr key={name} className="border-b text-right">
              <td className="py-2 text-left">{name}</td>
              {metrics.map((k) => (
                <td key={k}>{rates?.[k]?.toLocaleString() ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ModelDemandPanel({
  scope,
  subject,
  admin = false,
  canSubmit = true,
  keys = [],
}: {
  scope: "team" | "user";
  subject: string;
  admin?: boolean;
  canSubmit?: boolean;
  keys?: KeyOption[];
}) {
  const t = useTranslations("modelDemand");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [selection, setSelection] = useState("");
  const id = useId();
  return (
    <details
      className="rounded-lg border bg-card px-4 py-3"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="cursor-pointer font-semibold">{t("title")}</summary>
      {open && (
        <div className="space-y-4 pt-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              setModel(name.trim());
            }}
          >
            {scope === "user" && (
              <label>
                {t("scope")}
                <FormSelect
                  value={selection}
                  onValueChange={setSelection}
                  placeholder={t("user")}
                >
                  <FormSelectItem value="">{t("user")}</FormSelectItem>
                  <FormSelectItem value="personal">
                    {t("personal")}
                  </FormSelectItem>
                  {keys.map((k) => (
                    <FormSelectItem key={k.token} value={k.token}>
                      {t("key")} ·{" "}
                      {k.key_alias || k.key_name || k.token.slice(0, 10)}
                    </FormSelectItem>
                  ))}
                </FormSelect>
              </label>
            )}
            <label>
              {t("alias")}
              <Input
                list={id}
                required
                value={name}
                maxLength={256}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <ModelsList id={id} />
            <Button variant="outline" type="submit">
              {t("view")}
            </Button>
          </form>
          {model && (
            <DemandDetails
              key={JSON.stringify([scope, subject, selection, model])}
              target={{
                scope:
                  scope === "team"
                    ? "team"
                    : selection === "personal"
                      ? "personal"
                      : selection
                        ? "key"
                        : "user",
                subject:
                  selection && selection !== "personal" ? selection : subject,
                model,
              }}
              admin={admin}
              canSubmit={canSubmit}
            />
          )}
        </div>
      )}
    </details>
  );
}

function ModelsList({ id }: { id: string }) {
  const models = useModels();
  return (
    <datalist id={id}>
      {models.data?.map((m) => (
        <option value={m.model_name} key={m.model_name} />
      ))}
    </datalist>
  );
}

function DemandDetails({
  target,
  admin,
  canSubmit,
}: {
  target: Target;
  admin: boolean;
  canSubmit: boolean;
}) {
  const t = useTranslations("modelDemand");
  const [days, setDays] = useState("7");
  const params = new URLSearchParams({ ...target, days });
  const data = useQuery({
    queryKey: ["model-demand", target, days],
    queryFn: () => apiFetch<Demand>("/api/model-demand?" + params),
  });
  const obs = data.data?.observed,
    limits = data.data?.current_limits;
  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm">
        {t("days")}
        <FormSelect value={days} onValueChange={setDays}>
          {[1, 7, 14, 30, 90].map((d) => (
            <FormSelectItem value={d} key={d}>
              {d}
            </FormSelectItem>
          ))}
        </FormSelect>
      </label>
      {data.isPending && <p aria-busy="true">…</p>}
      {data.error && <p role="alert">{data.error.message}</p>}
      {obs && (
        <>
          <RateTable
            rows={[
              { name: t("peak"), rates: obs.peak },
              { name: t("p95"), rates: obs.p95 },
            ]}
          />
          <p className="text-sm text-muted-foreground">
            {obs.coverage === "recorded"
              ? t("coverage", {
                  active: obs.active_minutes ?? 0,
                  total: obs.minutes_requested,
                  first: obs.first_seen ?? "—",
                  last: obs.last_seen ?? "—",
                  errors: obs.errors ?? 0,
                })
              : t(
                  obs.coverage === "unavailable"
                    ? "logsUnavailable"
                    : "noCoverage",
                )}{" "}
            {t("missing", {
              tokens: obs.missing_tokens ?? "—",
              aliases: obs.unassigned ?? "—",
            })}
          </p>
          {!!data.data?.forecasts.length && (
            <div>
              <h4 className="text-sm font-medium">{t("submitted")}</h4>
              <RateTable
                rows={data.data.forecasts.map((f, i) => ({
                  name: `${f.period_start} – ${f.period_end} · ${f.submitted_by} · ${new Date(f.submitted_at).toLocaleString()} (#${i + 1})`,
                  rates: f.rates,
                }))}
              />
            </div>
          )}
        </>
      )}
      {limits && (
        <p className="text-sm">
          {t("current")} (
          {limits.scope === "user_global" ? t("userGlobal") : t(target.scope)})
          · RPM {limits.rpm ?? "—"} {limits.rpm_inherited && t("inherited")} ·
          TPM {limits.tpm ?? "—"} {limits.tpm_inherited && t("inherited")}
        </p>
      )}
      {canSubmit && <ForecastForm target={target} />}
      {admin && <Simulator target={target} days={Number(days)} />}
    </div>
  );
}

function ForecastForm({ target }: { target: Target }) {
  const t = useTranslations("modelDemand");
  const cache = useQueryClient();
  const [mode, setMode] = useState("per_request");
  const [submission, setSubmission] = useState(createRequestKey);
  const today = new Date().toISOString().slice(0, 10);
  const submit = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      apiFetch("/api/model-demand/forecasts", {
        method: "POST",
        body: JSON.stringify({
          ...target,
          mode,
          ...values,
          request_id: submission,
        }),
      }),
    onSuccess: () => {
      setSubmission(createRequestKey());
      return cache.invalidateQueries({ queryKey: ["model-demand", target] });
    },
  });
  return (
    <details>
      <summary className="cursor-pointer text-sm font-medium">
        {t("submitDemand")}
      </summary>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          const v = new FormData(e.currentTarget);
          submit.mutate({
            rpm: Number(v.get("rpm")),
            input_tokens: Number(v.get("inputs")),
            output_tokens: Number(v.get("outputs")),
            period_start: v.get("start"),
            period_end: v.get("end"),
          });
        }}
      >
        <label>
          {t("inputMode")}
          <FormSelect value={mode} onValueChange={setMode}>
            {["per_request", "per_minute"].map((v) => (
              <FormSelectItem value={v} key={v}>
                {t(v)}
              </FormSelectItem>
            ))}
          </FormSelect>
        </label>
        <label>
          {t("start")}
          <Input name="start" required type="date" defaultValue={today} />
        </label>
        <label>
          {t("end")}
          <Input name="end" required type="date" defaultValue={today} />
        </label>
        <label>
          RPM
          <Input name="rpm" type="number" min={0} max={1e9} required />
        </label>
        <label>
          {t(mode === "per_request" ? "inputPer" : "input_tpm")}
          <Input name="inputs" required type="number" min={0} />
        </label>
        <label>
          {t(mode === "per_request" ? "outputPer" : "output_tpm")}
          <Input name="outputs" required type="number" min={0} />
        </label>
        <Button type="submit" disabled={submit.isPending}>
          {t("submit")}
        </Button>
        {submit.error && <p role="alert">{submit.error.message}</p>}
        {submit.isSuccess && <p role="status">{t("submitted")}</p>}
      </form>
    </details>
  );
}

function Simulator({ target, days }: { target: Target; days: number }) {
  const t = useTranslations("modelDemand");
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const runs = useInfiniteQuery({
    queryKey: ["model-capacity-baselines"],
    enabled: open,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiFetch<{ items: Baseline[]; next_offset: number | null }>(
        `/api/model-demand/baselines?offset=${pageParam}`,
      ),
    getNextPageParam: (p) => p.next_offset,
  });
  const [run, setRun] = useState("");
  const [reserve, setReserve] = useState("20");
  const [onDate, setOnDate] = useState(new Date().toISOString().slice(0, 10));
  const [consumers, setConsumers] = useState<Consumer[]>([
    { ...target, source: "peak", workload_match: false },
  ]);
  const [draft, setDraft] = useState<Recommendation | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const items =
    runs.data?.pages
      .flatMap((p) => p.items)
      .filter((r) => r.aliases.includes(target.model)) ?? [];
  const baseline = items.find((b) => b.id === run);
  const request = JSON.stringify({
    run_id: run,
    days,
    on_date: onDate,
    reserve_percent: Number(reserve),
    consumers,
  });
  const simulate = useMutation({
    mutationFn: (body: string) =>
      apiFetch<Allocation>("/api/model-demand/simulate", {
        method: "POST",
        body,
      }),
  });
  const result = request === snapshot ? simulate.data : null;
  function edit(index: number, patch: Partial<Consumer>) {
    setConsumers((rows) =>
      rows.map((r, i) => (index === i ? { ...r, ...patch } : r)),
    );
    setDraft(null);
  }
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer font-medium">
        {t("simulateTitle")}
      </summary>
      {open && (
        <div className="mt-3 space-y-4">
          <label>
            {t("benchmark")}
            <FormSelect
              value={run}
              onValueChange={(v) => {
                setRun(v);
                setDraft(null);
              }}
            >
              {items.map((r) => (
                <FormSelectItem key={r.id} value={r.id}>
                  {r.deployment} · {r.namespace} ·{" "}
                  {r.preset ||
                    t("profileSummary", {
                      inputs: r.workload.random_input_len ?? "—",
                      outputs: r.workload.random_output_len ?? "—",
                      concurrency: r.workload.max_concurrency ?? "—",
                      cache: r.workload.cache_ratio ?? "—",
                    })}{" "}
                  · {new Date(r.created_at).toLocaleString()}
                </FormSelectItem>
              ))}
            </FormSelect>
          </label>
          {runs.isPending && "…"}
          {runs.error && <p role="alert">{runs.error.message}</p>}
          {runs.hasNextPage && (
            <Button variant="outline" onClick={() => runs.fetchNextPage()}>
              {t("more")}
            </Button>
          )}
          {runs.isSuccess && items.length === 0 && <p>{t("noBaseline")}</p>}
          {baseline && (
            <>
              <div className="flex flex-wrap gap-3">
                <label>
                  {t("reserve")}
                  <Input
                    type="number"
                    min={0}
                    max={99}
                    value={reserve}
                    onChange={(e) => setReserve(e.target.value)}
                  />
                </label>
                <label>
                  {t("planningDate")}
                  <Input
                    type="date"
                    value={onDate}
                    onChange={(e) => setOnDate(e.target.value)}
                  />
                </label>
              </div>
              {consumers.map((c, i) => (
                <fieldset
                  key={i}
                  className="flex flex-wrap items-end gap-2 border-b pb-3"
                >
                  <label>
                    {t("scope")}
                    <FormSelect
                      value={c.scope}
                      disabled={i === 0}
                      onValueChange={(v) =>
                        edit(i, { scope: v as Scope, subject: "" })
                      }
                    >
                      {["team", "user", "personal", "key"].map((k) => (
                        <FormSelectItem key={k} value={k}>
                          {t(k)}
                        </FormSelectItem>
                      ))}
                    </FormSelect>
                  </label>
                  <label>
                    {t("id")}
                    <Input
                      disabled={i === 0}
                      value={c.subject}
                      onChange={(e) => edit(i, { subject: e.target.value })}
                    />
                  </label>
                  <label>
                    {t("alias")}
                    <FormSelect
                      value={c.model}
                      disabled={i === 0}
                      onValueChange={(model) => edit(i, { model })}
                    >
                      {baseline.aliases.map((m) => (
                        <FormSelectItem key={m} value={m}>
                          {m}
                        </FormSelectItem>
                      ))}
                    </FormSelect>
                  </label>
                  <label>
                    {t("source")}
                    <FormSelect
                      value={c.source}
                      onValueChange={(v) =>
                        edit(i, { source: v as Consumer["source"] })
                      }
                    >
                      {["peak", "p95", "submitted"].map((v) => (
                        <FormSelectItem key={v} value={v}>
                          {t(v)}
                        </FormSelectItem>
                      ))}
                    </FormSelect>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={c.workload_match}
                      onChange={(e) =>
                        edit(i, { workload_match: e.target.checked })
                      }
                    />
                    {t("sameProfile")}
                  </label>
                  {i > 0 && (
                    <Button
                      variant="outline"
                      onClick={() =>
                        setConsumers((rows) => rows.filter((_, j) => j !== i))
                      }
                    >
                      {t("remove")}
                    </Button>
                  )}
                </fieldset>
              ))}
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  disabled={consumers.length >= 64}
                  onClick={() =>
                    setConsumers((rows) => [
                      ...rows,
                      {
                        ...target,
                        scope: "team",
                        subject: "",
                        source: "submitted",
                        workload_match: false,
                      },
                    ])
                  }
                >
                  {t("add")}
                </Button>
                <Button
                  disabled={
                    simulate.isPending ||
                    !onDate ||
                    !reserve ||
                    consumers.some((c) => !c.subject)
                  }
                  onClick={() => {
                    setDraft(null);
                    setSnapshot(request);
                    simulate.mutate(request);
                  }}
                >
                  {t("simulate")}
                </Button>
              </div>
            </>
          )}
          {simulate.error && <p role="alert">{simulate.error.message}</p>}
          {result && (
            <>
              <p>
                {t(result.basis)} · {t("limiting")}{" "}
                {result.limiting_dimension ? t(result.limiting_dimension) : "—"}{" "}
                · {t("accounting")} {result.accounting ?? "—"}
              </p>
              <RateTable
                rows={[{ name: t("available"), rates: result.available }]}
              />
              {result.consumers.map((c) => {
                const label =
                  c.scope === "key" ? c.subject.slice(0, 12) : c.subject;
                return (
                  <section
                    className="space-y-2 py-2"
                    key={JSON.stringify([c.scope, c.subject, c.model])}
                  >
                    <h4 className="font-medium">
                      {t(c.scope)} {label} · {c.model}
                    </h4>
                    <RateTable
                      rows={[
                        { name: t(c.source), rates: c.demand },
                        { name: t("recommended"), rates: c.recommended },
                      ]}
                    />
                    <p className="text-sm">
                      {t("current")} (
                      {c.current_limits.scope === "user_global"
                        ? t("userGlobal")
                        : t(c.scope)}
                      ) · RPM {c.current_limits.rpm ?? "—"} · TPM{" "}
                      {c.current_limits.tpm ?? "—"}
                    </p>
                    {(c.scope === "key" || c.scope === "team") && (
                      <Button
                        variant="outline"
                        disabled={!c.counted_tpm || c.recommended.rpm < 1}
                        onClick={() => setDraft(c)}
                      >
                        {t("fill")}
                      </Button>
                    )}
                  </section>
                );
              })}
              {result.accounting === null && (
                <p className="text-sm text-muted-foreground">
                  {t("unknownAccounting")}
                </p>
              )}
              {draft && (
                <LimitDraft
                  key={JSON.stringify(draft)}
                  draft={draft}
                  onSaved={() => {
                    setDraft(null);
                    setSnapshot("");
                    cache.invalidateQueries({ queryKey: ["model-demand"] });
                  }}
                />
              )}
            </>
          )}
        </div>
      )}
    </details>
  );
}

function LimitDraft({
  draft,
  onSaved,
}: {
  draft: Recommendation;
  onSaved: () => void;
}) {
  const t = useTranslations("modelDemand");
  const [rpm, setRpm] = useState<number | null>(draft.recommended.rpm);
  const [tpm, setTpm] = useState(draft.counted_tpm);
  const [acknowledged, setAcknowledged] = useState(false);
  const current = draft.current_limits;
  const touchesInherited =
    (rpm !== current.rpm && current.rpm_inherited) ||
    (tpm !== current.tpm && current.tpm_inherited);
  const startsOverride =
    (rpm !== current.rpm && current.rpm_starts_key_override) ||
    (tpm !== current.tpm && current.tpm_starts_key_override);
  const save = useMutation({
    mutationFn: () =>
      apiFetch("/api/model-demand/limits", {
        method: "PATCH",
        body: JSON.stringify({
          scope: draft.scope,
          subject: draft.subject,
          model: draft.model,
          rpm,
          tpm,
          acknowledge_key_override: acknowledged,
          expected_rpm: current.rpm,
          expected_tpm: current.tpm,
        }),
      }),
    onSuccess: onSaved,
  });
  return (
    <div className="space-y-3 rounded border p-3">
      <h4>
        {t("editScoped", {
          scope: t(draft.scope),
          id:
            draft.scope === "key" ? draft.subject.slice(0, 12) : draft.subject,
          model: draft.model,
        })}
      </h4>
      <ModelLimitEditor
        fixedModel={draft.model}
        initialRpm={{ [draft.model]: draft.recommended.rpm }}
        initialTpm={{ [draft.model]: draft.counted_tpm! }}
        onChange={(tp, rp) => {
          setTpm(tp[draft.model] ?? null);
          setRpm(rp[draft.model] ?? null);
        }}
      />
      {touchesInherited ? (
        <p role="status">{t("editTeamInstead")}</p>
      ) : (
        startsOverride && (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            {t("confirmKeyOverride")}
          </label>
        )
      )}
      <Button
        disabled={
          save.isPending ||
          touchesInherited ||
          (startsOverride && !acknowledged) ||
          rpm === null ||
          tpm === null ||
          rpm < 1 ||
          tpm < 1 ||
          rpm > 2147483647 ||
          tpm > 2147483647
        }
        onClick={() => save.mutate()}
      >
        {t("save")}
      </Button>
      {save.error && <p role="alert">{save.error.message}</p>}
    </div>
  );
}
