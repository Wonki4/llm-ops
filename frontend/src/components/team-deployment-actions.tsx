"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RecipeOptions } from "@/components/recipe-options";
import { DeployRecipeBrowser, SelectedRecipe } from "@/components/deploy-recipe-browser";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useMe } from "@/hooks/use-api";
import { scopeKey, teamScopeParams, type TeamScope } from "@/hooks/use-serving-access";
import { apiFetch } from "@/lib/api";

type Pool = "prefill" | "decode";
interface Recipe {
  id: string; name: string; engine: string; serving_mode: string;
  hf_repo_id: string | null;
  prefill_replicas: number | null; decode_replicas: number | null;
}
interface ManagedDeployment {
  id: string; model_name: string; recipe_id: string | null; serving_mode: string;
  replicas: number; prefill_replicas: number | null; decode_replicas: number | null;
}

const deployments = "/api/serving/deployments";

function useRefreshDeployments() {
  const client = useQueryClient();
  return () => Promise.all(["team-deployments", "team-deployment", "team-topology", "topology-workloads"]
    .map(key => client.invalidateQueries({queryKey: [key]})));
}

export function OperatorDeployButton({scope}: {scope: TeamScope}) {
  const t = useTranslations("teamServing");
  const [open, setOpen] = useState(false);
  return <>
    <Button onClick={() => setOpen(true)}>{t("deployFromRecipe")}</Button>
    {open && <OperatorRecipeDialog scope={scope} close={() => setOpen(false)} />}
  </>;
}

function CountInput({name, label, count}: {name: string; label: string; count: number}) {
  return <label className="grid gap-1 text-sm">{label}
    <Input name={name} type="number" inputMode="numeric" min={0} max={2147483647} step={1} defaultValue={count} required />
  </label>;
}

function OperatorRecipeDialog({scope, deployment, close}: {
  scope: TeamScope; deployment?: ManagedDeployment; close: () => void;
}) {
  const t = useTranslations("teamServing");
  const td = useTranslations("adminDeployments");
  const tr = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const me = useMe();
  const router = useRouter();
  const refresh = useRefreshDeployments();
  const recipes = useQuery({
    queryKey: ["operator-recipes", me.data?.user_id, scopeKey(scope)],
    queryFn: () => apiFetch<{recipes: Recipe[]}>("/api/serving/recipes?" + teamScopeParams(scope)),
  });
  const options = recipes.data?.recipes.filter(r => !deployment || r.serving_mode === deployment.serving_mode) ?? [];
  const [selected, setSelected] = useState(deployment?.recipe_id ?? "");
  const recipe = options.find(r => r.id === selected);
  const [choosingRecipe, setChoosingRecipe] = useState(true);
  const showBrowser = !deployment && (choosingRecipe || !recipe);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => apiFetch<{id: string}>(
      deployment ? deployments + "/" + deployment.id + "/reapply" : deployments,
      {method: "POST", body: JSON.stringify(body)}
    ),
    onSuccess: async result => {
      await refresh();
      toast.success(deployment ? td("reapplySuccess") : tr("deploySuccess"));
      close();
      if (!deployment) router.push("/admin/deployments/" + result.id);
    },
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!recipe || showBrowser || save.isPending) return;
    if (deployment) { save.mutate({recipe_id: recipe.id}); return; }
    const data = new FormData(event.currentTarget);
    const counts = recipe.serving_mode === "pd" ? ["prefill_replicas", "decode_replicas"] : ["replicas"];
    save.mutate({
      recipe_id: recipe.id, cluster_id: scope.cluster_id, namespace: scope.namespace,
      model_name: String(data.get("model_name")).trim(),
      ...Object.fromEntries(counts.map(key => [key, Number(data.get(key))])),
    });
  }
  return <Dialog open onOpenChange={open => { if (!open && !save.isPending) close(); }}>
    <DialogContent className={showBrowser ? "max-h-[90dvh] overflow-y-auto sm:max-w-3xl" : "max-h-[90dvh] overflow-y-auto"}>
      <DialogHeader>
        <DialogTitle>{deployment ? td("reapplyButton") : t("deployFromRecipe")}</DialogTitle>
        <DialogDescription>{scope.cluster_name ?? t("portal")} / {scope.namespace}{deployment && " · " + deployment.model_name}</DialogDescription>
      </DialogHeader>
      {recipes.isPending && <p role="status">{tc("loading")}</p>}
      {recipes.isError && <p role="alert">{recipes.error.message}</p>}
      {recipes.isSuccess && !options.length && <p>{deployment ? td("reapplyNoRecipes") : t("noRecipes")}</p>}
      {showBrowser && !!options.length && <DeployRecipeBrowser recipes={options}
        initialRepo={recipe ? recipe.hf_repo_id ?? "" : undefined}
        onSelect={next => { setSelected(next.id); setChoosingRecipe(false); }} />}
      <form hidden={showBrowser} className="space-y-4" onSubmit={submit}>
        <fieldset disabled={save.isPending} className="space-y-3">
          {deployment ? <label className="grid gap-1 text-sm">{td("reapplyRecipe")}
            <select className="h-9 w-full rounded-md border bg-background px-2" required value={recipe?.id ?? ""}
              onChange={e => setSelected(e.target.value)}>
              <option value="" disabled>{td("reapplyPickRecipe")}</option>
              <RecipeOptions recipes={options} label={r => `${r.name} · ${r.engine} · ${r.serving_mode}`} />
            </select>
          </label> : recipe && <SelectedRecipe recipe={recipe} onChange={() => setChoosingRecipe(true)} />}
          {deployment ? <p className="text-sm text-muted-foreground">{td("reapplyDesc")}</p> : <>
            <label className="grid gap-1 text-sm">{tr("deployModelName")}
              <Input name="model_name" required maxLength={256} autoComplete="off" />
            </label>
            {recipe && <div key={recipe.id} className="grid gap-3 sm:grid-cols-2">
              {recipe.serving_mode === "pd" ? <>
                <CountInput name="prefill_replicas" label={tr("deployPrefillReplicas")} count={recipe.prefill_replicas ?? 1} />
                <CountInput name="decode_replicas" label={tr("deployDecodeReplicas")} count={recipe.decode_replicas ?? 1} />
              </> : <CountInput name="replicas" label={tr("deployReplicas")} count={1} />}
            </div>}
            <p className="text-sm text-muted-foreground">{t("ingressNote")}</p>
          </>}
        </fieldset>
        {save.isError && <p role="alert" className="text-sm text-destructive">{save.error.message}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={close} disabled={save.isPending}>{tc("cancel")}</Button>
          <Button type="submit" disabled={!recipe || save.isPending}>{deployment ? td("reapplySubmit") : tr("deploySubmit")}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}

