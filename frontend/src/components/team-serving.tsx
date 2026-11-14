"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMe } from "@/hooks/use-api";
import { scopeKey, teamScopeParams, useTeamScopes, type TeamScope } from "@/hooks/use-serving-access";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { OperatorDeployButton, OperatorDeploymentActions } from "@/components/team-deployment-actions";

export function ServingRoleView({ children, member }: { children: ReactNode; member: ReactNode }) {
  const me = useMe();
  if (me.isError) return <p role="alert">{me.error.message}</p>;
  if (!me.data) return <div aria-busy="true" className="h-32 animate-pulse rounded-lg bg-muted" />;
  return me.data.role === "super_user" ? children : member;
}

export function TeamScopeView({ children }: { children: (scope: TeamScope) => ReactNode }) {
  const t = useTranslations("teamServing");
  const scopes = useTeamScopes();
  const [choice, setChoice] = useState("");
  if (scopes.isError) return <p role="alert">{scopes.error.message}</p>;
  if (!scopes.data) return <div aria-busy="true" className="h-32 animate-pulse rounded-lg bg-muted" />;
  const scope = scopes.data.find(s => scopeKey(s) === choice) ?? scopes.data[0];
  if (!scope) return <p>{t("noScopes")}</p>;
  return <div className="space-y-4">
    <label className="flex flex-wrap items-center gap-3 text-sm">{t("scope")}
      <select className="rounded-md border bg-background p-2" value={scopeKey(scope)} onChange={e => setChoice(e.target.value)}>
        {scopes.data.map(s => <option key={scopeKey(s)} value={scopeKey(s)}>{s.cluster_name ?? t("portal")} / {s.namespace}</option>)}
      </select>
      <Badge variant="secondary">{t(scope.role)}</Badge>
    </label>
    {children(scope)}
  </div>;
}

interface Deployment {
  id: string; model_name: string; litellm_model_name: string | null; status: string;
  replicas: number; ready_replicas: number; pool_status: string | null; namespace: string;
  engine: string; serving_mode: string;
}

interface DeploymentDetail extends Deployment {
  cluster_id: string | null;
  image: string;
  gpu_type: string | null;
  gpu_count: number;
  recipe_id: string | null;
  ingress_host: string;
  prefill_replicas: number | null;
  decode_replicas: number | null;
  events: { created_at: string; event_type: string; severity: string; from_status: string | null; to_status: string | null }[];
}

function readiness(d: Deployment) {
  return d.pool_status ?? (d.serving_mode === "pd" ? "—" : `${d.ready_replicas}/${d.replicas}`);
}

function TeamDeployments({ scope }: { scope: TeamScope }) {
  const me = useMe();
  const query = useQuery({
    queryKey: ["team-deployments", me.data?.user_id, scopeKey(scope)],
    queryFn: () => apiFetch<{deployments: Deployment[]}>(`/api/serving/deployments?${teamScopeParams(scope)}`),
    refetchInterval: 15000,
  });
  if (query.isError) return <p role="alert">{query.error.message}</p>;
  if (!query.data) return <div aria-busy="true" className="h-16 animate-pulse bg-muted" />;
  return <DeploymentTable deployments={query.data.deployments} />;
}

function DeploymentTable({ deployments }: { deployments: Deployment[] }) {
  const t = useTranslations("teamServing");
  if (!deployments.length) return <p className="text-sm text-muted-foreground">{t("empty")}</p>;
  return <Table>
    <TableHeader><TableRow>{["name","alias","status","ready"].map(h => <TableHead key={h}>{t(h)}</TableHead>)}</TableRow></TableHeader>
    <TableBody>{deployments.map(d => <TableRow key={d.id}>
      <TableCell><Link className="font-medium underline" href={`/admin/deployments/${d.id}`}>{d.model_name}</Link></TableCell>
      <TableCell>{d.litellm_model_name ?? d.model_name}</TableCell><TableCell>{d.status}</TableCell><TableCell>{readiness(d)}</TableCell>
    </TableRow>)}</TableBody>
  </Table>;
}

