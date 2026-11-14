"use client";

import { Suspense, useDeferredValue, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Box, Boxes, ChevronRight, Cloud, Layers3, Network, RefreshCw, Route, Search, Server, Waypoints } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { useK8sClusterSettings, useMe } from "@/hooks/use-api";
import { scopeKey, teamScopeParams, type TeamScope } from "@/hooks/use-serving-access";
import { ServingRoleView, TeamScopeView } from "@/components/team-serving";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ServingScopeFilter, useServingScope } from "@/components/serving-scope-filter";
import { inScope, isScoped, scopeQuery, type ScopedItem, type ServingScope } from "@/lib/serving-scope";

type Kind = "model" | "registration" | "ingress" | "service" | "router" | "pod" | "workload";
type Origin = "cluster" | "cloud" | "unknown";
interface Node {
  id: string;
  kind: Kind;
  label: string;
  detail: string | null;
  scope: string | null;
  status: string | null;
  ref: string | null;
  controller?: string | null;
  upstream_model?: string | null;
  deployment?: string | null;
  origin?: Origin;
  cloud_provider?: string;
  locations?: ScopedItem[];
}
interface ResourceGraph {
  nodes: Node[];
  edges: [string, string][];
  ownership_edges?: [string, string][];
}
interface Entry {
  cluster: string;
  ns: string;
  scope: string;
  kind: "ingress" | "service";
  root_kind: "ingress" | "service" | "router";
  name: string;
  registrations: string[];
}
interface Graph extends ResourceGraph {
  entries: Entry[];
  partial_coverage: boolean;
  litellm_available: boolean;
  unavailable_scopes: string[];
  scopes: ScopedItem[];
}

const stages: Kind[][] = [["model"], ["registration"], ["ingress"], ["router", "service"], ["pod"], ["workload"]];
const titles = ["models", "registration", "ingress", "targets", "pods", "owningDeployment"] as const;
const icons = { model: Boxes, registration: Waypoints, ingress: Route, router: Network, service: Server, pod: Box, workload: Layers3 };
const colors = {
  model: "text-indigo-600", registration: "text-blue-600", ingress: "text-cyan-600",
  router: "text-violet-600", service: "text-teal-600", pod: "text-emerald-600", workload: "text-indigo-600",
};
const WIDTH = 190, HEIGHT = 88, GAP_X = 35, GAP_Y = 16;

interface ModelRoutes { model: Node; registrations: Node[] }

function matchesRoute(term: string, model: Node, reg: Node) {
  return [model.label, model.detail, reg.label, reg.upstream_model, reg.cloud_provider, reg.deployment, reg.detail]
    .some(value => value?.toLowerCase().includes(term));
}

function groupModels(graph: Graph, search: string, scope: ServingScope) {
  const byId = new Map(graph.nodes.map(node => [node.id, node]));
  const children = new Map<string, Node[]>();
  for (const [from, to] of graph.edges) {
    const target = byId.get(to);
    if (!target) continue;
    if (!children.has(from)) children.set(from, []);
    children.get(from)!.push(target);
  }
  const groups: Record<Origin, ModelRoutes[]> = { cluster: [], cloud: [], unknown: [] };
  const term = search.trim().toLowerCase();
  const matches = (...values: (string | null | undefined)[]) => values.some(v => v?.toLowerCase().includes(term));
  const models = graph.nodes.filter(n => n.kind === "model").sort((a, b) => a.label.localeCompare(b.label));
  for (const model of models) {
    const registrations = children.get(model.id) ?? [];
    const matchingModel = matches(model.label, model.detail);
    if (!registrations.length && matchingModel) groups.unknown.push({ model, registrations: [] });
    const routes: Record<Origin, Node[]> = { cluster: [], cloud: [], unknown: [] };
    for (const reg of registrations) {
      const origin = reg.origin ?? (children.has(reg.id) || reg.deployment ? "cluster" : "unknown");
      if (origin !== "cluster" && !matchesRoute(term, model, reg)) continue;
      if (origin === "cluster" && isScoped(scope) && !reg.locations?.some(item => inScope(scope, item))) continue;
      routes[origin].push(reg);
    }
    for (const origin of ["cluster", "cloud", "unknown"] as const) {
      if (routes[origin].length) groups[origin].push({ model, registrations: routes[origin] });
    }
  }
  return groups;
}

