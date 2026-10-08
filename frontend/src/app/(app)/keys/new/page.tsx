"use client";

import { use, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMyTeams, useCreateKey, usePortalSettings, useTeamDetail, useModels, useMe } from "@/hooks/use-api";
import { buildAccessGroupIndex } from "@/lib/access-groups";
import { expandPersonalScope, grantState } from "@/lib/personal-access";
import { Users, AlertTriangle } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  ArrowLeft,
  Copy,
  Check,
  Loader2,
  Key,
} from "lucide-react";
import { toast } from "sonner";
import type { CreateKeyRequest, Team } from "@/types";
import { copyText } from "@/lib/clipboard";
import { useTranslations } from "next-intl";

function SuccessKeyDialog({
  token,
  open,
  onClose,
}: {
  token: string;
  open: boolean;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const t = useTranslations("keys");
  const tc = useTranslations("common");

  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const ok = await copyText(token);
    if (!ok) {
      toast.error(t("copyError"));
      return;
    }
    setCopied(true);
    toast.success(t("copySuccess"));
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Key className="size-5" />
            {t("successTitle")}
          </DialogTitle>
          <DialogDescription>
            {t("successDescription")}
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 p-3">
          <code className="flex-1 break-all text-sm font-mono">{token}</code>
          <Button type="button" variant="ghost" size="icon-xs" onClick={handleCopy}>
            {copied ? (
              <Check className="size-4 text-green-600" />
            ) : (
              <Copy className="size-4" />
            )}
          </Button>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>{tc("confirm")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function CreateKeyPage({
  searchParams,
}: {
  searchParams: Promise<{ team_id?: string; type?: string }>;
}) {
  const params = use(searchParams);
  const router = useRouter();
  const { data: teams, isLoading: teamsLoading } = useMyTeams();
  const { data: me } = useMe();
  const createKeyMutation = useCreateKey();
  const { data: portalSettings } = usePortalSettings();
  const t = useTranslations("keys");
  const tc = useTranslations("common");

  // Personal keys (Beta): offered only while the portal switch is on; a grant
  // on the user's account decides whether one can actually be minted.
  const betaOn = !!me?.personal_keys_beta_enabled;
  const [keyType, setKeyType] = useState<"team" | "personal">(params.type === "personal" ? "personal" : "team");
  const personal = betaOn && keyType === "personal";
  const personalState = grantState(me?.models);
  const personalEnabled = !!me?.personal_keys_enabled;
  const [selectedModels, setSelectedModels] = useState<string[]>([]);

  const [selectedTeamId, setSelectedTeamId] = useState<string>(
    params.team_id ?? ""
  );
  const [keyAlias, setKeyAlias] = useState("");
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const selectedTeam: Team | undefined = teams?.find(
    (t) => t.team_id === selectedTeamId
  );

  const { data: teamDetail } = useTeamDetail(personal ? "" : selectedTeamId);
  const { data: allModels } = useModels();
  const accessGroupIndex = useMemo(() => buildAccessGroupIndex(allModels), [allModels]);
  const modelsByName = useMemo(() => new Map((allModels ?? []).map((m) => [m.model_name, m])), [allModels]);
  const personalScope = useMemo(
    () => expandPersonalScope(me?.models, accessGroupIndex, modelsByName, allModels),
    [me?.models, accessGroupIndex, modelsByName, allModels],
  );

  // Effective TPM/RPM for the to-be-created key: team override first, then global portal default.
  const teamTpm = personal ? null : teamDetail?.default_tpm_limit ?? null;
  const teamRpm = personal ? null : teamDetail?.default_rpm_limit ?? null;
  const effectiveTpm = teamTpm ?? portalSettings?.default_tpm_limit ?? null;
  const effectiveRpm = teamRpm ?? portalSettings?.default_rpm_limit ?? null;
  const tpmSource = teamTpm != null ? t("sourceTeam") : t("sourceGlobal");
  const rpmSource = teamRpm != null ? t("sourceTeam") : t("sourceGlobal");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!personal && !selectedTeamId) {
      toast.error(t("errorSelectTeam"));
      return;
    }
    if (personal && !personalEnabled) {
      toast.error(t("personalKeysDisabled"));
      return;
    }

    if (!keyAlias.trim()) {
      toast.error(t("errorEnterAlias"));
      return;
    }

    const body: CreateKeyRequest = {
      team_id: personal ? null : selectedTeamId,
      key_alias: keyAlias.trim(),
      ...(personal && selectedModels.length > 0 ? { models: selectedModels } : {}),
    };

    createKeyMutation.mutate(body, {
      onSuccess: (data) => {
        setCreatedToken(data.key || "");
        toast.success(t("createSuccess"));
      },
      onError: (err) => {
        toast.error(
          err instanceof Error
            ? err.message
            : t("createError")
        );
      },
    });
  };

  const handleDialogClose = () => {
    setCreatedToken(null);
    setKeyAlias("");
    router.back();
  };

  const handleTeamChange = (teamId: string) => {
    setSelectedTeamId(teamId);
  };

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Back button */}
      <Button variant="ghost" size="sm" asChild>
        <Link href={personal ? "/keys" : selectedTeamId ? `/teams/${selectedTeamId}` : "/teams"}>
          <ArrowLeft className="size-4" />
          {t("back")}
        </Link>
      </Button>

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t("createTitle")}</h1>
        <p className="text-muted-foreground mt-1">
          {t("createSubtitle")}
        </p>
      </div>

      {/* Form */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("formTitle")}</CardTitle>
          <CardDescription>
            {t("formDescription")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Key type (personal keys are a Beta feature behind a portal switch) */}
            {betaOn && (
              <div className="space-y-2">
                <Label>{t("keyTypeLabel")}</Label>
                <div role="radiogroup" className="inline-flex rounded-md border p-0.5" data-testid="key-type">
                  {(["team", "personal"] as const).map((kt) => (
                    <button
                      key={kt}
                      type="button"
                      role="radio"
                      aria-checked={keyType === kt}
                      data-testid={`key-type-${kt}`}
                      onClick={() => setKeyType(kt)}
                      className={
                        "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm transition-colors " +
                        (keyType === kt ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
                      }
                    >
                      {kt === "team" ? t("keyTypeTeam") : t("keyTypePersonal")}
                      {kt === "personal" && (
                        <Badge variant={keyType === kt ? "secondary" : "outline"} className="px-1 py-0 text-[10px]">{t("betaBadge")}</Badge>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Personal scope: what the account grants (admin-set) */}
            {personal && (
              <div className="space-y-2" data-testid="personal-scope">
                <Label>{t("personalScopeTitle")}</Label>
                {!personalEnabled ? (
                  <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
                    <span className="text-muted-foreground">{t("personalKeysDisabled")}</span>
                  </div>
                ) : personalState === "all" ? (
                  <Badge variant="secondary">{t("personalScopeAll")}</Badge>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {personalScope.map((g) => (
                      <Badge key={g.name} variant={g.viaGroup ? "outline" : "secondary"} title={g.viaGroup ? `via ${g.viaGroup}` : undefined}>
                        {g.viaGroup && <Users className="mr-1 size-3" />}
                        {g.name}
                      </Badge>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">{t("personalScopeHint")}</p>
                {me?.max_budget != null && (
                  <p className="text-xs text-muted-foreground">{t("personalBudgetHint", { budget: `$${me.max_budget}` })}</p>
                )}
              </div>
            )}

            {/* Team Select */}
            {!personal && (
            <div className="space-y-2">
              <Label htmlFor="team">
                {t("labelTeam")} <span className="text-destructive">*</span>
              </Label>
              {teamsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  {t("teamsLoading")}
                </div>
              ) : (
                <Select
                  value={selectedTeamId}
                  onValueChange={handleTeamChange}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={t("teamSelectPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {teams?.map((team) => (
                      <SelectItem key={team.team_id} value={team.team_id}>
                        {team.team_alias}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            )}

            {/* Key Alias */}
            <div className="space-y-2">
              <Label htmlFor="key-alias">{t("labelAlias")} <span className="text-destructive">*</span></Label>
              <Input
                id="key-alias"
                placeholder={t("aliasPlaceholder")}
                value={keyAlias}
                onChange={(e) => setKeyAlias(e.target.value)}
              />
            </div>

            {/* Personal key: optional narrowing inside the scope */}
            {personal && personalEnabled && personalState === "custom" && personalScope.length > 0 && (
              <div className="space-y-2" data-testid="personal-models">
                <Label>{t("labelModelsPersonal")}</Label>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {personalScope.map((g) => (
                    <label key={g.name} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={selectedModels.includes(g.name)}
                        onChange={(e) =>
                          setSelectedModels((prev) => (e.target.checked ? [...prev, g.name] : prev.filter((m) => m !== g.name)))
                        }
                      />
                      {g.name}
                    </label>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">{t("modelsPickerHint")}</p>
              </div>
            )}

            {/* Models (read-only) */}
            {!personal && selectedTeam && (
              <div className="space-y-2">
                <Label>{t("labelModels")}</Label>
                <div className="flex flex-wrap gap-2">
                  {selectedTeam.models.length > 0 ? (
                    selectedTeam.models.map((model) => {
                      const members = accessGroupIndex.get(model);
                      return members ? (
                        <Badge
                          key={model}
                          variant="outline"
                          className="gap-1"
                          title={members.map((m) => m.model_name).join("\n")}
                        >
                          <Users className="size-3" />
                          {t("accessGroupBadge", { group: model, count: members.length })}
                        </Badge>
                      ) : (
                        <Badge key={model} variant="secondary">{model}</Badge>
                      );
                    })
                  ) : (
                    <p className="text-sm text-muted-foreground">{t("noModels")}</p>
                  )}
                </div>
              </div>
            )}

            {/* TPM / RPM (read-only, team default overrides global portal default) */}
            {(effectiveTpm != null || effectiveRpm != null) && (
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>
                    TPM (Tokens Per Minute){" "}
                    {(personal || selectedTeamId) && (
                      <span className="text-xs text-muted-foreground font-normal">({tpmSource})</span>
                    )}
                  </Label>
                  <Input
                    value={effectiveTpm != null ? effectiveTpm.toLocaleString() : "-"}
                    disabled
                  />
                </div>
                <div className="space-y-2">
                  <Label>
                    RPM (Requests Per Minute){" "}
                    {(personal || selectedTeamId) && (
                      <span className="text-xs text-muted-foreground font-normal">({rpmSource})</span>
                    )}
                  </Label>
                  <Input
                    value={effectiveRpm != null ? effectiveRpm.toLocaleString() : "-"}
                    disabled
                  />
                </div>
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              className="w-full"
              disabled={(personal ? !personalEnabled : !selectedTeamId) || !keyAlias.trim() || createKeyMutation.isPending}
            >
              {createKeyMutation.isPending && (
                <Loader2 className="size-4 animate-spin" />
              )}
              {t("createKey")}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Success Dialog */}
      {createdToken && (
        <SuccessKeyDialog
          token={createdToken}
          open={!!createdToken}
          onClose={handleDialogClose}
        />
      )}
    </div>
  );
}
