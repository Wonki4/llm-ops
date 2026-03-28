"use client";

import { useState, useEffect } from "react";
import { Loader2, Settings, Save } from "lucide-react";
import { toast } from "sonner";

import { usePortalSettings, useUpdatePortalSettings } from "@/hooks/use-api";
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

  const [tpmLimit, setTpmLimit] = useState("");
  const [rpmLimit, setRpmLimit] = useState("");
  const [defaultTeamId, setDefaultTeamId] = useState("");

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
              placeholder="비어있으면 팀 없이 유저만 생성됩니다"
            />
            <p className="text-xs text-muted-foreground">
              신규 유저가 자동으로 추가될 팀의 ID입니다
            </p>
          </div>
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
