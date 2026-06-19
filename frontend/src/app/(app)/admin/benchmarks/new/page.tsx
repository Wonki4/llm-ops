"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateBenchmark, useModels, useModelDeployments } from "@/hooks/use-api";
import type { BenchmarkTool, CreateBenchmarkRequest } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const TOOL_OPTIONS: BenchmarkTool[] = [
  "vllm_serving",
  "sglang_serving",
  "lm_eval",
];

const TOOL_TO_KIND: Record<BenchmarkTool, "performance" | "accuracy"> = {
  vllm_serving: "performance",
  sglang_serving: "performance",
  lm_eval: "accuracy",
};

const DEFAULT_PERF_PARAMS = {
  num_prompts: 32,
  concurrency: 8,
  max_tokens: 128,
  temperature: 0,
  request_rate: "",
  ignore_eos: false,
  prompt: "Write a short paragraph explaining the difference between TCP and UDP.",
};

const DEFAULT_ACCURACY_PARAMS = {
  tasks: "mmlu",
  num_fewshot: "",
  limit: "",
  batch_size: 8,
  num_concurrent: 4,
};

export default function NewBenchmarkPage() {
  const t = useTranslations("benchmarkForm");
  const tc = useTranslations("common");
  const router = useRouter();
  const { data: models, isLoading: modelsLoading } = useModels();
  const { data: deployments } = useModelDeployments();
  const createMutation = useCreateBenchmark();

  const [deploymentId, setDeploymentId] = useState("");
  const [ephemeral, setEphemeral] = useState(false);
  const [servingOverridesText, setServingOverridesText] = useState("");
  const [modelName, setModelName] = useState("");
  const [tool, setTool] = useState<BenchmarkTool>("vllm_serving");
  const [perfParams, setPerfParams] = useState(DEFAULT_PERF_PARAMS);
  const [accParams, setAccParams] = useState(DEFAULT_ACCURACY_PARAMS);
  const [extraParamsText, setExtraParamsText] = useState("");
  const [namespace, setNamespace] = useState("");
  const [image, setImage] = useState("");

  const kind = TOOL_TO_KIND[tool];

  // Deduplicated model_name list for the dropdown.
  const modelOptions = useMemo(() => {
    if (!models) return [] as string[];
    return Array.from(new Set(models.map((m) => m.model_name))).sort();
  }, [models]);

  // Only Ready serving deployments can be benchmarked directly.
  const readyDeployments = useMemo(
    () => (deployments ?? []).filter((d) => d.ready_replicas > 0),
    [deployments],
  );
  const selectedDeployment = readyDeployments.find((d) => d.id === deploymentId) ?? null;

  const buildNamedParams = (): Record<string, unknown> => {
    if (kind === "performance") {
      const params: Record<string, unknown> = {
        num_prompts: perfParams.num_prompts,
        concurrency: perfParams.concurrency,
        max_tokens: perfParams.max_tokens,
        temperature: perfParams.temperature,
        prompt: perfParams.prompt,
      };
      if (perfParams.request_rate !== "") {
        params.request_rate = Number(perfParams.request_rate);
      }
      if (perfParams.ignore_eos) {
        params.ignore_eos = true;
      }
      return params;
    }
    const tasks = accParams.tasks
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const params: Record<string, unknown> = {
      tasks,
      batch_size: accParams.batch_size,
      num_concurrent: accParams.num_concurrent,
    };
    if (accParams.num_fewshot !== "") {
      params.num_fewshot = Number(accParams.num_fewshot);
    }
    if (accParams.limit !== "") {
      params.limit = Number(accParams.limit);
    }
    return params;
  };

  const parseExtras = ():
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string } => {
    const text = extraParamsText.trim();
    if (!text) return { ok: true, value: {} };
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return { ok: false, error: t("errorExtrasNotObject") };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (e) {
      return {
        ok: false,
        error: t("errorExtrasJsonInvalid", {
          message: e instanceof Error ? e.message : String(e),
        }),
      };
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!deploymentId && !modelName.trim()) {
      toast.error(t("errorTargetRequired"));
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
      params: { ...buildNamedParams(), ...extras.value },
    };
    // Prefer a portal-managed serving deployment (hit directly); else a LiteLLM alias.
    if (deploymentId) {
      body.deployment_id = deploymentId;
      if (ephemeral) {
        body.ephemeral = true;
        const text = servingOverridesText.trim();
        if (text) {
          try {
            const parsed = JSON.parse(text);
            if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
              toast.error(t("errorExtrasNotObject"));
              return;
            }
            body.serving_overrides = parsed as Record<string, unknown>;
          } catch (err) {
            toast.error(
              t("errorExtrasJsonInvalid", {
                message: err instanceof Error ? err.message : String(err),
              }),
            );
            return;
          }
        }
      }
    } else {
      body.model_name = modelName.trim();
    }
    if (namespace.trim()) body.namespace = namespace.trim();
    if (image.trim()) body.image = image.trim();

    createMutation.mutate(body, {
      onSuccess: (run) => {
        toast.success(t("submitSuccess"));
        router.push(`/admin/benchmarks/${run.id}`);
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : t("submitFail")),
    });
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link
          href="/admin/benchmarks"
          className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToList")}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t("pageTitle")}</h1>
        <p className="text-muted-foreground mt-1">{t("pageDescription")}</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("target")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="deployment">{t("deploymentLabel")}</Label>
              <select
                id="deployment"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={deploymentId}
                onChange={(e) => setDeploymentId(e.target.value)}
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
              {selectedDeployment && (
                <p className="font-mono text-xs text-muted-foreground">
                  {selectedDeployment.model_path}
                </p>
              )}
            </div>

            {deploymentId && (
              <div className="space-y-2 rounded-md border border-dashed p-3">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 rounded border-input"
                    checked={ephemeral}
                    onChange={(e) => setEphemeral(e.target.checked)}
                  />
                  <span>
                    {t("ephemeralLabel")}
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                      {t("ephemeralHint")}
                    </span>
                  </span>
                </label>
                {ephemeral && (
                  <div className="space-y-1.5">
                    <Label htmlFor="serving_overrides">{t("servingOverridesLabel")}</Label>
                    <textarea
                      id="serving_overrides"
                      rows={3}
                      className="flex w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      placeholder='{"gpu_count": 2, "gpu_type": "NVIDIA-H100"}'
                      value={servingOverridesText}
                      onChange={(e) => setServingOverridesText(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">{t("servingOverridesHint")}</p>
                  </div>
                )}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="model_name">{t("modelLabel")}</Label>
              <select
                id="model_name"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                disabled={modelsLoading || !!deploymentId}
              >
                <option value="">{t("modelPlaceholder")}</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                {deploymentId ? t("modelHintDisabled") : t("modelHint")}
              </p>
            </div>

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
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("params")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {kind === "performance" ? (
              <PerfParamsFields
                params={perfParams}
                onChange={setPerfParams}
              />
            ) : (
              <AccuracyParamsFields
                params={accParams}
                onChange={setAccParams}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("extraParams")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Label htmlFor="extra_params">{t("extraParamsLabel")}</Label>
            <textarea
              id="extra_params"
              rows={5}
              spellCheck={false}
              placeholder={'{\n  "request_rate": 4,\n  "ignore_eos": true\n}'}
              className="flex min-h-[100px] w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              value={extraParamsText}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                setExtraParamsText(e.target.value)
              }
            />
            <p className="text-xs text-muted-foreground">{t("extraParamsHint")}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("advanced")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="namespace">{t("namespaceLabel")}</Label>
              <Input
                id="namespace"
                placeholder="default"
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t("namespaceHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="image">{t("imageLabel")}</Label>
              <Input
                id="image"
                placeholder="llmops-benchmark:latest"
                value={image}
                onChange={(e) => setImage(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t("imageHint")}</p>
            </div>
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-3">
          <Link href="/admin/benchmarks">
            <Button type="button" variant="outline">
              {tc("cancel")}
            </Button>
          </Link>
          <Button type="submit" disabled={createMutation.isPending}>
            {createMutation.isPending ? (
              <Loader2 className="size-4 mr-1 animate-spin" />
            ) : (
              <Play className="size-4 mr-1" />
            )}
            {t("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

function PerfParamsFields({
  params,
  onChange,
}: {
  params: typeof DEFAULT_PERF_PARAMS;
  onChange: (next: typeof DEFAULT_PERF_PARAMS) => void;
}) {
  const t = useTranslations("benchmarkForm");
  return (
    <>
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="num_prompts"
          label={t("numPromptsLabel")}
          hint={t("numPromptsHint")}
          value={params.num_prompts}
          onChange={(v) => onChange({ ...params, num_prompts: v })}
          min={1}
        />
        <NumberField
          id="concurrency"
          label={t("concurrencyLabel")}
          hint={t("concurrencyHint")}
          value={params.concurrency}
          onChange={(v) => onChange({ ...params, concurrency: v })}
          min={1}
        />
        <NumberField
          id="max_tokens"
          label={t("maxTokensLabel")}
          hint={t("maxTokensHint")}
          value={params.max_tokens}
          onChange={(v) => onChange({ ...params, max_tokens: v })}
          min={1}
        />
        <NumberField
          id="temperature"
          label={t("temperatureLabel")}
          hint={t("temperatureHint")}
          value={params.temperature}
          onChange={(v) => onChange({ ...params, temperature: v })}
          step="0.1"
          min={0}
        />
        <OptionalNumberField
          id="request_rate"
          label={t("requestRateLabel")}
          hint={t("requestRateHint")}
          value={params.request_rate}
          onChange={(v) => onChange({ ...params, request_rate: v })}
        />
        <div className="flex items-center gap-2 mt-6">
          <input
            id="ignore_eos"
            type="checkbox"
            className="size-4 rounded border-input"
            checked={params.ignore_eos}
            onChange={(e) => onChange({ ...params, ignore_eos: e.target.checked })}
          />
          <Label htmlFor="ignore_eos" className="cursor-pointer">
            {t("ignoreEosLabel")}
          </Label>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="prompt">{t("promptLabel")}</Label>
        <textarea
          id="prompt"
          rows={4}
          className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          value={params.prompt}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
            onChange({ ...params, prompt: e.target.value })
          }
        />
        <p className="text-xs text-muted-foreground">{t("promptHint")}</p>
      </div>
    </>
  );
}

function AccuracyParamsFields({
  params,
  onChange,
}: {
  params: typeof DEFAULT_ACCURACY_PARAMS;
  onChange: (next: typeof DEFAULT_ACCURACY_PARAMS) => void;
}) {
  const t = useTranslations("benchmarkForm");
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="tasks">{t("tasksLabel")}</Label>
        <Input
          id="tasks"
          placeholder="mmlu, hellaswag"
          value={params.tasks}
          onChange={(e) => onChange({ ...params, tasks: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">{t("tasksHint")}</p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <NumberField
          id="batch_size"
          label={t("batchSizeLabel")}
          hint={t("batchSizeHint")}
          value={params.batch_size}
          onChange={(v) => onChange({ ...params, batch_size: v })}
          min={1}
        />
        <NumberField
          id="num_concurrent"
          label={t("numConcurrentLabel")}
          hint={t("numConcurrentHint")}
          value={params.num_concurrent}
          onChange={(v) => onChange({ ...params, num_concurrent: v })}
          min={1}
        />
        <OptionalNumberField
          id="num_fewshot"
          label={t("numFewshotLabel")}
          hint={t("numFewshotHint")}
          value={params.num_fewshot}
          onChange={(v) => onChange({ ...params, num_fewshot: v })}
        />
        <OptionalNumberField
          id="limit"
          label={t("limitLabel")}
          hint={t("limitHint")}
          value={params.limit}
          onChange={(v) => onChange({ ...params, limit: v })}
        />
      </div>
    </>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  onChange,
  min,
  step,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        min={min}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

function OptionalNumberField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}
