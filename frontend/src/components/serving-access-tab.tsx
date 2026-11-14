"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown, Boxes, Check, ChevronRight, History, Pencil, Plus, Search, Shield, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDiscoverTeams, useK8sClusterSettings } from "@/hooks/use-api";
import type { ServingRole } from "@/hooks/use-serving-access";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Scope {
  cluster_id: string | null;
  namespace: string;
  role: ServingRole;
}
interface Grant extends Scope { id: string; team_id: string }
interface GrantEvent extends Omit<Grant, "role"> { actor: string; role: ServingRole | null; created_at: string }
interface GrantList { grants: Grant[]; history: GrantEvent[] }
interface Team { id: string; name: string; exists: boolean; showId?: boolean }
interface Cluster { id: string | null; name: string }

const api = "/api/admin/serving/grants";
const queryKey = ["serving-grants"];
const selectClass = "h-9 min-w-0 rounded-md border border-input bg-background px-2 text-sm";
const scopeKey = (g: Scope) => JSON.stringify([g.cluster_id, g.namespace]);

function groupByCluster(grants: Scope[], clusterName: (id: string | null) => string) {
  const groups = new Map<string | null, Scope[]>();
  for (const g of grants) {
    const group = groups.get(g.cluster_id);
    if (group) group.push(g);
    else groups.set(g.cluster_id, [g]);
  }
  return [...groups].sort(([a], [b]) => clusterName(a).localeCompare(clusterName(b)))
    .map(([id, scopes]) => ({ id, name: clusterName(id), scopes }));
}

function changedScopes(before: Scope[], after: Scope[]) {
  const original = new Map(before.map(g => [scopeKey(g), g]));
  const draft = new Map(after.map(g => [scopeKey(g), g]));
  return [...new Set([...original.keys(), ...draft.keys()])].flatMap(key => {
    const old = original.get(key);
    const next = draft.get(key);
    if (old?.role === next?.role) return [];
    const { cluster_id, namespace } = (next ?? old)!;
    return [{ cluster_id, namespace, previous_role: old?.role ?? null, role: next?.role ?? null }];
  });
}

interface ClusterGrantTableProps {
  group: { name: string; scopes: Scope[] };
  editor?: {
    canChangeRole: boolean;
    changeRole: (scope: Scope, role: ServingRole) => void;
    remove: (scope: Scope) => void;
  };
}