interface EntryRoutes { entry: Entry; routes: ModelRoutes[] }

function groupEntries(entries: Entry[], models: ModelRoutes[], term: string) {
  const byRegistration = new Map<string, Entry>();
  const byEntry = new Map<Entry, ModelRoutes[]>();
  const unmatched: ModelRoutes[] = [];
  for (const entry of entries) {
    for (const id of entry.registrations) byRegistration.set(id, entry);
  }
  for (const { model, registrations } of models) {
    const byTarget = new Map<Entry, Node[]>();
    const withoutTarget: Node[] = [];
    for (const reg of registrations) {
      const entry = byRegistration.get(reg.id);
      if (!entry) {
        if (matchesRoute(term, model, reg)) withoutTarget.push(reg);
        continue;
      }
      if (!byTarget.has(entry)) byTarget.set(entry, []);
      byTarget.get(entry)!.push(reg);
    }
    if (withoutTarget.length) unmatched.push({ model, registrations: withoutTarget });
    for (const [entry, regs] of byTarget) {
      if (!byEntry.has(entry)) byEntry.set(entry, []);
      byEntry.get(entry)!.push({ model, registrations: regs });
    }
  }
  // A search selects whole entry points, including siblings of the matched registration.
  const items = Array.from(byEntry, ([entry, routes]) => ({ entry, routes }))
    .filter(({ entry, routes }) => !term || [entry.name, entry.scope].some(x => x.toLowerCase().includes(term))
      || routes.some(({ model, registrations }) => registrations.some(reg => matchesRoute(term, model, reg))))
    .sort((a, b) => a.entry.scope.localeCompare(b.entry.scope) || a.entry.name.localeCompare(b.entry.name)
      || a.entry.kind.localeCompare(b.entry.kind));
  return { items, unmatched };
}

function PagedList<T,>({ items, size, children }: {
  items: T[]; size: number; children: (visible: T[]) => ReactNode;
}) {
  const t = useTranslations("serving.graph");
  const [requestedPage, setPage] = useState(0);
  const pages = Math.ceil(items.length / size);
  const page = Math.min(requestedPage, Math.max(0, pages - 1));
  const start = page * size;
  return <>
    {pages > 1 && <div className="flex flex-wrap items-center justify-end gap-2 px-2 py-1 text-xs">
      <span aria-live="polite" className="text-muted-foreground">
        {t("range", { first: start + 1, last: Math.min(start + size, items.length), total: items.length })}
      </span>
      <Button variant="outline" size="sm" disabled={!page} onClick={() => setPage(page - 1)}>{t("previous")}</Button>
      <Button variant="outline" size="sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>{t("next")}</Button>
    </div>}
    {children(items.slice(start, start + size))}
  </>;
}

