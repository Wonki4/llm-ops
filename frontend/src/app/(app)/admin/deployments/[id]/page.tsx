"use client";

import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, ScrollText, Trash2, Server, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useLocaleTag, parseServerDate } from "@/lib/locale";
import { engineArgsToFlags } from "@/lib/serving-engines";
import { PD_ROLES, PD_VLLM_PORT, effectiveRole, roleLaunchArgs } from "@/lib/pd-serving";

import {
  useModelDeployment,
  useModelDeploymentEvents,
  useDeleteModelDeployment,
} from "@/hooks/use-api";
import type { ModelDeploymentEvent, ProbeSpec } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "Ready" ? "default" : status === "Failed" || status === "Missing" ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function Field({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-sm ${mono ? "font-mono break-all" : ""}`}>{children}</div>
    </div>
  );
}

/** `GET /health · delay 60s · every 10s · timeout 5s · fail ×30` with portal defaults filled in. */
function probeSummary(spec: ProbeSpec | null, defaultPath: string, d: [number, number, number, number]): string {
  const p = spec ?? {};
  return [
    `GET ${p.path ?? defaultPath}`,
    `delay ${p.initial_delay_seconds ?? d[0]}s`,
    `every ${p.period_seconds ?? d[1]}s`,
    `timeout ${p.timeout_seconds ?? d[2]}s`,
    `fail ×${p.failure_threshold ?? d[3]}`,
  ].join(" · ");
}

function sevColor(sev: string): string {
  if (sev === "error") return "bg-destructive";
  if (sev === "warning") return "bg-amber-500";
  return "bg-muted-foreground/50";
}

export default function DeploymentDetailPage() {
  const t = useTranslations("adminDeployments");
  const localeTag = useLocaleTag();
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);

  const { data: dep, isLoading } = useModelDeployment(id);
  const { data: events } = useModelDeploymentEvents(id);
  const deleteMut = useDeleteModelDeployment();

  const fmt = (d: string | null) => (d ? parseServerDate(d).toLocaleString(localeTag) : "-");

  const handleDelete = () => {
    if (!dep) return;
    if (!window.confirm(t("deleteConfirm", { name: dep.model_name }))) return;
    deleteMut.mutate(dep.id, {
      onSuccess: () => { toast.success(t("deleteSuccess")); router.push("/admin/deployments"); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deleteFailed")),
    });
  };

  if (isLoading) {
    return <div className="flex justify-center py-16"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>;
  }
  if (!dep) {
    return (
      <div className="space-y-4">
        <Link href="/admin/deployments" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{t("backToList")}
        </Link>
        <p className="text-sm text-muted-foreground">{t("notFound")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-3">
        <Link href="/admin/deployments" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />{t("backToList")}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Server className="size-5" />
            <h1 className="text-2xl font-bold">{dep.model_name}</h1>
            <StatusBadge status={dep.status} />
            <Badge variant="secondary" className="font-mono text-[10px] uppercase">{dep.engine}</Badge>
            {dep.serving_mode === "pd" && <Badge variant="outline" className="text-[10px]" data-testid="pd-badge">{t("pdBadge")}</Badge>}
            {dep.gpu_type && <Badge variant="outline" className="font-mono text-[10px]">{dep.gpu_type}</Badge>}
            <span className="text-sm text-muted-foreground tabular-nums">
              {dep.serving_mode === "pd" && dep.pd_summary ? dep.pd_summary : `${dep.ready_replicas}/${dep.replicas} ready`}
            </span>
            {dep.recipe_id && (
              <Badge asChild variant="outline" className="gap-1">
                <Link href={`/admin/recipes/${dep.recipe_id}`} title={t("recipeLinkedHint")}>
                  <ScrollText className="size-3" />{dep.recipe_name ?? t("recipeLabel")}
                </Link>
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href={`/admin/recipes/new?from=deployment&id=${dep.id}`}>
                <ScrollText className="size-3.5" />{t("saveAsRecipe")}
              </Link>
            </Button>
            <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={handleDelete} disabled={deleteMut.isPending}>
              {deleteMut.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}{t("deleteButton")}
            </Button>
          </div>
        </div>
        {dep.status_message && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle className="size-4 shrink-0 text-amber-500 mt-0.5" />
            <span className="text-muted-foreground">{dep.status_message}</span>
          </div>
        )}
      </div>

      {/* Runtime status */}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("statusSection")}</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label={t("colStatus")}><StatusBadge status={dep.status} /></Field>
            <Field label={t("colReplicas")} mono>
              {dep.serving_mode === "pd" && dep.pd_summary ? dep.pd_summary : `${dep.ready_replicas}/${dep.replicas}`}
            </Field>
            <Field label={t("litellmRegistered")}>{dep.litellm_model_id ? `✅ ${dep.litellm_model_id}` : t("notRegistered")}</Field>
            <Field label={t("serviceIp")} mono>{dep.service_cluster_ip || "-"}</Field>
            <Field label={t("lastSynced")}>{fmt(dep.last_synced_at)}</Field>
          </div>
        </CardContent>
      </Card>

      {/* Config */}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("configSection")}</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {dep.serving_mode !== "pd" && (
              <>
                <Field label={t("modelPath")} mono>{dep.model_path}</Field>
                <Field label={t("colImage")} mono>{dep.image}</Field>
              </>
            )}
            <Field label={t("colNamespace")} mono>{dep.namespace}</Field>
            <Field label={t("cluster")} mono>{dep.cluster_id || t("portalDefault")}</Field>
            {dep.serving_mode !== "pd" && (
              <>
                <Field label={t("colGpu")} mono>{dep.gpu_count} × {dep.gpu_resource_key}</Field>
                <Field label={t("gpuType")} mono>
                  {dep.gpu_type ?? "-"}
                  {dep.node_selector && Object.keys(dep.node_selector).length > 0 && (
                    <span className="ml-2 text-xs text-muted-foreground">{Object.entries(dep.node_selector).map(([k, v]) => `${k}=${v}`).join(", ")}</span>
                  )}
                </Field>
                <Field label="CPU" mono>{dep.cpu_request || dep.cpu_limit ? `${dep.cpu_request ?? "-"} / ${dep.cpu_limit ?? "-"}` : "-"}</Field>
                <Field label={t("memory")} mono>{dep.memory_request || dep.memory_limit ? `${dep.memory_request ?? "-"} / ${dep.memory_limit ?? "-"}` : "-"}</Field>
              </>
            )}
            <Field label={t("ingressHost")} mono>{dep.ingress_host}</Field>
            <Field label={t("createdBy")}>{dep.created_by ?? "-"}</Field>
            <Field label={t("createdAt")}>{fmt(dep.created_at)}</Field>
          </div>
          {dep.serving_mode !== "pd" && (
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label={t("probeReadiness")} mono>{probeSummary(dep.probes?.readiness ?? null, "/health", [60, 10, 5, 30])}</Field>
            <Field label={t("probeLiveness")} mono>
              {dep.probes?.liveness == null ? t("probeOff") : probeSummary(dep.probes.liveness, "/health", [120, 30, 5, 3])}
            </Field>
            <Field label={t("probeStartup")} mono>
              {dep.probes?.startup == null ? t("probeOff") : probeSummary(dep.probes.startup, "/health", [15, 30, 5, 120])}
            </Field>
          </div>
          )}
          {(() => {
            if (dep.serving_mode === "pd") return null;
            const flags = [...engineArgsToFlags(dep.engine_args), ...(dep.vllm_extra_args ?? [])];
            return flags.length > 0 ? (
              <div className="mt-4 space-y-1">
                <div className="text-xs text-muted-foreground">{t("extraArgs")}</div>
                <code className="block rounded-md border bg-muted/40 p-2 text-xs font-mono">{flags.join(" ")}</code>
              </div>
            ) : null;
          })()}
        </CardContent>
      </Card>

      {/* Prefill / decode pools + the router they register through */}
      {dep.serving_mode === "pd" && (
        <Card data-testid="pd-section">
          <CardHeader><CardTitle className="text-base">{t("pdSection")}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {PD_ROLES.map((role) => {
                const st = dep.pd_status?.[role];
                const eff = effectiveRole(dep, dep.pd_config?.[role]);
                const args = roleLaunchArgs(dep, role);
                const cpu = eff.cpu_request || eff.cpu_limit ? `CPU ${eff.cpu_request ?? "-"} / ${eff.cpu_limit ?? "-"}` : null;
                const mem = eff.memory_request || eff.memory_limit ? `Mem ${eff.memory_request ?? "-"} / ${eff.memory_limit ?? "-"}` : null;
                return (
                  <div key={role} className="space-y-3 rounded-md border p-3" data-testid={`pd-role-${role}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{t(role === "prefill" ? "pdPrefill" : "pdDecode")}</span>
                      {st?.status && <StatusBadge status={st.status} />}
                      <span className="text-sm text-muted-foreground tabular-nums">
                        {st ? `${st.ready}/${st.desired}` : `–/${eff.replicas ?? 1}`}
                      </span>
                      <span className="ml-auto font-mono text-[11px] text-muted-foreground">{t("pdPort")} {PD_VLLM_PORT[role]}</span>
                    </div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <Field label={t("colImage")} mono>{eff.image}</Field>
                      <Field label={t("modelPath")} mono>{eff.model_path}</Field>
                      <Field label={t("pdResources")} mono>
                        {eff.gpu_count ?? 0} × {eff.gpu_resource_key}
                        {eff.gpu_type ? ` · ${eff.gpu_type}` : ""}{cpu ? ` · ${cpu}` : ""}{mem ? ` · ${mem}` : ""}
                      </Field>
                      <Field label={t("pdStorage")} mono>{eff.pvc_name ? `${eff.pvc_name} → ${eff.pvc_mount_path ?? "/models"}` : "-"}</Field>
                      <Field label={t("probeReadiness")} mono>{probeSummary(eff.probes?.readiness ?? null, "/health", [60, 10, 5, 30])}</Field>
                      <Field label={t("probeStartup")} mono>{probeSummary(eff.probes?.startup ?? null, "/health", [15, 30, 5, 120])}</Field>
                      <Field label={t("pdPlacement")} mono>
                        {eff.node_selector && Object.keys(eff.node_selector).length > 0
                          ? Object.entries(eff.node_selector).map(([k, v]) => `${k}=${v}`).join(", ")
                          : "-"}
                        {eff.runtime?.shm_size_gi ? ` · shm ${eff.runtime.shm_size_gi}Gi` : ""}
                        {eff.runtime?.host_ipc ? " · hostIPC" : ""}{eff.runtime?.privileged ? " · privileged" : ""}
                      </Field>
                    </div>
                    {st?.message && <p className="text-xs text-muted-foreground">{st.message}</p>}
                    <div className="space-y-1">
                      <div className="text-xs text-muted-foreground">{t("pdLaunchArgs")}</div>
                      <code className="block whitespace-pre-wrap break-all rounded-md border bg-muted/40 p-2 text-xs font-mono">{args.join(" ")}</code>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="rounded-md border p-3" data-testid="pd-router">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="outline">llm-d</Badge>
                <span className="font-medium">{t("pdRouter")}</span>
                {dep.router_stack ? (
                  <>
                    <Link href={`/admin/llmd/${dep.router_stack.id}`} className="hover:underline">{dep.router_stack.name}</Link>
                    {dep.router_stack.health_status && (
                      <Badge variant={dep.router_stack.health_status === "Healthy" ? "default" : "secondary"}>{dep.router_stack.health_status}</Badge>
                    )}
                    {dep.router_stack.sync_status && <Badge variant="secondary">{dep.router_stack.sync_status}</Badge>}
                    <span className="font-mono text-xs text-muted-foreground">{t("pdRouterHost")} {dep.router_stack.ingress_host}</span>
                    <span className="text-xs text-muted-foreground">
                      {dep.router_stack.created_by_deployment ? t("pdRouterCreated") : t("pdRouterLinked")}
                    </span>
                  </>
                ) : (
                  <span className="text-xs text-amber-600 dark:text-amber-400">{t("pdRouterNone")}</span>
                )}
              </div>
              {(() => {
                const r = dep.pd_config?.router ?? {};
                const image = r.epp_registry || r.epp_repository || r.epp_tag
                  ? `${r.epp_registry ?? "…"}/${r.epp_repository ?? "…"}:${r.epp_tag ?? "…"}`
                  : null;
                const parts = [
                  image ? `${t("pdEpp")} ${image}` : null,
                  r.epp_replicas ? `${t("pdEpp")} ×${r.epp_replicas}` : null,
                  r.ingress_class ? `${t("pdIngressClass")} ${r.ingress_class}` : null,
                  `peakPrefillThroughput ${r.peak_prefill_throughput ?? 33821}`,
                  `maxPrefixTokensToMatch ${r.prefix_tokens_to_match ?? 131072}`,
                ].filter(Boolean);
                return <div className="mt-2 font-mono text-[11px] text-muted-foreground">{parts.join(" · ")}</div>;
              })()}
              {dep.pd_status?.router && dep.pd_status.router.ready === false && dep.pd_status.router.reason && (
                <p className="mt-2 text-xs text-muted-foreground">{t("pdRouterWaiting", { reason: dep.pd_status.router.reason })}</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* llm-d routers that select this deployment's pods */}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("llmdSection")}</CardTitle></CardHeader>
        <CardContent>
          {!dep.llmd_stacks || dep.llmd_stacks.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("llmdNone")}</p>
          ) : (
            <div className="space-y-2">
              {dep.llmd_stacks.map((s) => (
                <Link key={s.id} href={`/admin/llmd/${s.id}`} className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm hover:bg-muted/50">
                  <Badge variant="outline">llm-d</Badge>
                  <span className="font-medium">{s.name}</span>
                  {s.namespace && <span className="font-mono text-xs text-muted-foreground">{s.namespace}</span>}
                  {s.health_status && (
                    <Badge variant={s.health_status === "Healthy" ? "default" : "secondary"}>{s.health_status}</Badge>
                  )}
                  {s.sync_status && <Badge variant="secondary">{s.sync_status}</Badge>}
                  <code className="ml-auto text-[11px] text-muted-foreground">
                    {Object.entries(s.selector).map(([k, v]) => `${k}=${v}`).join(",")}
                  </code>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Events timeline */}
      <Card>
        <CardHeader><CardTitle className="text-base">{t("eventsSection")}</CardTitle></CardHeader>
        <CardContent>
          {!events ? (
            <div className="flex justify-center py-6"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
          ) : events.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noEvents")}</p>
          ) : (
            <ol className="space-y-3">
              {events.map((e: ModelDeploymentEvent) => (
                <li key={e.id} className="flex gap-3">
                  <span className={`mt-1.5 size-2 shrink-0 rounded-full ${sevColor(e.severity)}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-medium">{e.event_type}</span>
                      {e.from_status && e.to_status && (
                        <span className="text-xs text-muted-foreground font-mono">{e.from_status} → {e.to_status}</span>
                      )}
                      <span className="ml-auto text-xs text-muted-foreground">{fmt(e.created_at)}</span>
                    </div>
                    {e.message && <p className="text-xs text-muted-foreground mt-0.5 break-words">{e.message}</p>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
