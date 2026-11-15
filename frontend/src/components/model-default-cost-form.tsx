"use client";

import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useModels, useUpdateCatalogEntry } from "@/hooks/use-api";
import type { ModelCatalog, UpdateModelCatalogRequest } from "@/types";

const PER_MILLION = 1_000_000;

interface Props {
  modelName: string;
  catalog: ModelCatalog | null;
  configOnly: boolean;
  liveInput?: number | null;
  liveOutput?: number | null;
}

export function DefaultCostForm({ modelName, catalog, configOnly, liveInput, liveOutput }: Props) {
  const t = useTranslations("modelCostSchedule");
  const tc = useTranslations("common");
  const update = useUpdateCatalogEntry();
  const [editing, setEditing] = useState(false);
  const { data: models } = useModels();
  const group = models?.filter((model) => model.model_name === modelName);
  const saved = group ? group.find((model) => model.catalog)?.catalog : catalog;
  const editable = group?.find(({ litellm_info }) => litellm_info && litellm_info.model_info?.db_model !== false)?.litellm_info;
  const configFileOnly = group?.some((model) => model.litellm_info) ? !editable : configOnly;

  if (configFileOnly) {
    return <p className="mb-3 text-xs text-muted-foreground">{t("configPricing")}</p>;
  }
  if (!saved) {
    return <p className="mb-3 text-xs text-muted-foreground">{t("registerForDefaults")}</p>;
  }

  const input = saved.default_input_cost_per_token ?? editable?.model_info?.input_cost_per_token ?? liveInput;
  const output = saved.default_output_cost_per_token ?? editable?.model_info?.output_cost_per_token ?? liveOutput;
  const catalogId = saved.id;

  function updatePricing(body: UpdateModelCatalogRequest) {
    update.mutate({ catalogId, body }, {
      onSuccess: () => {
        setEditing(false);
        toast.success(t("defaultSaved"));
      },
      onError: (err) => toast.error(err.message),
    });
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    updatePricing({
      default_input_cost_per_token: Number(fields.get("input")) / PER_MILLION,
      default_output_cost_per_token: Number(fields.get("output")) / PER_MILLION,
    });
  }

  return (
    <form onSubmit={save} className="mb-3 space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <h5 className="text-sm font-medium">{t("defaultPricing")}</h5>
        {!editing && <Button type="button" size="sm" variant="outline" disabled={update.isPending} onClick={() => setEditing(true)}>{tc("edit")}</Button>}
      </div>
      <p className="text-xs text-muted-foreground">{t(saved.default_cost_managed ? "portalManaged" : "liteLLMManaged")}</p>
      <div className="grid grid-cols-2 gap-2">
        {([["input", input, t("form.inputCost")], ["output", output, t("form.outputCost")]] as const).map(([name, price, label]) => (
          <label key={name} className="space-y-1 text-xs">
            <span className="text-muted-foreground">{label}</span>
            {editing ? (
              <Input name={name} type="number" min="0" step="any" required disabled={update.isPending}
                defaultValue={price == null ? "" : price * PER_MILLION} />
            ) : <span className="block tabular-nums">{price == null ? t("unset") : price * PER_MILLION}</span>}
          </label>
        ))}
      </div>
      {editing && (
        <div className="flex justify-end gap-2">
          {saved.default_cost_managed && (
            <Button size="sm" type="button" variant="ghost" disabled={update.isPending}
              onClick={() => updatePricing({ follow_litellm_pricing: true })}>{t("followLiteLLM")}</Button>
          )}
          <Button size="sm" type="button" variant="outline" disabled={update.isPending} onClick={() => setEditing(false)}>{tc("cancel")}</Button>
          <Button size="sm" type="submit" disabled={update.isPending}>{tc("save")}</Button>
        </div>
      )}
    </form>
  );
}
