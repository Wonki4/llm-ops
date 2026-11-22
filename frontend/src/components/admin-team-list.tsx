"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useAdminTeams } from "@/hooks/use-admin-teams";
import { useHiddenTeams, useUpdateTeamVisibility, type TeamVisibility } from "@/hooks/use-api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export function AdminTeamList({ modelName, accessGroup, manageVisibility = false }: {
  modelName?: string;
  accessGroup?: string;
  manageVisibility?: boolean;
}) {
  const t = useTranslations("portalTeams");
  const tv = useTranslations("teamVisibility");
  const params = useSearchParams();
  const router = useRouter();
  const prefix = modelName || accessGroup ? "teams_" : "";
  const q = params.get(prefix + "q") || "";
  const page = Math.max(1, Number(params.get(prefix + "page")) || 1);
  const query = useAdminTeams({ q, page, model_name: modelName, access_group: accessGroup });
  const visibility = useHiddenTeams(manageVisibility);
  const updateVisibility = useUpdateTeamVisibility();

  function change(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    next.set(prefix + key, value);
    if (key === "q") next.delete(prefix + "page");
    router.replace(`?${next}`, { scroll: false });
  }
  function setVisibility(teamId: string, label: string, mode: TeamVisibility) {
    updateVisibility.mutate({ teamId, mode }, {
      onSuccess: () => toast.success(tv("saved", { team: label })),
      onError: (error) => {
        toast.error(error.message);
        void visibility.refetch();
      },
    });
  }

  return <div className="space-y-4">
    <div className="flex gap-3">
      <Input className="max-w-sm" aria-label={t("search")} placeholder={t("search")} value={q} onChange={e => change("q", e.target.value)} />
      {(modelName || accessGroup) && <Button variant="outline" asChild><Link href="/admin/teams">{t("assignTeam")}</Link></Button>}
    </div>
    {manageVisibility && <p className="text-sm text-muted-foreground">{tv("hint")}</p>}
    {query.isError && <p role="alert">{query.error.message}</p>}
    {manageVisibility && visibility.isError && <p role="alert">{tv("loadError")}: {visibility.error.message}</p>}
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead><tr className="border-b text-left">
        {["team", "members", "directAccess", "effective"].map(k => <th className="py-3 pr-4 whitespace-nowrap" key={k}>{t(k)}</th>)}
        {manageVisibility && <th className="py-3 whitespace-nowrap">{tv("title")}</th>}
      </tr></thead>
      <tbody>{query.data?.teams.map(team => {
        const label = team.team_alias || team.team_id;
        const strict = !!visibility.data?.hidden_teams_strict.includes(team.team_id);
        const hidden = strict || !!visibility.data?.hidden_teams.includes(team.team_id);
        const disabled = !visibility.data || visibility.isError || updateVisibility.isPending;
        return <tr key={team.team_id} className="border-b">
          <td className="py-3 pr-4"><Link className="font-medium underline" href={`/admin/teams/${encodeURIComponent(team.team_id)}${modelName || accessGroup ? "?tab=models" : ""}`}>{label}</Link><p className="text-xs text-muted-foreground">{team.team_id}</p></td>
          <td className="pr-4">{team.member_count}</td>
          <td className="pr-4">{t(team.access.mode)}</td>
          <td className="pr-4">{modelName ? team.access.effective_models.find(m => m.model_name === modelName)?.via.map(v => ["all", "direct"].includes(v) ? t(v) : v).join(", ") : t("modelCount", { count: team.access.effective_models.length })}</td>
          {manageVisibility && <td className="py-3">
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" role="switch" aria-checked={hidden} aria-label={tv("toggleLabel", { team: label })}
                disabled={disabled} onClick={() => setVisibility(team.team_id, label, hidden ? "visible" : "discovery")}
                className={`inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${hidden ? "bg-primary" : "bg-input"}`}>
                <span className={`size-4 rounded-full bg-background shadow-sm transition-transform ${hidden ? "translate-x-4" : "translate-x-0"}`} />
              </button>
              {visibility.data ? hidden ? <select
                aria-label={tv("scopeLabel", { team: label })} value={strict ? "strict" : "discovery"} disabled={disabled}
                onChange={e => setVisibility(team.team_id, label, e.target.value as TeamVisibility)}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                <option value="discovery">{tv("discovery")}</option><option value="strict">{tv("strict")}</option>
              </select> : <span className="text-xs text-muted-foreground">{tv("visible")}</span> : <span aria-busy="true">…</span>}
            </div>
          </td>}
        </tr>;
      })}</tbody>
    </table></div>
    {query.isPending && <p aria-busy="true">…</p>}
    {query.data && !query.data.teams.length && <p>{t("empty")}</p>}
    <div className="flex items-center gap-3">
      <Button variant="outline" disabled={page <= 1} onClick={() => change("page", String(page - 1))}>{t("previous")}</Button>
      <span>{page} / {Math.max(1, Math.ceil((query.data?.total || 0) / (query.data?.page_size || 25)))}</span>
      <Button variant="outline" disabled={!query.data || page * query.data.page_size >= query.data.total} onClick={() => change("page", String(page + 1))}>{t("next")}</Button>
    </div>
  </div>;
}
