"use client";

import { useState, useEffect } from "react";
import { Loader2, Settings, Save, EyeOff, X, Plus } from "lucide-react";
import { toast } from "sonner";

import { usePortalSettings, useUpdatePortalSettings, useHiddenTeams, useUpdateHiddenTeams, useDefaultTeamRules, useUpdateDefaultTeamRules } from "@/hooks/use-api";
import type { DefaultTeamRule } from "@/hooks/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";

export default function PortalSettingsPage() {
  const { data: settings, isLoading } = usePortalSettings();
  const updateMutation = useUpdatePortalSettings();
  const { data: hiddenTeams } = useHiddenTeams();
  const updateHiddenTeams = useUpdateHiddenTeams();

  const [tpmLimit, setTpmLimit] = useState("");
  const [rpmLimit, setRpmLimit] = useState("");
  const [defaultTeamId, setDefaultTeamId] = useState("");
  const [newHiddenTeamId, setNewHiddenTeamId] = useState("");
  const { data: teamRules } = useDefaultTeamRules();
  const updateTeamRules = useUpdateDefaultTeamRules();
  const [newRulePrefix, setNewRulePrefix] = useState("");
  const [newRuleTeams, setNewRuleTeams] = useState("");

  useEffect(() => {
    if (settings) {
      setTpmLimit(String(settings.default_tpm_limit));
      setRpmLimit(String(settings.default_rpm_limit));
      setDefaultTeamId(settings.default_team_id || "");
    }
  }, [settings]);

  const handleSave = () => {
    updateMutation.mutate(
      {
        default_tpm_limit: Number(tpmLimit),
        default_rpm_limit: Number(rpmLimit),
        default_team_id: defaultTeamId || undefined,
      },
      {
        onSuccess: () => toast.success("설정이 저장되었습니다."),
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "저장 실패"),
      },
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold">포털 설정</h1>
        <p className="text-muted-foreground mt-1">
          전체 포털에 적용되는 기본값을 관리합니다
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Settings className="size-4" />
            API 키 기본 제한
          </CardTitle>
          <CardDescription>
            새로운 API 키 생성 시 적용되는 기본 TPM/RPM 제한값입니다
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="tpm-limit">기본 TPM (Tokens Per Minute)</Label>
              <Input
                id="tpm-limit"
                type="number"
                min="0"
                value={tpmLimit}
                onChange={(e) => setTpmLimit(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="rpm-limit">기본 RPM (Requests Per Minute)</Label>
              <Input
                id="rpm-limit"
                type="number"
                min="0"
                value={rpmLimit}
                onChange={(e) => setRpmLimit(e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Settings className="size-4" />
            신규 유저 자동 등록
          </CardTitle>
          <CardDescription>
            SSO 로그인 시 신규 유저를 자동으로 LiteLLM에 등록하고 기본 팀에 추가합니다
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="default-team-id">기본 팀 ID</Label>
            <Input
              id="default-team-id"
              value={defaultTeamId}
              onChange={(e) => setDefaultTeamId(e.target.value)}
              placeholder="쉼표로 여러 팀 입력 가능 (예: team-a, team-b)"
            />
            <p className="text-xs text-muted-foreground">
              모든 신규 유저가 자동으로 추가될 팀입니다. 쉼표(,)로 여러 팀을 지정할 수 있습니다.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Settings className="size-4" />
            사번 기반 기본 팀 규칙
          </CardTitle>
          <CardDescription>
            사번 prefix에 따라 신규 유저를 다른 팀에 자동 배정합니다. 매칭 순서대로 첫 번째 규칙이 적용됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Input
              placeholder="Prefix (예: X)"
              value={newRulePrefix}
              onChange={(e) => setNewRulePrefix(e.target.value)}
              className="w-32"
            />
            <Input
              placeholder="팀 ID (쉼표 구분)"
              value={newRuleTeams}
              onChange={(e) => setNewRuleTeams(e.target.value)}
              className="flex-1"
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!newRulePrefix.trim() || !newRuleTeams.trim() || updateTeamRules.isPending}
              onClick={() => {
                const teams = newRuleTeams.split(",").map((t) => t.trim()).filter(Boolean);
                if (teams.length === 0) return;
                const updated: DefaultTeamRule[] = [
                  ...(teamRules || []),
                  { prefix: newRulePrefix.trim().toUpperCase(), teams },
                ];
                updateTeamRules.mutate(updated, {
                  onSuccess: () => {
                    toast.success("규칙이 추가되었습니다.");
                    setNewRulePrefix("");
                    setNewRuleTeams("");
                  },
                  onError: (err) => toast.error(err instanceof Error ? err.message : "추가 실패"),
                });
              }}
            >
              <Plus className="size-4" />
              추가
            </Button>
          </div>
          {teamRules && teamRules.length > 0 ? (
            <div className="space-y-2">
              {teamRules.map((rule, idx) => (
                <div key={idx} className="flex items-center gap-2 rounded-md border p-2">
                  <Badge variant="default" className="shrink-0">{rule.prefix}</Badge>
                  <div className="flex flex-wrap gap-1 flex-1">
                    {rule.teams.map((teamId) => (
                      <Badge key={teamId} variant="secondary">{teamId}</Badge>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="rounded-full p-1 hover:bg-muted"
                    onClick={() => {
                      const updated = teamRules.filter((_, i) => i !== idx);
                      updateTeamRules.mutate(updated, {
                        onSuccess: () => toast.success("규칙이 삭제되었습니다."),
                        onError: (err) => toast.error(err instanceof Error ? err.message : "삭제 실패"),
                      });
                    }}
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">등록된 규칙이 없습니다. 기본 팀 ID가 사용됩니다.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <EyeOff className="size-4" />
            팀 숨기기
          </CardTitle>
          <CardDescription>
            일반 유저에게 보이지 않는 팀을 관리합니다. 관리자에게는 항상 표시됩니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Input
              placeholder="숨길 팀 ID 입력"
              value={newHiddenTeamId}
              onChange={(e) => setNewHiddenTeamId(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (!newHiddenTeamId.trim()) return;
                  const updated = [...(hiddenTeams || []), newHiddenTeamId.trim()];
                  updateHiddenTeams.mutate(updated, {
                    onSuccess: () => {
                      toast.success("팀이 숨김 목록에 추가되었습니다.");
                      setNewHiddenTeamId("");
                    },
                    onError: (err) => toast.error(err instanceof Error ? err.message : "추가 실패"),
                  });
                }
              }}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!newHiddenTeamId.trim() || updateHiddenTeams.isPending}
              onClick={() => {
                if (!newHiddenTeamId.trim()) return;
                const updated = [...(hiddenTeams || []), newHiddenTeamId.trim()];
                updateHiddenTeams.mutate(updated, {
                  onSuccess: () => {
                    toast.success("팀이 숨김 목록에 추가되었습니다.");
                    setNewHiddenTeamId("");
                  },
                  onError: (err) => toast.error(err instanceof Error ? err.message : "추가 실패"),
                });
              }}
            >
              <Plus className="size-4" />
              추가
            </Button>
          </div>
          {hiddenTeams && hiddenTeams.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {hiddenTeams.map((teamId) => (
                <Badge key={teamId} variant="secondary" className="gap-1 pr-1">
                  {teamId}
                  <button
                    type="button"
                    className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
                    onClick={() => {
                      const updated = hiddenTeams.filter((id) => id !== teamId);
                      updateHiddenTeams.mutate(updated, {
                        onSuccess: () => toast.success("팀이 숨김 목록에서 제거되었습니다."),
                        onError: (err) => toast.error(err instanceof Error ? err.message : "제거 실패"),
                      });
                    }}
                  >
                    <X className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">숨겨진 팀이 없습니다.</p>
          )}
        </CardContent>
      </Card>

      <Button
        onClick={handleSave}
        disabled={updateMutation.isPending}
      >
        {updateMutation.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Save className="size-4" />
        )}
        저장
      </Button>
    </div>
  );
}