function ClusterGraph({ graph, routes, locationLabel, team }: {
  graph: ResourceGraph; routes: ModelRoutes[]; locationLabel: (location: ScopedItem) => string; team: boolean;
}) {
  const t = useTranslations("serving.graph");
  const [selectedId, selectNode] = useState("");
  const children = new Map<string, string[]>();
  const ownership = graph.ownership_edges ?? [];
  const ownerByPod = new Map(ownership);
  const modelIds = new Set(graph.nodes.filter(n => n.kind === "model").map(n => n.id));
  const edges = [
    ...routes.flatMap(({ model, registrations }) => registrations.map(reg => [model.id, reg.id] as const)),
    ...graph.edges.filter(([from]) => !modelIds.has(from)), ...ownership,
  ];
  for (const [from, to] of edges) {
    const targets = children.get(from) ?? [];
    targets.push(to);
    children.set(from, targets);
  }
  const reachable = new Set<string>();
  function visit(id: string) {
    if (reachable.has(id)) return;
    reachable.add(id);
    children.get(id)?.forEach(visit);
  }
  for (const { model } of routes) visit(model.id);
  const nodes = graph.nodes.filter(n => reachable.has(n.id));
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  function ownerOf(pod: Node) {
    return nodeById.get(ownerByPod.get(pod.id) ?? "");
  }
  const selected = nodeById.get(selectedId) ?? routes[0].model;
  const stageByKind = new Map(stages.flatMap((kinds, i) => kinds.map(kind => [kind, i] as const)));
  const width = stages.length * (WIDTH + GAP_X) - GAP_X;
  const positions = new Map<string, { x: number; y: number }>();
  // Each model gets a band; shared resources retain a single position from their first route.
  let height = 0;
  for (const { model } of routes) {
    const layers = stages.map(() => [] as Node[]);
    const pending = [model.id];
    const seen = new Set<string>();
    while (pending.length) {
      const id = pending.pop()!;
      if (seen.has(id) || positions.has(id)) continue;
      seen.add(id);
      const node = nodeById.get(id)!;
      layers[stageByKind.get(node.kind)!].push(node);
      pending.push(...(children.get(id) ?? []));
    }
    const rows = Math.max(...layers.map(layer => layer.length));
    const band = rows * (HEIGHT + GAP_Y);
    layers.forEach((layer, col) => {
      layer.sort((a, b) => (ownerOf(a)?.label ?? "").localeCompare(ownerOf(b)?.label ?? "") || a.label.localeCompare(b.label));
      layer.forEach((node, row) => positions.set(node.id, {
        x: col * (WIDTH + GAP_X), y: height + (band - layer.length * (HEIGHT + GAP_Y)) / 2 + row * (HEIGHT + GAP_Y) + GAP_Y / 2,
      }));
    });
    height += band;
  }
  function statusText(status: string) {
    return t.has(`status.${status}`) ? t(`status.${status}`) : status;
  }
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <div className="max-h-[65vh] overflow-auto px-5 pb-3" tabIndex={0} aria-label={t("title")}>
        <div style={{ width }}>
          <div className="sticky top-0 z-10 flex border-b bg-card py-3 text-xs font-medium text-muted-foreground" style={{ gap: GAP_X }}>
            {titles.map(title => <div key={title} style={{ width: WIDTH }}>{t(title)}</div>)}
          </div>
          <div className="relative" style={{ width, height }}>
            <svg aria-hidden="true" className="absolute inset-0" width={width} height={height}>
              {edges.map(([from, to]) => {
                const a = positions.get(from), b = positions.get(to);
                if (!a || !b) return null;
                const x = a.x + WIDTH, y = a.y + HEIGHT / 2, end = b.y + HEIGHT / 2;
                const mid = (x + b.x) / 2;
                return <path key={JSON.stringify([from, to])} d={`M ${x} ${y} C ${mid} ${y}, ${mid} ${end}, ${b.x} ${end}`}
                  fill="none" strokeDasharray={ownerByPod.get(from) === to ? "4 3" : undefined} strokeWidth={1.6} stroke={from === selected.id || to === selected.id ? "var(--primary)" : "var(--muted-foreground)"} />;
              })}
            </svg>
            {nodes.map(node => {
              const pos = positions.get(node.id)!;
              const Icon = icons[node.kind];
              return <button key={node.id} type="button" aria-pressed={selected.id === node.id} onClick={() => selectNode(node.id)}
                title={[node.label, node.detail, node.scope, ...(node.locations ?? []).map(locationLabel)].filter(Boolean).join("\n")}
                className="absolute flex flex-col justify-center gap-1 rounded-lg border bg-card px-3 text-left shadow-sm hover:border-foreground/50 aria-pressed:border-primary aria-pressed:ring-1 aria-pressed:ring-primary"
                style={{ left: pos.x, top: pos.y, width: WIDTH, height: HEIGHT }}>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><Icon className={`size-3.5 ${colors[node.kind]}`} />{t(node.kind)}</span>
                <span className="w-full truncate font-mono text-sm font-medium">{node.deployment || node.upstream_model || node.label}</span>
                <span className="w-full truncate text-xs text-muted-foreground">{node.status ? statusText(node.status) : node.detail || node.scope}</span>
              </button>;
            })}
          </div>
        </div>
      </div>
      <div className="space-y-1 border-t bg-muted/30 px-5 py-3 text-sm" aria-live="polite">
        <div className="text-xs text-muted-foreground">{t("detail")} · {t(selected.kind)}</div>
        <div className="break-all font-medium">{selected.label}</div>
        {selected.kind === "pod" && <>
          {selected.controller && <div className="break-all">{t("controller")} · {selected.controller}</div>}
          <div>{t("owningDeployment")} · {ownerOf(selected)?.label ?? t("ownerUnknown")}</div>
        </>}
        {selected.kind === "workload" && <div className="break-all">
          {t("pods")} · {nodes.filter(n => ownerByPod.get(n.id) === selected.id).map(n => n.label).sort().join(", ")}
        </div>}
        {selected.deployment && <div>{t("deployment")} · {selected.deployment}</div>}
        {selected.upstream_model && <div className="break-all">{t("upstream")} · {selected.upstream_model}</div>}
        {selected.locations?.map(loc => <div key={JSON.stringify(loc)} className="text-muted-foreground">{locationLabel(loc)}</div>)}
        {[selected.detail, selected.scope, selected.status && statusText(selected.status)].filter(Boolean).map((line, i) =>
          <div key={i} className="break-all text-muted-foreground">{line}</div>)}
        {selected.ref && !(team && selected.kind === "router") && <Link className="underline underline-offset-4" href={selected.kind === "router" ? `/admin/llmd/${selected.ref}` : `/admin/deployments/${selected.ref}`}>
          {t(selected.kind === "router" ? "openStack" : "openDep")}
        </Link>}
      </div>
    </section>
  );
}

