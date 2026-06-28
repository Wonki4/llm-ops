"use client";

import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, Trash2, Server, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { useLocaleTag, parseServerDate } from "@/lib/locale";

import {
  useModelDeployment,
  useModelDeploymentEvents,
  useDeleteModelDeployment,
} from "@/hooks/use-api";
import type { ModelDeploymentEvent } from "@/types";
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
            <span className="text-sm text-muted-foreground tabular-nums">{dep.ready_replicas}/{dep.replicas} ready</span>
          </div>
          <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={handleDelete} disabled={deleteMut.isPending}>
            {deleteMut.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}{t("deleteButton")}
          </Button>
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
            <Field label={t("colReplicas")} mono>{dep.ready_replicas}/{dep.replicas}</Field>
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
            <Field label={t("modelPath")} mono>{dep.model_path}</Field>
            <Field label={t("colImage")} mono>{dep.image}</Field>
            <Field label={t("colNamespace")} mono>{dep.namespace}</Field>
            <Field label={t("cluster")} mono>{dep.cluster_id || t("portalDefault")}</Field>
            <Field label={t("colGpu")} mono>{dep.gpu_count} × {dep.gpu_resource_key}</Field>
            <Field label="CPU" mono>{dep.cpu_request || dep.cpu_limit ? `${dep.cpu_request ?? "-"} / ${dep.cpu_limit ?? "-"}` : "-"}</Field>
            <Field label={t("memory")} mono>{dep.memory_request || dep.memory_limit ? `${dep.memory_request ?? "-"} / ${dep.memory_limit ?? "-"}` : "-"}</Field>
            <Field label={t("ingressHost")} mono>{dep.ingress_host}</Field>
            <Field label={t("createdBy")}>{dep.created_by ?? "-"}</Field>
            <Field label={t("createdAt")}>{fmt(dep.created_at)}</Field>
          </div>
          {dep.vllm_extra_args && dep.vllm_extra_args.length > 0 && (
            <div className="mt-4 space-y-1">
              <div className="text-xs text-muted-foreground">{t("extraArgs")}</div>
              <code className="block rounded-md border bg-muted/40 p-2 text-xs font-mono">{dep.vllm_extra_args.join(" ")}</code>
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