export function TeamServingInventory({ teamId }: { teamId: string }) {
  const t = useTranslations("teamServing");
  const me = useMe();
  const inventory = useQuery({
    queryKey: ["team-deployments", "inventory", teamId, me.data?.user_id],
    queryFn: () => apiFetch<{ scopes: (TeamScope & { deployments: Deployment[] })[] }>(
      `/api/teams/${encodeURIComponent(teamId)}/serving-deployments`
    ),
    enabled: !!me.data,
    staleTime: 0,
    refetchInterval: 15000,
  });
  if (me.isError || inventory.isError) return <p role="alert">{me.error?.message ?? inventory.error?.message}</p>;
  if (!inventory.data) return <div aria-busy="true" className="h-24 animate-pulse rounded-lg bg-muted" />;
  return <section className="space-y-4">
    <h2 className="text-lg font-semibold">{t("inventoryTitle")}</h2>
    {inventory.data.scopes.length === 0 && <p className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">{t("teamEmpty")}</p>}
    {inventory.data.scopes.map(scope => <section key={scopeKey(scope)} className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex flex-wrap items-center gap-2 font-medium">
          {scope.cluster_name ?? t("portal")} / {scope.namespace} <Badge variant="secondary">{t(scope.role)}</Badge>
        </h3>
        {scope.role === "operator" && <OperatorDeployButton scope={scope} />}
      </div>
      <DeploymentTable deployments={scope.deployments} />
    </section>)}
  </section>;
}

export function TeamServingHome() {
  const t = useTranslations("teamServing");
  return <div className="space-y-5">
    <div className="flex items-center justify-between gap-4"><h1 className="text-2xl font-bold">{t("title")}</h1>
      <Link className="text-sm underline" href="/admin/serving/topology">{t("graph")}</Link></div>
    <TeamScopeView>{scope => <div key={scopeKey(scope)} className="space-y-4">
      {scope.role === "operator" && <OperatorDeployButton scope={scope} />}
      <TeamDeployments scope={scope} />
    </div>}</TeamScopeView>
  </div>;
}

export function TeamDeploymentDetail({ id }: { id: string }) {
  const t = useTranslations("teamServing");
  const me = useMe();
  const detail = useQuery({
    queryKey: ["team-deployment", me.data?.user_id, id],
    queryFn: () => apiFetch<DeploymentDetail>(`/api/serving/deployments/${id}`),
    staleTime: 0,
    refetchInterval: 15000,
  });
  const scopes = useTeamScopes();
  if (detail.isError || scopes.isError) return <p role="alert">{detail.error?.message ?? scopes.error?.message}</p>;
  const d = detail.data;
  if (!d || !scopes.data) return <div aria-busy="true" className="h-24 animate-pulse bg-muted" />;
  const scope = scopes.data.find(s => s.cluster_id === d.cluster_id && s.namespace === d.namespace);
  if (!scope) return <p>{t("noScopes")}</p>;
  return <div className="space-y-4"><Link className="text-sm underline" href="/admin/serving">{t("title")}</Link>
    <h1 className="text-2xl font-bold">{d.model_name}</h1>
    <dl className="grid max-w-2xl grid-cols-[auto_1fr] gap-x-8 gap-y-3 rounded-lg border bg-card p-5">
      {Object.entries({scope: (scope.cluster_name ?? t("portal")) + " / " + scope.namespace, alias: d.litellm_model_name ?? d.model_name,
        status: d.status, ready: readiness(d), engine: d.engine, mode: d.serving_mode, image: d.image,
        gpu: [d.gpu_count, d.gpu_type].filter(v => v != null).join(" × ")}).map(([k,v]) =>
        <div key={k} className="contents"><dt className="text-muted-foreground">{t(k)}</dt><dd className="break-all">{v}</dd></div>)}
      <dt className="text-muted-foreground">Ingress</dt><dd className="break-all">{d.ingress_host}</dd>
    </dl>
    {scope.role === "operator" && <OperatorDeploymentActions key={d.id} deployment={d} scope={scope} />}
    {!!d.events.length && <section className="max-w-2xl space-y-2"><h2 className="font-semibold">{t("events")}</h2>
      <ul className="divide-y">{d.events.map((e, i) => <li key={i} className="flex flex-wrap gap-x-3 py-2 text-sm">
        <time dateTime={e.created_at} className="text-muted-foreground">{new Date(e.created_at).toLocaleString()}</time>
        <span>{e.event_type}</span><span>{[e.from_status, e.to_status].filter(Boolean).join(" → ")}</span>
      </li>)}</ul></section>}
  </div>;
}
