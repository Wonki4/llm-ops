"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { useModelSummary, type ModelSummary } from "@/hooks/use-api";
import { ModelIcon } from "@/components/model-icon";
import { ModalityValue } from "@/components/model-modality";
import { ModelStatusBadge } from "@/components/model-status-badge";
import type { ModelStatus } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Render a per-token cost as price per 1M tokens. */
function pricePerM(costPerToken: unknown): string {
  const c = num(costPerToken);
  if (c === null) return "—";
  return `$${(c * 1_000_000).toFixed(2)} / 1M`;
}

function fmt(v: unknown, digits = 2, suffix = ""): string {
  const n = num(v);
  return n === null ? "—" : `${n.toFixed(digits)}${suffix}`;
}

function get(obj: unknown, key: string): unknown {
  return obj && typeof obj === "object" ? (obj as Record<string, unknown>)[key] : undefined;
}

function StatCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs font-medium text-muted-foreground">{label}</div>
        <div className="mt-1 flex min-h-8 items-center text-xl font-semibold tabular-nums">{value}</div>
        <div className="mt-0.5 min-h-4 text-xs text-muted-foreground">{sub}</div>
      </CardContent>
    </Card>
  );
}

function EmptyTab({ text }: { text: string }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{text}</p>;
}

function MeasuredAt({ tool, at }: { tool?: string; at?: string | null }) {
  if (!at) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {tool ? `${tool} · ` : ""}측정 {new Date(at).toLocaleString()}
    </p>
  );
}

