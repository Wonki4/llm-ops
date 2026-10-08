"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { KeyRound, Loader2, Users } from "lucide-react";
import { toast } from "sonner";

import { useModels, useUpdatePersonalAccess } from "@/hooks/use-api";
import type { AdminUserDetailProfile, PersonalAccess, PersonalAccessBody } from "@/types";
import { buildAccessGroupIndex } from "@/lib/access-groups";
import { expandPersonalScope, grantEntries, grantState } from "@/lib/personal-access";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const STATES: PersonalAccess[] = ["none", "all", "custom"];

/**
 * Admin card for one user's personal-key access (Beta): the grant state,
 * the expanded scope, personal budget and the account-wide TPM/RPM, with an
 * editor dialog that writes through `/api/admin/users/{id}/personal-access`.
 */
export function PersonalAccessCard({
  userId, profile, personalKeyCount,
}: {
  userId: string;
  profile: AdminUserDetailProfile;
  personalKeyCount: number;
}) {
  const t = useTranslations("adminUsers");
  const { data: models } = useModels();
  const index = useMemo(() => buildAccessGroupIndex(models), [models]);
  const byName = useMemo(() => new Map((models ?? []).map((m) => [m.model_name, m])), [models]);
  const state = grantState(profile.models);
  const scope = useMemo(() => expandPersonalScope(profile.models, index, byName, models), [profile.models, index, byName, models]);
  const [open, setOpen] = useState(false);

  const stateLabel =
    state === "none" ? t("personalStateNone")
    : state === "all" ? t("personalStateAll")
    : t("personalStateCustom", { count: grantEntries(profile.models).length });

  return (
    <Card data-testid="personal-access-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="size-4" />
            {t("personalAccessCard")}
          </CardTitle>
          <CardDescription>{t("personalAccessDescription")}</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="personal-access-edit">
          {t("personalAccessEdit")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={state === "none" ? "outline" : "default"} data-testid="personal-access-state">{stateLabel}</Badge>
          {state === "custom" &&
            grantEntries(profile.models).map((name) => {
              const members = index.get(name);
              return (
                <Badge key={name} variant={members ? "outline" : "secondary"} className="gap-1" title={members?.map((m) => m.model_name).join("\n")}>
                  {members && <Users className="size-3" />}
                  {name}
                  {members && <span className="text-[10px] text-muted-foreground">×{members.length}</span>}
                </Badge>
              );
            })}
        </div>
        {state === "custom" && scope.some((g) => g.viaGroup) && (
          <p className="text-xs text-muted-foreground">
            → {scope.map((g) => g.name).join(", ")}
          </p>
        )}
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Field label={t("personalBudget")}>
            {profile.max_budget != null ? `$${profile.max_budget}` : t("unlimited")}
            {profile.budget_duration ? <span className="ml-1 text-xs text-muted-foreground">/ {profile.budget_duration}</span> : null}
          </Field>
          <Field label="TPM">{profile.tpm_limit?.toLocaleString() ?? "-"}</Field>
          <Field label="RPM">{profile.rpm_limit?.toLocaleString() ?? "-"}</Field>
          <Field label={t("colPersonal")}>{personalKeyCount}</Field>
        </div>
        <p className="text-xs text-muted-foreground">{t("userLimitsHint")}</p>
      </CardContent>
      {open && (
        <PersonalAccessDialog
          userId={userId}
          profile={profile}
          personalKeyCount={personalKeyCount}
          onClose={() => setOpen(false)}
        />
      )}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-medium tabular-nums">{children}</div>
    </div>
  );
}

function PersonalAccessDialog({
  userId, profile, personalKeyCount, onClose,
}: {
  userId: string;
  profile: AdminUserDetailProfile;
  personalKeyCount: number;
  onClose: () => void;
}) {
  const t = useTranslations("adminUsers");
  const tc = useTranslations("common");
  const { data: models } = useModels();
  const index = useMemo(() => buildAccessGroupIndex(models), [models]);
  const update = useUpdatePersonalAccess(userId);

  const [state, setState] = useState<PersonalAccess>(grantState(profile.models));
  const [selected, setSelected] = useState<string[]>(grantEntries(profile.models));
  const [maxBudget, setMaxBudget] = useState(profile.max_budget != null ? String(profile.max_budget) : "");
  const [duration, setDuration] = useState(profile.budget_duration ?? "");
  const [tpm, setTpm] = useState(profile.tpm_limit != null ? String(profile.tpm_limit) : "");
  const [rpm, setRpm] = useState(profile.rpm_limit != null ? String(profile.rpm_limit) : "");
  const [deleteKeys, setDeleteKeys] = useState(false);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => Array.from(index.keys()).sort(), [index]);
  const modelNames = useMemo(
    () => (models ?? []).map((m) => m.model_name).filter((n) => !index.has(n)).sort(),
    [models, index],
  );
  const q = query.trim().toLowerCase();
  const visible = (name: string) => !q || name.toLowerCase().includes(q);
  const toggle = (name: string, on: boolean) =>
    setSelected((prev) => (on ? [...prev, name] : prev.filter((n) => n !== name)));

  function save() {
    if (state === "custom" && selected.length === 0) {
      toast.error(t("personalAccessInvalid"));
      return;
    }
    const body: PersonalAccessBody = {
      access: state,
      models: state === "custom" ? selected : undefined,
      max_budget: maxBudget.trim() === "" ? null : Number(maxBudget),
      budget_duration: duration.trim() || null,
      tpm_limit: tpm.trim() === "" ? null : Number(tpm),
      rpm_limit: rpm.trim() === "" ? null : Number(rpm),
      delete_personal_keys: state === "none" && deleteKeys,
    };
    update.mutate(body, {
      onSuccess: () => { toast.success(t("personalAccessSaved")); onClose(); },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("personalAccessSaveFailed")),
    });
  }

  const hint = state === "none" ? t("personalAccessNoneHint") : state === "all" ? t("personalAccessAllHint") : t("personalAccessCustomHint");

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("personalAccessDialogTitle")}</DialogTitle>
          <DialogDescription>{t("personalAccessDialogDescription")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-5">
          <div className="space-y-2">
            <div role="radiogroup" className="inline-flex rounded-md border p-0.5" data-testid="personal-access-states">
              {STATES.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={state === s}
                  data-testid={`personal-access-${s}`}
                  onClick={() => setState(s)}
                  className={
                    "rounded px-3 py-1 text-sm transition-colors " +
                    (state === s ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {s === "none" ? t("personalStateNone") : s === "all" ? t("personalStateAll") : t("personalAccessModels")}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{hint}</p>
          </div>

          {state === "custom" && (
            <div className="space-y-3" data-testid="personal-access-picker">
              <Input placeholder={t("personalAccessSearch")} value={query} onChange={(e) => setQuery(e.target.value)} />
              {groups.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-xs font-medium text-muted-foreground">{t("personalAccessGroups")}</div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                    {groups.filter(visible).map((g) => (
                      <label key={g} className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={selected.includes(g)} onChange={(e) => toggle(g, e.target.checked)} />
                        <Users className="size-3 text-muted-foreground" />
                        {g}
                        <span className="text-[10px] text-muted-foreground">×{index.get(g)?.length ?? 0}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <div className="space-y-1.5">
                <div className="text-xs font-medium text-muted-foreground">{t("personalAccessModels")}</div>
                <div className="flex max-h-48 flex-wrap gap-x-4 gap-y-1.5 overflow-y-auto">
                  {modelNames.filter(visible).map((m) => (
                    <label key={m} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={selected.includes(m)} onChange={(e) => toggle(m, e.target.checked)} />
                      {m}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="space-y-1">
              <Label htmlFor="pa-budget">{t("personalBudget")}</Label>
              <Input id="pa-budget" type="number" min={0} step="0.01" placeholder="∞" value={maxBudget} onChange={(e) => setMaxBudget(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pa-duration">{t("personalBudgetDuration")}</Label>
              <Input id="pa-duration" placeholder={t("personalBudgetDurationPlaceholder")} value={duration} onChange={(e) => setDuration(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pa-tpm">TPM</Label>
              <Input id="pa-tpm" type="number" min={0} step={1} placeholder="∞" value={tpm} onChange={(e) => setTpm(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pa-rpm">RPM</Label>
              <Input id="pa-rpm" type="number" min={0} step={1} placeholder="∞" value={rpm} onChange={(e) => setRpm(e.target.value)} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{t("userLimits")}: {t("userLimitsHint")}</p>

          {state === "none" && personalKeyCount > 0 && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={deleteKeys} onChange={(e) => setDeleteKeys(e.target.checked)} />
              {t("deletePersonalKeys", { count: personalKeyCount })}
            </label>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={update.isPending}>{tc("cancel")}</Button>
          <Button onClick={save} disabled={update.isPending} data-testid="personal-access-save">
            {update.isPending && <Loader2 className="size-4 animate-spin" />}
            {tc("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
