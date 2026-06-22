"use client";

import { useState } from "react";
import { Loader2, Pencil, Plus, Trash2, X, Save } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { apiFetch } from "@/lib/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ─── Types ──────────────────────────────────────────────────────

interface CostRule {
  id: string;
  model_name: string;
  days_of_week: number[]; // 1=Mon..7=Sun, in the schedule timezone
  hour_start_local: number;
  hour_end_local: number;
  timezone: string; // IANA name the hours/days are authored in (e.g. Asia/Seoul)
  input_cost_per_token: number;
  output_cost_per_token: number;
  priority: number;
  enabled: boolean;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface CostScheduleResponse {
  model_name: string;
  rules: CostRule[];
}

// Rules are authored, stored, and displayed in the schedule timezone
// (settings.schedule_timezone on the backend, surfaced as rule.timezone). The
// backend converts the current time into that zone when matching, so no UTC
// round-trip is needed here — what you pick is exactly what's stored.

// ─── Hooks ──────────────────────────────────────────────────────

function useCostSchedule(modelName: string | null) {
  return useQuery({
    queryKey: ["cost-schedule", modelName],
    queryFn: () =>
      apiFetch<CostScheduleResponse>(`/api/models/${encodeURIComponent(modelName!)}/cost-schedule`),
    enabled: !!modelName,
  });
}

interface RuleBody {
  days_of_week: number[];
  hour_start_local: number;
  hour_end_local: number;
  input_cost_per_token: number;
  output_cost_per_token: number;
  priority: number;
  enabled: boolean;
}

function useCreateCostRule(modelName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RuleBody) =>
      apiFetch<CostRule>(`/api/models/${encodeURIComponent(modelName)}/cost-schedule`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cost-schedule", modelName] });
    },
  });
}

function useUpdateCostRule(modelName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: RuleBody }) =>
      apiFetch<CostRule>(`/api/models/cost-schedule/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cost-schedule", modelName] });
    },
  });
}

function useDeleteCostRule(modelName: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ deleted: boolean }>(`/api/models/cost-schedule/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cost-schedule", modelName] });
    },
  });
}

// ─── Form state ─────────────────────────────────────────────────

interface FormState {
  daysKst: number[];
  hourStartKst: string; // string for input control
  hourEndKst: string;
  inputCost: string;
  outputCost: string;
  priority: string;
  enabled: boolean;
}

// LiteLLM stores cost as per-1-token (e.g. 0.00000125). Humans think in
// per-1M-tokens (e.g. $1.25), and the rest of the portal already shows costs
// that way, so the form converts at the boundary: divide by 1M on submit,
// multiply by 1M when loading.
const PER_MILLION = 1_000_000;

function toPerMillion(perToken: number): string {
  return (perToken * PER_MILLION).toString();
}

function ruleToForm(rule: CostRule | null): FormState {
  if (!rule) {
    return {
      daysKst: [],
      hourStartKst: "",
      hourEndKst: "",
      inputCost: "",
      outputCost: "",
      priority: "0",
      enabled: true,
    };
  }
  return {
    daysKst: [...rule.days_of_week].sort((a, b) => a - b),
    hourStartKst: String(rule.hour_start_local),
    hourEndKst: String(rule.hour_end_local),
    inputCost: toPerMillion(rule.input_cost_per_token),
    outputCost: toPerMillion(rule.output_cost_per_token),
    priority: String(rule.priority),
    enabled: rule.enabled,
  };
}