function ClusterGrantTable({ group, editor }: ClusterGrantTableProps) {
  const t = useTranslations("teamServing");
  const locale = useLocale();
  type Column = "namespace" | "role";
  const [sort, setSort] = useState<{ column: Column; direction: "ascending" | "descending" }>({
    column: "namespace", direction: "ascending",
  });
  const scopes = useMemo(() => {
    const { compare } = new Intl.Collator(locale);
    const roles = { viewer: t("viewer"), operator: t("operator") };
    const direction = sort.direction === "ascending" ? 1 : -1;
    return [...group.scopes].sort((a, b) => {
      const byName = compare(a.namespace, b.namespace);
      if (sort.column === "namespace") return direction * byName;
      return direction * compare(roles[a.role], roles[b.role]) || byName;
    });
  }, [group.scopes, locale, sort, t]);

  function toggleSort(column: Column) {
    setSort(current => ({
      column,
      direction: current.column === column && current.direction === "ascending" ? "descending" : "ascending",
    }));
  }

  return (
    <section className="overflow-hidden rounded-lg border">
      <h3 className="flex items-center gap-2 border-b bg-muted/40 px-3 py-2.5 text-sm font-semibold sm:px-4">
        <Boxes className="size-4 shrink-0" /><span className="break-all">{group.name}</span>
      </h3>
      <Table aria-label={group.name} className="table-fixed">
        <TableHeader>
          <TableRow className="text-xs">
            {(["namespace", "role"] as const).map(column => {
              const active = sort.column === column;
              const Icon = active ? (sort.direction === "ascending" ? ArrowUp : ArrowDown) : ArrowUpDown;
              return (
                <TableHead key={column} scope="col" aria-sort={active ? sort.direction : undefined}
                  className={cn("h-8 p-0", column === "role" && "w-24 sm:w-32")}>
                  <button type="button" onClick={() => toggleSort(column)}
                    className={cn("flex min-h-8 w-full items-center gap-1 hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                      column === "namespace" ? "px-3 sm:px-4" : "px-2", !active && "text-muted-foreground")}>
                    {t(column)}<Icon aria-hidden="true" className="size-3 shrink-0" />
                  </button>
                </TableHead>
              );
            })}
            {editor && <TableHead scope="col" className="w-11"><span className="sr-only">{t("revoke")}</span></TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {scopes.map(scope => (
            <TableRow key={scope.namespace}>
              <TableHead scope="row" className="px-3 py-2 font-mono font-normal whitespace-normal [overflow-wrap:anywhere] sm:px-4">
                {scope.namespace}
              </TableHead>
              <TableCell>
                {editor ? (
                  <select className="h-8 max-w-full rounded-md border bg-background px-1 text-sm"
                    aria-label={t("scopeRole", {ns: scope.namespace, cluster: group.name})}
                    disabled={!editor.canChangeRole} value={scope.role}
                    onChange={e => editor.changeRole(scope, e.target.value as ServingRole)}>
                    <option value="viewer">{t("viewer")}</option>
                    <option value="operator">{t("operator")}</option>
                  </select>
                ) : (
                  <Badge variant={scope.role === "operator" ? "secondary" : "outline"}>{t(scope.role)}</Badge>
                )}
              </TableCell>
              {editor && <TableCell className="p-1">
                <Button type="button" variant="ghost" size="icon" className="size-8"
                  aria-label={t("removeScope", {ns: scope.namespace, cluster: group.name})}
                  onClick={() => editor.remove(scope)}><Trash2 className="size-3.5" /></Button>
              </TableCell>}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}

export function ServingAccessTab() {
  const t = useTranslations("teamServing");
  const common = useTranslations("common");
  const params = useSearchParams();
  const teams = useDiscoverTeams();
  const settings = useK8sClusterSettings();
  const summary = useQuery({
    queryKey: [...queryKey, "directory"],
    queryFn: () => apiFetch<{teams: {team_id: string; count: number}[]}>(api + "/teams"),
  });
  const [search, setSearch] = useState("");
  const directory = useMemo(() => {
    const result = new Map<string, Team>();
    for (const team of teams.data ?? []) result.set(team.team_id, { id: team.team_id, name: team.team_alias || team.team_id, exists: true });
    for (const {team_id} of summary.data?.teams ?? []) {
      if (!result.has(team_id)) result.set(team_id, { id: team_id, name: team_id, exists: false });
    }
    const names = new Set<string>();
    const duplicates = new Set<string>();
    for (const {name} of result.values()) {
      if (names.has(name)) duplicates.add(name);
      names.add(name);
    }
    return [...result.values()].map(team => ({...team, showId: duplicates.has(team.name)}))
      .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  }, [teams.data, summary.data]);
  const counts = useMemo(() => new Map(summary.data?.teams.map(g => [g.team_id, g.count])), [summary.data]);
  const filtered = directory.filter(team => [team.name, team.id].some(v => v.toLowerCase().includes(search.trim().toLowerCase())));
  const active = filtered.find(team => team.id === params.get("team")) ?? filtered[0];
  function selectTeam(id: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("team", id);
    window.history.replaceState(null, "", url);
  }
  const error = teams.error ?? summary.error ?? settings.error;
  if (error) return <p role="alert">{error.message}</p>;
  if (!teams.data || !summary.data || !settings.data) return <p role="status">{common("loading")}</p>;
  const clusters: Cluster[] = [{id: null, name: t("portal")}, ...settings.data.clusters.map(({id, name}) => ({id, name}))];

  return <div className="grid min-w-64 items-start gap-5 lg:grid-cols-[minmax(15rem,0.85fr)_minmax(0,2fr)]">
    <aside aria-label={t("teams")} className="overflow-hidden rounded-xl border bg-card">
      <div className="space-y-3 border-b p-4">
        <h2 className="flex items-center gap-2 font-semibold"><Users className="size-4" />{t("teams")}
          <span className="text-sm font-normal tabular-nums text-muted-foreground">{directory.length}</span></h2>
        <div className="relative"><Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input type="search" className="pl-8" aria-label={t("searchTeams")} placeholder={t("searchTeams")} value={search}
            onChange={e => setSearch(e.target.value)} /></div>
      </div>
      <ul className="max-h-56 space-y-1 overflow-y-auto p-2 lg:max-h-[65vh]">{filtered.map(team => <li key={team.id}>
        <button type="button" title={team.id} aria-pressed={active?.id === team.id} onClick={() => selectTeam(team.id)}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm hover:bg-muted aria-pressed:bg-primary/10 aria-pressed:text-primary">
          <span className="min-w-0 flex-1 break-words font-medium">{team.name}
            {team.showId && <span className="block truncate font-mono text-xs font-normal text-muted-foreground">{team.id}</span>}</span>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{t("scopeCount", {count: counts.get(team.id) ?? 0})}</span>
          <ChevronRight className={cn("size-3.5 shrink-0", active?.id !== team.id && "invisible")} />
        </button></li>)}</ul>
      {!filtered.length && <p className="px-4 pb-4 text-sm text-muted-foreground">{t(directory.length ? "noTeamMatches" : "noTeams")}</p>}
    </aside>
    {active && <TeamPermissions key={active.id} team={active} clusters={clusters} />}
  </div>;
}

function TeamPermissions({ team, clusters }: {team: Team; clusters: Cluster[]}) {
  const t = useTranslations("teamServing");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [edit, setEdit] = useState(false);
  const detail = useQuery({
    queryKey: [...queryKey, "team", team.id],
    queryFn: () => apiFetch<GrantList>(api + "?" + new URLSearchParams({team_id: team.id})),
  });
  if (detail.isError) return <p role="alert">{detail.error.message}</p>;
  if (!detail.data) return <p role="status">{tc("loading")}</p>;
  const { grants, history } = detail.data;
  const clusterName = (id: string | null) => clusters.find(c => c.id === id)?.name ?? id ?? t("portal");
  const groups = groupByCluster(grants, clusterName);

  return <section aria-label={team.name} className="min-w-0 space-y-4">
    <div className="rounded-xl border bg-card">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b p-5">
        <div className="min-w-0"><h2 className="break-words text-xl font-semibold">{team.name}</h2>
          {team.showId && <p className="break-all font-mono text-xs text-muted-foreground">{team.id}</p>}
          <p className="mt-1 text-sm text-muted-foreground">{t("coverage", {clusters: groups.length, namespaces: grants.length})}</p></div>
        {(team.exists || grants.length > 0) && <Button type="button" size="sm" variant={grants.length ? "outline" : "default"}
          onClick={() => setEdit(true)}>{grants.length ? <Pencil /> : <Plus />}{t(grants.length ? "editAccess" : "grantAccess")}</Button>}
      </header>
      {groups.length ? <div className="space-y-3 p-3 sm:p-5">{groups.map(group =>
        <ClusterGrantTable key={group.id ?? "default"} group={group} />
      )}</div> :
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
          <Shield className="size-6 text-muted-foreground" /><p className="text-sm text-muted-foreground">{t("teamEmpty")}</p></div>}
    </div>
    <details className="rounded-xl border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">
      <History className="mr-2 inline size-4" />{t("history")}</summary>
      {history.length ? <ul className="mt-2 divide-y">{history.map(h => <li key={h.id} className="space-y-1 py-2 text-sm">
        <span>{clusterName(h.cluster_id)} / <span className="font-mono">{h.namespace}</span> · {t(h.role ?? "revoked")}</span>
        <p className="text-xs text-muted-foreground">{h.actor} · {new Date(h.created_at).toLocaleString(locale)}</p>
      </li>)}</ul> : <p className="mt-3 text-sm text-muted-foreground">{t("noHistory")}</p>}
    </details>
    {edit && <TeamEditor team={team} grants={grants} clusters={clusters} onClose={() => setEdit(false)} />}
  </section>;
}

function TeamEditor({team, grants, clusters, onClose}: {team: Team; grants: Grant[]; clusters: Cluster[]; onClose: () => void}) {
  const t = useTranslations("teamServing");
  const tc = useTranslations("common");
  const client = useQueryClient();
  const [original] = useState<Scope[]>(() => [...grants]);
  const [draft, setDraft] = useState<Scope[]>(() => [...grants]);
  const [cluster, setCluster] = useState(grants[0]?.cluster_id ?? "");
  const [names, setNames] = useState("");
  const [role, setRole] = useState<ServingRole>("viewer");
  const [error, setError] = useState("");
  const clusterName = (id: string | null) => clusters.find(c => c.id === id)?.name ?? id ?? t("portal");
  const groups = groupByCluster(draft, clusterName);
  const changes = changedScopes(original, draft);
  const save = useMutation({
    mutationFn: () => apiFetch(api, {method: "PATCH", body: JSON.stringify({team_id: team.id, changes})}),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({queryKey}),
        client.invalidateQueries({queryKey: ["team-serving-scopes"]}),
        client.invalidateQueries({queryKey: ["team-deployments", "inventory", team.id]}),
      ]);
      toast.success(t("saved"));
      onClose();
    },
    onError: async (err: Error) => {
      setError(err.message);
      await client.invalidateQueries({queryKey});
    },
  });

  function addScopes() {
    const namespaces = [...new Set(names.trim().split(/[\s,]+/).filter(Boolean))];
    const invalid = namespaces.find(ns => ns.length > 63 || !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(ns));
    if (invalid) { setError(t("invalidNamespace", {name: invalid})); return; }
    const merged = new Map(draft.map(g => [scopeKey(g), g]));
    for (const namespace of namespaces) {
      const g = {cluster_id: cluster || null, namespace, role};
      merged.set(scopeKey(g), g);
    }
    setDraft([...merged.values()]);
    setNames("");
    setError("");
  }

  return <Dialog open onOpenChange={open => { if (!open && !save.isPending) onClose(); }}>
    <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>{t("editorTitle", {team: team.name})}</DialogTitle>
        <DialogDescription>{t("editorHint")}</DialogDescription></DialogHeader>
      <fieldset disabled={save.isPending} className="min-w-0 space-y-4">
        {team.exists && <form onSubmit={e => {e.preventDefault(); addScopes();}} className="grid gap-3 rounded-lg border bg-muted/25 p-3 sm:grid-cols-2">
          <label className="grid gap-1 text-xs font-medium">{t("cluster")}
            <select className={selectClass} value={cluster} onChange={e => setCluster(e.target.value)}>
              {clusters.map(c => <option key={c.id ?? "default"} value={c.id ?? ""}>{c.name}</option>)}</select></label>
          <label className="grid gap-1 text-xs font-medium">{t("role")}
            <select className={selectClass} value={role} onChange={e => setRole(e.target.value as ServingRole)}>
              <option value="viewer">{t("viewer")}</option><option value="operator">{t("operator")}</option></select></label>
          <label className="grid gap-1 text-xs font-medium sm:col-span-2">{t("namespaces")}
            <Input value={names} onChange={e => setNames(e.target.value)} placeholder={t("namespaceExample")} /></label>
          <Button type="submit" className="w-fit" variant="outline" size="sm" disabled={!names.trim()}><Plus />{t("addScopes")}</Button>
        </form>}
        {groups.map(group => <ClusterGrantTable key={group.id ?? "default"} group={group} editor={{
          canChangeRole: team.exists,
          changeRole: (scope, role) => setDraft(items => items.map(item => item === scope ? {...item, role} : item)),
          remove: scope => setDraft(items => items.filter(item => item !== scope)),
        }} />)}
      </fieldset>
      {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
      <DialogFooter>
        <Button type="button" variant="outline" disabled={save.isPending} onClick={onClose}>{tc("cancel")}</Button>
        <Button type="button" disabled={save.isPending || names.trim() !== "" || !changes.length} onClick={() => save.mutate()}>
          <Check />{t("saveTeam")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
