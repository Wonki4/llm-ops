"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Copy } from "lucide-react";

import type { GpuTypeOption, PdExtras, PdRoleOverride, PoolConfig, SidecarSpec } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Area, Field } from "@/components/engine-args-fields";
import { FormCard, FormSection } from "@/components/form-section";
import { PoolConfigCards } from "@/components/pool-config-cards";
import { poolOf, type PdRole } from "@/lib/pd-serving";
import { linesToList, linesToMap, listToLines, mapToLines } from "@/lib/placement";

/**
 * One pool of a prefill/decode recipe: its replica count plus the complete
 * pool config (image, model, resources, storage, args, probes, runtime,
 * placement). The parent remounts the panel (new `key`) after copying the
 * other pool's settings in so the text areas re-read their values.
 */
export function PdRolePanel({
  role, value, onChange, gpuTypes, onCopyFrom,
}: {
  role: PdRole;
  value: PdRoleOverride;
  onChange: (next: PdRoleOverride) => void;
  gpuTypes: GpuTypeOption[];
  onCopyFrom?: () => void;
}) {
  const t = useTranslations("servingRecipes");
  const other: PdRole = role === "prefill" ? "decode" : "prefill";
  const pool = poolOf(value as unknown as PoolConfig);

  return (
    <FormCard data-testid={`pd-role-${role}`}>
      <FormSection
        title={t(role === "prefill" ? "pdRolePrefill" : "pdRoleDecode")}
        hint={<span className="font-mono text-[11px]">{t(role === "prefill" ? "pdRolePrefillHint" : "pdRoleDecodeHint")}</span>}
        action={onCopyFrom && (
          <Button type="button" variant="outline" size="sm" onClick={onCopyFrom} data-testid={`pd-copy-${role}`}>
            <Copy className="size-3.5" />{t("pdCopyFrom", { role: other })}
          </Button>
        )}
      >
        <Field id={`pd-${role}-replicas`} label={t("pdRoleReplicas")}>
          <Input
            id={`pd-${role}-replicas`} type="number" min={0} step={1} className="w-28"
            value={value.replicas ?? 1}
            onChange={(e) => onChange({ ...value, replicas: e.target.value === "" ? 0 : Number(e.target.value) })}
          />
        </Field>
      </FormSection>
      <PdExtrasSection
        role={role}
        value={value.pd_extras ?? {}}
        onChange={(pd_extras) => onChange({ ...value, pd_extras })}
      />
      {role === "decode" && value.sidecar && (
        <SidecarSection value={value.sidecar} onChange={(sidecar) => onChange({ ...value, sidecar })} />
      )}
      <PoolConfigCards
        idPrefix={`pd-${role}`}
        engine="vllm"
        value={pool}
        gpuTypes={gpuTypes}
        pdHints
        onChange={(patch) => onChange({ ...value, ...patch })}
      />
    </FormCard>
  );
}

type MapKey = "env" | "labels" | "annotations" | "resources";
const MAP_FIELDS: { key: MapKey; label: "pdExtrasEnv" | "pdExtrasLabels" | "pdExtrasAnnotations" | "pdExtrasResources"; placeholder: string }[] = [
  { key: "env", label: "pdExtrasEnv", placeholder: "VLLM_NIXL_SIDE_CHANNEL_HOST=fieldRef:status.podIP" },
  { key: "labels", label: "pdExtrasLabels", placeholder: "llm-d.ai/guide={{model_name}}" },
  { key: "annotations", label: "pdExtrasAnnotations", placeholder: "k8s.v1.cni.cncf.io/networks=multi-nic-compute" },
  { key: "resources", label: "pdExtrasResources", placeholder: "rdma/rdma_shared_device_a=1" },
];

/**
 * The pool's P/D-only settings (env, launch args, pod labels/annotations,
 * extra resources), stored apart from its general config and layered on top
 * of it when the manifests render. Text areas keep their own string state.
 */
