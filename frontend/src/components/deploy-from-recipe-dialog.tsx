"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useCreateDeployment } from "@/hooks/use-api";
import type { ServingRecipe, CreateDeploymentBody } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

export function DeployFromRecipeDialog({
  recipe,
  onClose,
}: {
  recipe: ServingRecipe | null;
  onClose: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const tc = useTranslations("common");
  const createDep = useCreateDeployment();

  const [modelName, setModelName] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [clusterId, setClusterId] = useState("");
  const [ingressHost, setIngressHost] = useState("");
  const [ingressPath, setIngressPath] = useState("/");
  const [ingressClass, setIngressClass] = useState("nginx");
  const [replicas, setReplicas] = useState(1);

  // Instance fields reset each time a new recipe opens the dialog: the
  // caller remounts this component with `key={recipe.id}`, so fresh
  // `useState` initial values apply instead of resetting via an effect.

  function submit() {
    if (!recipe) return;
    if (!modelName.trim() || !ingressHost.trim()) {
      toast.error(t("deployRequired"));
      return;
    }
    const body: CreateDeploymentBody = {
      model_name: modelName.trim(),
      cluster_id: clusterId.trim() || null,
      namespace: namespace.trim() || "default",
      image: recipe.image,
      replicas,
      gpu_count: recipe.gpu_count,
      gpu_resource_key: recipe.gpu_resource_key,
      cpu_request: recipe.cpu_request,
      cpu_limit: recipe.cpu_limit,
      memory_request: recipe.memory_request,
      memory_limit: recipe.memory_limit,
      node_selector: recipe.node_selector,
      tolerations: recipe.tolerations,
      pvc_name: recipe.pvc_name,
      pvc_mount_path: recipe.pvc_mount_path,
      model_path: recipe.model_path,
      vllm_extra_args: recipe.vllm_extra_args,
      env: recipe.env,
      ingress_host: ingressHost.trim(),
      ingress_path: ingressPath.trim() || "/",
      ingress_class: ingressClass.trim() || "nginx",
    };
    createDep.mutate(body, {
      onSuccess: () => { toast.success(t("deploySuccess")); onClose(); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("deployError")),
    });
  }

  return (
    <Dialog open={!!recipe} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{recipe ? t("deployTitle", { name: recipe.name }) : ""}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {recipe ? `${recipe.model_path} · ${recipe.image} · ${recipe.gpu_count}×${recipe.gpu_resource_key}` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("deployModelName")} span2><Input value={modelName} onChange={(e) => setModelName(e.target.value)} /></Field>
          <Field label={t("deployNamespace")}><Input value={namespace} onChange={(e) => setNamespace(e.target.value)} /></Field>
          <Field label={t("deployClusterId")}><Input value={clusterId} onChange={(e) => setClusterId(e.target.value)} /></Field>
          <Field label={t("deployIngressHost")} span2><Input value={ingressHost} onChange={(e) => setIngressHost(e.target.value)} /></Field>
          <Field label={t("deployIngressPath")}><Input value={ingressPath} onChange={(e) => setIngressPath(e.target.value)} /></Field>
          <Field label={t("deployIngressClass")}><Input value={ingressClass} onChange={(e) => setIngressClass(e.target.value)} /></Field>
          <Field label={t("deployReplicas")}><Input type="number" min={0} value={replicas} onChange={(e) => setReplicas(Number(e.target.value))} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={createDep.isPending}>{tc("cancel")}</Button>
          <Button onClick={submit} disabled={createDep.isPending}>{t("deploySubmit")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, span2, children }: { label: string; span2?: boolean; children: React.ReactNode }) {
  return (
    <div className={span2 ? "col-span-2 space-y-1" : "space-y-1"}>
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