function EntryRow({ item, summary, scope, locationLabel }: {
  item: EntryRoutes; summary: Graph; scope?: TeamScope; locationLabel: (location: ScopedItem) => string;
}) {
  const { entry, routes } = item;
  const t = useTranslations("serving.graph");
  const [open, setOpen] = useState(false);
  const me = useMe();
  const params = scope ? teamScopeParams(scope) : new URLSearchParams({ cluster: entry.cluster, ns: entry.ns });
  params.set("kind", entry.kind);
  params.set("name", entry.name);
  const detail = useQuery({
    queryKey: ["topology-workloads", scope ? me.data?.user_id : "admin", entry.cluster, entry.ns, entry.kind, entry.name],
    queryFn: () => apiFetch<ResourceGraph>(`${scope ? "/api/serving" : "/api/admin/serving"}/topology/workloads?${params}`),
    enabled: open,
    staleTime: 30_000,
  });
  const graph = useMemo(() => {
    if (!detail.data || !open) return summary;
    const nodes = new Map(summary.nodes.map(n => [n.id, n]));
    for (const node of detail.data.nodes) nodes.set(node.id, node);
    return { nodes: [...nodes.values()], edges: [...summary.edges, ...detail.data.edges],
      ownership_edges: detail.data.ownership_edges };
  }, [summary, detail.data, open]);
  const Icon = icons[entry.root_kind];
  return <article className="overflow-hidden rounded-lg border bg-card">
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
      className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-muted/40">
      <ChevronRight className={`size-4 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      <Icon className={`size-4 ${colors[entry.root_kind]}`} />
      <span className="text-sm font-medium">{entry.name}</span>
      <span className="text-xs text-muted-foreground">{t(entry.root_kind)} · {entry.scope}</span>
      <span className="ml-auto text-xs">{t("modelCount", { count: routes.length })} · {t("regCount", { count: entry.registrations.length })}</span>
    </button>
    {open && <>
      {detail.isLoading && <p role="status" className="px-4 py-2 text-sm text-muted-foreground">{t("loadingPods")}</p>}
      {detail.isError && <div role="alert" className="flex items-center gap-2 px-4 py-2 text-sm">
        {t("workloadError")} <Button variant="outline" size="sm" onClick={() => detail.refetch()}>{t("refresh")}</Button>
      </div>}
      <ClusterGraph graph={graph} routes={routes} locationLabel={locationLabel} team={!!scope} />
    </>}
  </article>;
}

function RegistrationList({ routes }: { routes: ModelRoutes[] }) {
  const t = useTranslations("serving.graph");
  const rows = routes.flatMap(({ model, registrations }) =>
    (registrations.length ? registrations : [null]).map(reg => ({ model, reg })));
  return <PagedList items={rows} size={25}>{visible => <div className="max-h-96 overflow-auto">
    <table className="w-full text-left text-sm">
      <thead className="sticky top-0 bg-muted text-xs text-muted-foreground"><tr>
        {[t("models"), t("registration"), t("upstream")].map(label => <th key={label} className="px-4 py-2">{label}</th>)}
      </tr></thead>
      <tbody>{visible.map(({ model, reg }) => <tr key={JSON.stringify([model.id, reg?.id])} className="border-t align-top">
          <td className="px-4 py-2 font-medium">{model.label}</td>
          <td className="break-all px-4 py-2 font-mono text-xs">{reg?.label ?? t("noRegistrations")}
            {reg?.status && <div className="text-muted-foreground">{t.has(`status.${reg.status}`) ? t(`status.${reg.status}`) : reg.status}</div>}
          </td>
          <td className="break-all px-4 py-2">{reg?.upstream_model ?? "—"}{reg?.detail && <div className="text-xs text-muted-foreground">{reg.detail}</div>}</td>
        </tr>)}</tbody>
    </table>
  </div>}</PagedList>;
}

function RouteGroup({ label, routes, searching }: { label: string; routes: ModelRoutes[]; searching: boolean }) {
  const t = useTranslations("serving.graph");
  const [toggled, toggle] = useState<boolean | null>(null);
  const open = toggled ?? searching;
  return <div className="rounded-lg border bg-card">
    <button type="button" onClick={() => toggle(!open)} aria-expanded={open}
      className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm hover:bg-muted/40">
      <ChevronRight className={`size-4 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      <span className="font-semibold">{label}</span>
      <span className="truncate text-xs text-muted-foreground">{routes.slice(0, 3).map(r => r.model.label).join(", ")}</span>
      <span className="ml-auto shrink-0 text-xs tabular-nums">{t("modelCount", { count: routes.length })}</span>
    </button>
    {open && <RegistrationList routes={routes} />}
  </div>;
}

export default function ServingTopologyPage() {
  return <Suspense fallback={<div className="h-48 animate-pulse rounded-xl bg-muted" />}>
    <ServingRoleView member={<TeamScopeView>{scope => <TeamTopology key={scopeKey(scope)} scope={scope} />}</TeamScopeView>}>
      <TopologyView />
    </ServingRoleView></Suspense>;
}

function TeamTopology({scope}: {scope: TeamScope}) {
  const t = useTranslations("serving.graph");
  const ts = useTranslations("serving");
  const me = useMe();
  const query = useQuery({
    queryKey: ["team-topology", me.data?.user_id, scopeKey(scope)],
    queryFn: () => apiFetch<Graph>(`/api/serving/topology?${teamScopeParams(scope)}`),
  });
  if (query.isError) return <p role="alert">{query.error.message}</p>;
  if (!query.data) return <div aria-busy="true" className="h-32 animate-pulse bg-muted" />;
  const graph = query.data;
  const groups = groupModels(graph, "", {cluster: scope.cluster_id ?? "default", ns: scope.namespace});
  const internal = groupEntries(graph.entries, groups.cluster, "");
  const locationLabel = () => `${scope.cluster_name ?? ts("scopePortalCluster")} / ${scope.namespace}`;
  return <section className="space-y-3">
    <Link className="text-sm underline" href="/admin/serving">{t("back")}</Link>
    <h1 className="text-2xl font-bold">{t("title")}</h1>
    {!graph.litellm_available && <p role="status">{t("unavailable")}</p>}
    {!!graph.unavailable_scopes.length && <p role="status">{t("partial")} · {locationLabel()}</p>}
    {internal.items.length ? <PagedList items={internal.items} size={10}>{items => items.map(item =>
      <EntryRow key={JSON.stringify([item.entry.kind, item.entry.name])} scope={scope} summary={graph}
        item={item} locationLabel={locationLabel} />)}</PagedList> : <p>{t("noEntries")}</p>}
    {!!internal.unmatched.length && <RouteGroup label={t("unmatched")} routes={internal.unmatched} searching={false} />}
    {!!groups.cloud.length && <RouteGroup label={t("cloudModels")} routes={groups.cloud} searching={false} />}
  </section>;
}

function TopologyView() {
  const t = useTranslations("serving.graph");
  const ts = useTranslations("serving");
  const [search, setSearch] = useState("");
  const term = useDeferredValue(search.trim().toLowerCase());
  const settings = useK8sClusterSettings();
  const [scope, setScope] = useServingScope(settings.data);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["serving-topology", "entries", scope.cluster, scope.ns],
    queryFn: () => apiFetch<Graph>(`/api/admin/serving/topology${scopeQuery(scope, true)}&summary=true`),
    enabled: !!settings.data,
    staleTime: 30_000,
  });
  const data = query.data;
  const groups = useMemo(() => settings.data && data && groupModels(data, term, scope), [data, settings.data, term, scope]);
  const internal = useMemo(() => data && groups && groupEntries(data.entries, groups.cluster, term), [data, groups, term]);
  const providers = useMemo(() => {
    const result = new Map<string, ModelRoutes[]>();
    for (const { model, registrations } of groups?.cloud ?? []) {
      const byProvider = new Map<string, Node[]>();
      for (const reg of registrations) {
        const name = reg.cloud_provider!;
        if (!byProvider.has(name)) byProvider.set(name, []);
        byProvider.get(name)!.push(reg);
      }
      for (const [provider, regs] of byProvider) {
        if (!result.has(provider)) result.set(provider, []);
        result.get(provider)!.push({ model, registrations: regs });
      }
    }
    return [...result].sort(([a], [b]) => a.localeCompare(b));
  }, [groups]);
  return <div className="space-y-5">
    <Link href={`/admin/serving${settings.data ? scopeQuery(scope, true) : ""}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"><ArrowLeft className="size-4" />{t("back")}</Link>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="flex items-center gap-2 text-2xl font-bold"><Network className="size-5" />{t("title")}</h1>
      <Button variant="outline" disabled={query.isFetching || settings.isFetching} onClick={() => {
        if (settings.data) void query.refetch();
        void settings.refetch();
        void queryClient.invalidateQueries({ queryKey: ["topology-workloads"] });
      }}><RefreshCw className={query.isFetching ? "animate-spin" : ""} />{t("refresh")}</Button>
    </div>
    {query.isError && <p role="alert">{query.error.message}</p>}
    {settings.isError && <p role="alert">{settings.error.message}</p>}
    {(query.isLoading || settings.isLoading) && <div className="h-48 animate-pulse rounded-xl bg-muted" aria-busy="true" />}
    {data && groups && internal && <>
      {!data.litellm_available && <p role="status">{t("unavailable")}</p>}
      {data.unavailable_scopes.length > 0 && <p role="status" className="text-sm text-muted-foreground">{t("partial")} · {data.unavailable_scopes.join(", ")}</p>}
      <label className="relative block max-w-lg"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
        <Input type="search" className="pl-9" aria-label={t("search")} placeholder={t("search")} value={search} onChange={e => setSearch(e.target.value)} />
      </label>
      <section className="space-y-3">
        <h2 className="flex items-center gap-2 font-semibold"><Server className="size-4" />{t("clusterModels")}
          <span className="text-sm font-normal text-muted-foreground">{t("entryCount", { count: internal.items.length })}</span></h2>
        <ServingScopeFilter scope={scope} onChange={setScope} items={data.scopes ?? []} settings={settings.data} />
        {data.partial_coverage && internal.items.length > 0 && <p className="text-xs text-muted-foreground">{t("scopeCandidate")}</p>}
        {internal.items.length ? <PagedList key={JSON.stringify([scope.cluster, scope.ns, term])} items={internal.items} size={10}>
          {visible => visible.map(item => <EntryRow key={JSON.stringify([item.entry.cluster, item.entry.ns, item.entry.kind, item.entry.name])}
            item={item} summary={data} locationLabel={loc =>
              `${loc.cluster_id ? settings.data?.clusters.find(c => c.id === loc.cluster_id)?.name ?? loc.cluster_id : ts("scopePortalCluster")} / ${loc.namespace}`} />)}
        </PagedList> : <p className="text-sm text-muted-foreground">{t("noEntries")}</p>}
        {internal.unmatched.length > 0 && <RouteGroup key={term} label={t("unmatched")} routes={internal.unmatched} searching={Boolean(term)} />}
      </section>
      <section className="space-y-3">
        <h2 className="flex items-center gap-2 font-semibold"><Cloud className="size-4" />{t("cloudModels")}
          <span className="text-sm font-normal text-muted-foreground">{t("modelCount", { count: groups.cloud.length })}</span></h2>
        {providers.length ? providers.map(([name, routes]) =>
          <RouteGroup key={JSON.stringify([name, term])} label={name} routes={routes} searching={Boolean(term)} />)
          : <p className="text-sm text-muted-foreground">{t("empty")}</p>}
      </section>
      {groups.unknown.length > 0 && <RouteGroup key={term} label={t("unknownModels")} routes={groups.unknown} searching={Boolean(term)} />}
    </>}
  </div>;
}