function PdExtrasSection({
  role, value, onChange,
}: { role: PdRole; value: PdExtras; onChange: (next: PdExtras) => void }) {
  const t = useTranslations("servingRecipes");
  const id = (k: string) => `pd-${role}-extras-${k}`;
  const [text, setText] = useState<Record<MapKey | "args", string>>(() => ({
    env: mapToLines(value.env ?? null),
    labels: mapToLines(value.labels ?? null),
    annotations: mapToLines(value.annotations ?? null),
    resources: mapToLines(value.resources ?? null),
    args: listToLines(value.args ?? null),
  }));

  function setMap(key: MapKey, raw: string) {
    setText((s) => ({ ...s, [key]: raw }));
    onChange({ ...value, [key]: linesToMap(raw) ?? {} });
  }

  return (
    <FormSection
      title={t("pdExtrasSection")}
      hint={t("pdExtrasHint")}
    >
      <Field id={id("env")} label={t(MAP_FIELDS[0].label)}>
        <Area id={id("env")} rows={4} value={text.env} placeholder={MAP_FIELDS[0].placeholder} onChange={(v) => setMap("env", v)} />
      </Field>
      <Field id={id("args")} label={t("pdExtrasArgs")}>
        <Area
          id={id("args")} rows={4} value={text.args} placeholder="--port=8000"
          onChange={(v) => { setText((s) => ({ ...s, args: v })); onChange({ ...value, args: linesToList(v) ?? [] }); }}
        />
      </Field>
      {MAP_FIELDS.slice(1).map((f) => (
        <Field key={f.key} id={id(f.key)} label={t(f.label)}>
          <Area id={id(f.key)} rows={2} value={text[f.key]} placeholder={f.placeholder} onChange={(v) => setMap(f.key, v)} />
        </Field>
      ))}
    </FormSection>
  );
}

type SidecarMapKey = "env" | "requests" | "limits";

/** The decode pool's routing sidecar (native-sidecar initContainer): image, args, env, resources. */
function SidecarSection({ value, onChange }: { value: SidecarSpec; onChange: (next: SidecarSpec) => void }) {
  const t = useTranslations("servingRecipes");
  const id = (k: string) => `pd-decode-sidecar-${k}`;
  const [text, setText] = useState<Record<SidecarMapKey | "args", string>>(() => ({
    args: listToLines(value.args ?? null),
    env: mapToLines(value.env ?? null),
    requests: mapToLines(value.requests ?? null),
    limits: mapToLines(value.limits ?? null),
  }));
  function setMap(key: SidecarMapKey, raw: string) {
    setText((s) => ({ ...s, [key]: raw }));
    onChange({ ...value, [key]: linesToMap(raw) ?? {} });
  }
  return (
    <FormSection title={t("pdSidecarSection")} hint={t("pdSidecarSectionHint")}>
      <Field id={id("image")} label={t("pdSidecarImage")}>
        <Input
          id={id("image")} className="font-mono" placeholder={t("pdSidecarImageDefault")}
          value={value.image ?? ""} onChange={(e) => onChange({ ...value, image: e.target.value.trim() || null })}
        />
      </Field>
      <Field id={id("args")} label={t("pdExtrasArgs")}>
        <Area
          id={id("args")} rows={4} value={text.args} placeholder="--port=8000"
          onChange={(v) => { setText((s) => ({ ...s, args: v })); onChange({ ...value, args: linesToList(v) ?? [] }); }}
        />
      </Field>
      <Field id={id("env")} label={t("pdExtrasEnv")}>
        <Area id={id("env")} rows={2} value={text.env} onChange={(v) => setMap("env", v)} />
      </Field>
      <Field id={id("requests")} label={t("pdSidecarRequests")}>
        <Area id={id("requests")} rows={2} value={text.requests} placeholder="cpu=100m" onChange={(v) => setMap("requests", v)} />
      </Field>
      <Field id={id("limits")} label={t("pdSidecarLimits")}>
        <Area id={id("limits")} rows={2} value={text.limits} placeholder="memory=256Mi" onChange={(v) => setMap("limits", v)} />
      </Field>
    </FormSection>
  );
}