function KVTable({ rows }: { rows: Array<[string, string]> }) {
  return (
    <Table>
      <TableBody>
        {rows.map(([k, v]) => (
          <TableRow key={k}>
            <TableCell className="w-1/2 text-muted-foreground">{k}</TableCell>
            <TableCell className="font-medium tabular-nums">{v}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function PerfDetailTab({ perf }: { perf: ModelSummary["performance"] }) {
  const raw = perf?.result ?? null;
  if (!raw) return <EmptyTab text="아직 성능 벤치마크 결과가 없습니다." />;
  // Stored result wraps the runner output under `metrics`; `vllm bench serve`
  // emits a flat schema (throughput in req|tok/s, latencies in ms).
  const r =
    raw && typeof raw === "object" && "metrics" in (raw as Record<string, unknown>)
      ? (raw as Record<string, unknown>).metrics
      : raw;
  const rows: Array<[string, string]> = [
    ["Throughput (output)", fmt(get(r, "output_throughput"), 1, " tok/s")],
    ["Throughput (requests)", fmt(get(r, "request_throughput"), 2, " req/s")],
    ["Total token throughput", fmt(get(r, "total_token_throughput"), 1, " tok/s")],
    ["Completed", fmt(get(r, "completed"), 0)],
    ["Total output tokens", fmt(get(r, "total_output_tokens"), 0)],
    ["Duration", fmt(get(r, "duration"), 2, " s")],
    ["TTFT mean", fmt(get(r, "mean_ttft_ms"), 1, " ms")],
    ["TTFT p99", fmt(get(r, "p99_ttft_ms"), 1, " ms")],
    ["TPOT mean", fmt(get(r, "mean_tpot_ms"), 2, " ms")],
    ["TPOT p99", fmt(get(r, "p99_tpot_ms"), 2, " ms")],
    ["ITL mean", fmt(get(r, "mean_itl_ms"), 2, " ms")],
    ["E2E mean", fmt(get(r, "mean_e2el_ms"), 1, " ms")],
  ];
  return (
    <div className="space-y-3">
      <MeasuredAt tool={perf?.tool} at={perf?.finished_at} />
      <KVTable rows={rows} />
    </div>
  );
}

function AccuracyDetailTab({ acc }: { acc: ModelSummary["accuracy"] }) {
  const r = acc?.result ?? null;
  const entries = r && typeof r === "object" ? Object.entries(r as Record<string, unknown>) : [];
  if (entries.length === 0) return <EmptyTab text="아직 정확도 결과가 없습니다." />;
  const rows: Array<[string, string]> = entries.map(([k, v]) => [
    k,
    num(v) !== null ? fmt(v, 4) : typeof v === "object" ? JSON.stringify(v) : String(v),
  ]);
  return (
    <div className="space-y-3">
      <MeasuredAt tool={acc?.tool} at={acc?.finished_at} />
      <KVTable rows={rows} />
    </div>
  );
}

function PricingDetailTab({ summary }: { summary: ModelSummary }) {
  const info = get(summary.litellm_info, "model_info") ?? summary.litellm_info;
  const baseIn = summary.catalog?.default_input_cost_per_token ?? get(info, "input_cost_per_token");
  const baseOut = summary.catalog?.default_output_cost_per_token ?? get(info, "output_cost_per_token");
  const baseCacheRead =
    summary.catalog?.default_cache_read_cost_per_token ?? get(info, "cache_read_input_token_cost");
  return (
    <div className="space-y-4">
      <KVTable
        rows={[
          ["기본 Input", pricePerM(baseIn)],
          ["기본 Output", pricePerM(baseOut)],
          ["기본 Cache Read", pricePerM(baseCacheRead)],
        ]}
      />
      {summary.cost_schedule.length > 0 ? (
        <div className="space-y-1.5">
          <span className="text-xs font-medium text-muted-foreground">시간대 요금 (UTC)</span>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시간</TableHead>
                <TableHead>요일</TableHead>
                <TableHead>Input</TableHead>
                <TableHead>Output</TableHead>
                <TableHead>Cache Read</TableHead>
                <TableHead>우선순위</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.cost_schedule.map((s, i) => (
                <TableRow key={i}>
                  <TableCell>
                    {s.hour_start_local}:00–{s.hour_end_local}:00
                  </TableCell>
                  <TableCell>{s.days_of_week.join(", ")}</TableCell>
                  <TableCell className="tabular-nums">{pricePerM(s.input_cost_per_token)}</TableCell>
                  <TableCell className="tabular-nums">{pricePerM(s.output_cost_per_token)}</TableCell>
                  <TableCell className="tabular-nums">{pricePerM(s.cache_read_cost_per_token)}</TableCell>
                  <TableCell className="tabular-nums">{s.priority}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">시간대 요금 규칙이 없습니다.</p>
      )}
    </div>
  );
}

function InfoDetailTab({ litellm }: { litellm: Record<string, unknown> | null }) {
  const info = get(litellm, "model_info") ?? litellm;
  const rows: Array<[string, string]> = [];
  const provider = get(get(litellm, "litellm_params"), "custom_llm_provider");
  if (provider) rows.push(["Provider", String(provider)]);
  const wanted = [
    "mode",
    "max_input_tokens",
    "max_output_tokens",
    "max_tokens",
    "supports_function_calling",
    "supports_vision",
    "supports_parallel_function_calling",
  ];
  for (const k of wanted) {
    const v = get(info, k);
    if (v !== undefined && v !== null) {
      rows.push([k, typeof v === "number" ? v.toLocaleString() : String(v)]);
    }
  }
  if (rows.length === 0) return <EmptyTab text="모델 메타데이터가 없습니다." />;
  return <KVTable rows={rows} />;
}

export default function ModelDetailPage() {
  const params = useParams();
  const raw = params.modelName;
  const modelName = Array.isArray(raw) ? raw.map(decodeURIComponent).join("/") : decodeURIComponent(raw ?? "");

  const { data: summary, isLoading, error } = useModelSummary(modelName || null);

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="space-y-4 p-6">
        <p className="text-sm text-muted-foreground">모델 정보를 불러오지 못했습니다.</p>
        <Button asChild variant="outline" size="sm">
          <Link href="/models/dashboard">← 모델 대시보드</Link>
        </Button>
      </div>
    );
  }

  const provider = String(get(get(summary.litellm_info, "litellm_params"), "custom_llm_provider") ?? "");

  const info = get(summary.litellm_info, "model_info") ?? summary.litellm_info;
  const iconProvider = String(get(info, "litellm_provider") ?? provider);
  const baseIn = summary.catalog?.default_input_cost_per_token ?? get(info, "input_cost_per_token");
  const baseOut = summary.catalog?.default_output_cost_per_token ?? get(info, "output_cost_per_token");
  const ctx = num(get(info, "max_input_tokens")) ?? num(get(info, "max_tokens"));
  const maxOut = num(get(info, "max_output_tokens"));

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="space-y-2">
        <Button asChild variant="ghost" size="sm" className="-ml-2 h-7 text-muted-foreground">
          <Link href="/models/dashboard">← 모델 대시보드</Link>
        </Button>
        <div className="flex items-center gap-3">
          <ModelIcon iconUrl={summary.catalog?.icon_url} provider={iconProvider} modelName={summary.model_name} size={32} />
          <h1 className="text-2xl font-bold">{summary.catalog?.display_name || summary.model_name}</h1>
          {summary.catalog?.status && <ModelStatusBadge status={summary.catalog.status as ModelStatus} />}
        </div>
        <p className="text-sm text-muted-foreground">
          {summary.model_name}
          {provider ? ` · ${provider}` : ""}
        </p>
        {summary.catalog?.description && (
          <p className="text-sm text-muted-foreground">{summary.catalog.description}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="모달리티" value={<ModalityValue info={info} />} />
        <StatCard label="Input price" value={pricePerM(baseIn)} />
        <StatCard label="Output price" value={pricePerM(baseOut)} />
        <StatCard
          label="Context"
          value={ctx !== null ? `${ctx.toLocaleString()} tok` : "—"}
          sub={maxOut !== null ? `Max output ${maxOut.toLocaleString()} tok` : undefined}
        />
      </div>

      <Tabs defaultValue="info" className="w-full">
        <TabsList variant="line" className="w-full justify-start gap-6 rounded-none border-b">
          <TabsTrigger value="info" className="flex-none px-1">모델 정보</TabsTrigger>
          <TabsTrigger value="pricing" className="flex-none px-1">가격 상세</TabsTrigger>
          <TabsTrigger value="performance" className="flex-none px-1">성능 상세</TabsTrigger>
          <TabsTrigger value="accuracy" className="flex-none px-1">정확도</TabsTrigger>
        </TabsList>
        <TabsContent value="performance" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <PerfDetailTab perf={summary.performance} />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="accuracy" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <AccuracyDetailTab acc={summary.accuracy} />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="pricing" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <PricingDetailTab summary={summary} />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="info" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <InfoDetailTab litellm={summary.litellm_info} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