function formToBody(form: FormState, t: ReturnType<typeof useTranslations>): RuleBody | string {
  if (form.daysKst.length === 0) return t("validation.selectDays");
  const hs = Number(form.hourStartKst);
  const he = Number(form.hourEndKst);
  if (!Number.isInteger(hs) || hs < 0 || hs > 23) return t("validation.invalidStartHour");
  if (!Number.isInteger(he) || he < 1 || he > 24) return t("validation.invalidEndHour");
  if (hs === he) return t("validation.sameStartEnd");
  const inCostPerMillion = Number(form.inputCost);
  const outCostPerMillion = Number(form.outputCost);
  if (!Number.isFinite(inCostPerMillion) || inCostPerMillion < 0)
    return t("validation.invalidInputCost");
  if (!Number.isFinite(outCostPerMillion) || outCostPerMillion < 0)
    return t("validation.invalidOutputCost");

  return {
    days_of_week: [...form.daysKst].sort((a, b) => a - b),
    hour_start_local: hs,
    hour_end_local: he,
    input_cost_per_token: inCostPerMillion / PER_MILLION,
    output_cost_per_token: outCostPerMillion / PER_MILLION,
    priority: Number(form.priority) || 0,
    enabled: form.enabled,
  };
}

// ─── UI ─────────────────────────────────────────────────────────