function ScaleForm({deployment, pool, disabled, submit}: {
  deployment: ManagedDeployment; pool?: Pool; disabled: boolean; submit: (path: string, count: number) => void;
}) {
  const tr = useTranslations("servingRecipes");
  const td = useTranslations("adminDeployments");
  const count = pool ? (deployment[`${pool}_replicas`] ?? 1) : deployment.replicas;
  const label = pool === "prefill" ? tr("deployPrefillReplicas") : pool === "decode" ? tr("deployDecodeReplicas") : tr("deployReplicas");
  return <form className="flex items-end gap-2" onSubmit={event => {
    event.preventDefault();
    submit(pool ? "/pools/" + pool + "/replicas" : "/replicas", Number(new FormData(event.currentTarget).get("count")));
  }}>
    <CountInput key={count} name="count" count={count} label={label} />
    <Button type="submit" variant="outline" disabled={disabled}>{td("applyReplicas")}</Button>
  </form>;
}

export function OperatorDeploymentActions({scope, deployment}: {scope: TeamScope; deployment: ManagedDeployment}) {
  const t = useTranslations("adminDeployments");
  const tc = useTranslations("common");
  const router = useRouter();
  const refresh = useRefreshDeployments();
  const [edit, setEdit] = useState(false);
  const url = deployments + "/" + deployment.id;
  const manage = useMutation({
    mutationFn: (action: {path: string; count: number} | "delete") => action === "delete"
      ? apiFetch(url, {method: "DELETE"})
      : apiFetch(url + action.path, {method: "PUT", body: JSON.stringify({replicas: action.count})}),
    onSuccess: async (_, action) => {
      if (action === "delete") {
        toast.success(t("deleteSuccess"));
        router.replace("/admin/serving");
      } else {
        toast.success(t("replicasUpdated", {name: deployment.model_name, count: action.count}));
      }
      await refresh();
    },
  });
  const scale = (path: string, count: number) => manage.mutate({path, count});
  return <section className="max-w-2xl space-y-4 rounded-lg border bg-card p-5">
    <h2 className="font-semibold">{t("desiredReplicas")}</h2>
    <div className="flex flex-wrap gap-4">
      {deployment.serving_mode === "pd" ? (["prefill", "decode"] as const).map(pool =>
        <ScaleForm key={pool} deployment={deployment} pool={pool} disabled={manage.isPending} submit={scale} />
      ) : <ScaleForm deployment={deployment} disabled={manage.isPending} submit={scale} />}
    </div>
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" onClick={() => setEdit(true)} disabled={manage.isPending}>{t("reapplyButton")}</Button>
      <Button variant="destructive" disabled={manage.isPending} onClick={() => {
        if (window.confirm(t("deleteConfirm", {name: deployment.model_name}))) manage.mutate("delete");
      }}>{tc("delete")}</Button>
    </div>
    {manage.isError && <p role="alert" className="text-sm text-destructive">{manage.error.message}</p>}
    {edit && <OperatorRecipeDialog scope={scope} deployment={deployment} close={() => setEdit(false)} />}
  </section>;
}