function DaySelector({ value, onChange }: { value: number[]; onChange: (days: number[]) => void }) {
  const t = useTranslations("modelCostSchedule");
  const dayLabels = [t("mon"), t("tue"), t("wed"), t("thu"), t("fri"), t("sat"), t("sun")];
  return (
    <div className="flex gap-1">
      {dayLabels.map((label, idx) => {
        const day = idx + 1; // ISO weekday
        const active = value.includes(day);
        return (
          <button
            key={day}
            type="button"
            onClick={() => {
              const next = active ? value.filter((d) => d !== day) : [...value, day].sort();
              onChange(next);
            }}
            className={`flex h-7 w-7 items-center justify-center rounded text-xs ${
              active
                ? "bg-primary text-primary-foreground"
                : "border border-input bg-transparent hover:bg-muted"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

function RuleForm({
  modelName,
  rule,
  onDone,
}: {
  modelName: string;
  rule: CostRule | null;
  onDone: () => void;
}) {
  const t = useTranslations("modelCostSchedule");
  const tc = useTranslations("common");
  const [form, setForm] = useState<FormState>(ruleToForm(rule));
  const createMutation = useCreateCostRule(modelName);
  const updateMutation = useUpdateCostRule(modelName);
  const pending = createMutation.isPending || updateMutation.isPending;

  function handleSave() {
    const result = formToBody(form, t);
    if (typeof result === "string") {
      toast.error(result);
      return;
    }
    if (rule) {
      updateMutation.mutate(
        { id: rule.id, body: result },
        {
          onSuccess: () => {
            toast.success(t("toast.ruleSaved"));
            onDone();
          },
          onError: (err) => toast.error(err instanceof Error ? err.message : t("toast.saveFailed")),
        },
      );
    } else {
      createMutation.mutate(result, {
        onSuccess: () => {
          toast.success(t("toast.ruleAdded"));
          onDone();
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("toast.addFailed")),
      });
    }
  }

  return (
    <div className="space-y-3 rounded-md border bg-muted/30 p-3">
      <div>
        <label className="text-xs text-muted-foreground">{t("form.daysKst")}</label>
        <DaySelector
          value={form.daysKst}
          onChange={(days) => setForm((f) => ({ ...f, daysKst: days }))}
        />
        <p className="text-[10px] text-muted-foreground mt-1">
          {t("form.daySpanningHint")}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">{t("form.startHour")}</label>
          <Input
            type="number"
            min="0"
            max="23"
            value={form.hourStartKst}
            onChange={(e) => setForm((f) => ({ ...f, hourStartKst: e.target.value }))}
            className="h-8 text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("form.endHour")}</label>
          <Input
            type="number"
            min="1"
            max="24"
            value={form.hourEndKst}
            onChange={(e) => setForm((f) => ({ ...f, hourEndKst: e.target.value }))}
            className="h-8 text-sm"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">{t("form.inputCost")}</label>
          <Input
            type="number"
            step="0.01"
            min="0"
            value={form.inputCost}
            onChange={(e) => setForm((f) => ({ ...f, inputCost: e.target.value }))}
            className="h-8 text-sm font-mono"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("form.outputCost")}</label>
          <Input
            type="number"
            step="0.01"
            min="0"
            value={form.outputCost}
            onChange={(e) => setForm((f) => ({ ...f, outputCost: e.target.value }))}
            className="h-8 text-sm font-mono"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 items-end">
        <div>
          <label className="text-xs text-muted-foreground">{t("form.priority")}</label>
          <Input
            type="number"
            value={form.priority}
            onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
            className="h-8 text-sm"
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
          />
          {t("form.enabled")}
        </label>
      </div>
      <div className="flex gap-1">
        <Button size="sm" onClick={handleSave} disabled={pending}>
          {pending ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
          {tc("save")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          <X className="size-3" />
          {tc("cancel")}
        </Button>
      </div>
    </div>
  );
}

function RuleRow({ modelName, rule }: { modelName: string; rule: CostRule }) {
  const t = useTranslations("modelCostSchedule");
  const tc = useTranslations("common");
  const [editing, setEditing] = useState(false);
  const deleteMutation = useDeleteCostRule(modelName);

  if (editing) {
    return <RuleForm modelName={modelName} rule={rule} onDone={() => setEditing(false)} />;
  }

  const startHour = rule.hour_start_local;
  const endHour = rule.hour_end_local;
  const dayNames = [t("mon"), t("tue"), t("wed"), t("thu"), t("fri"), t("sat"), t("sun")];
  const dayLabels = rule.days_of_week.map((d) => dayNames[d - 1]).join(", ");

  function handleDelete() {
    if (!confirm(t("confirm.deleteRule"))) return;
    deleteMutation.mutate(rule.id, {
      onSuccess: () => toast.success(t("toast.ruleDeleted")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("toast.deleteFailed")),
    });
  }

  return (
    <Card>
      <CardContent className="pt-3 pb-3 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge variant="outline" className="text-[10px]">
              {dayLabels}
            </Badge>
            <span className="text-xs font-mono">
              {String(startHour).padStart(2, "0")}:00–{String(endHour).padStart(2, "0")}:00 {rule.timezone}
            </span>
            <Badge variant={rule.enabled ? "default" : "secondary"} className="text-[10px]">
              {rule.enabled ? t("rule.active") : t("rule.inactive")}
            </Badge>
            <span className="text-[10px] text-muted-foreground">prio {rule.priority}</span>
          </div>
          <div className="flex gap-1">
            <Button variant="ghost" size="icon-xs" onClick={() => setEditing(true)} title={tc("edit")}>
              <Pencil className="size-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
              title={tc("delete")}
            >
              <Trash2 className="size-3 text-destructive" />
            </Button>
          </div>
        </div>
        <div className="text-[11px] font-mono text-muted-foreground">
          in ${(rule.input_cost_per_token * PER_MILLION).toFixed(2)} · out $
          {(rule.output_cost_per_token * PER_MILLION).toFixed(2)} / 1M
        </div>
      </CardContent>
    </Card>
  );
}

export function ModelCostScheduleSection({ modelName }: { modelName: string }) {
  const t = useTranslations("modelCostSchedule");
  const { data, isLoading } = useCostSchedule(modelName);
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {t("description")}
        </p>
        {!adding && (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus className="size-3" />
            {t("addRule")}
          </Button>
        )}
      </div>
      {adding && <RuleForm modelName={modelName} rule={null} onDone={() => setAdding(false)} />}
      {isLoading ? (
        <div className="flex items-center justify-center py-3">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : data && data.rules.length > 0 ? (
        <div className="space-y-2">
          {data.rules.map((r) => (
            <RuleRow key={r.id} modelName={modelName} rule={r} />
          ))}
        </div>
      ) : !adding ? (
        <div className="text-xs text-muted-foreground py-2">{t("noRules")}</div>
      ) : null}
    </div>
  );
}
