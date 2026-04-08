"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMyTeams, useCreateKey, usePortalSettings, useMe } from "@/hooks/use-api";
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

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(token);
    } catch {
      // Fallback for non-HTTPS environments
      const textarea = document.createElement("textarea");
      textarea.value = token;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    setCopied(true);
    toast.success("키가 클립보드에 복사되었습니다.");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Key className="size-5" />
            API 키가 생성되었습니다
          </DialogTitle>
          <DialogDescription>
            내 전체키 탭에서 언제든지 키를 확인하고 복사할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 p-3">
          <code className="flex-1 break-all text-sm font-mono">{token}</code>
          <Button variant="ghost" size="icon-xs" onClick={handleCopy}>
            {copied ? (
              <Check className="size-4 text-green-600" />
            ) : (
              <Copy className="size-4" />
            )}
          </Button>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>확인</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function CreateKeyPage({
  searchParams,
}: {
  searchParams: Promise<{ team_id?: string }>;
}) {
  const params = use(searchParams);
  const router = useRouter();
  const { data: teams, isLoading: teamsLoading } = useMyTeams();
  const createKeyMutation = useCreateKey();
  const { data: portalSettings } = usePortalSettings();
  const aliasPrefix = me?.user_id ? `${me.user_id}-` : "";

  const [selectedTeamId, setSelectedTeamId] = useState<string>(
    params.team_id ?? ""
  );
  const [keyAlias, setKeyAlias] = useState("");
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const selectedTeam: Team | undefined = teams?.find(
    (t) => t.team_id === selectedTeamId
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedTeamId) {
      toast.error("팀을 선택해주세요.");
      return;
    }

    if (!keyAlias.trim()) {
      toast.error("키 별칭을 입력해주세요.");
      return;
    }

    const body: CreateKeyRequest = {
      team_id: selectedTeamId,
      key_alias: keyAlias.trim(),
    };

    createKeyMutation.mutate(body, {
      onSuccess: (data) => {
        const rawKey = data.key || data.token || "";
        setCreatedToken(rawKey.replace(/^sk-/, ""));
        toast.success("API 키가 성공적으로 생성되었습니다.");
      },
      onError: (err) => {
        toast.error(
          err instanceof Error
            ? err.message
            : "키 생성 중 오류가 발생했습니다."
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
        <Link href={selectedTeamId ? `/teams/${selectedTeamId}` : "/teams"}>
          <ArrowLeft className="size-4" />
          돌아가기
        </Link>
      </Button>

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">API 키 생성</h1>
        <p className="text-muted-foreground mt-1">
          팀에 새로운 API 키를 생성합니다.
        </p>
      </div>

      {/* Form */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">키 설정</CardTitle>
          <CardDescription>
            팀과 키 별칭은 필수 항목입니다.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Team Select */}
            <div className="space-y-2">
              <Label htmlFor="team">
                팀 <span className="text-destructive">*</span>
              </Label>
              {teamsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  팀 목록 로딩 중...
                </div>
              ) : (
                <Select
                  value={selectedTeamId}
                  onValueChange={handleTeamChange}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="팀을 선택하세요" />
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

            {/* Key Alias */}
            <div className="space-y-2">
              <Label htmlFor="key-alias">키 별칭 <span className="text-destructive">*</span></Label>
              <Input
                id="key-alias"
                placeholder="예: my-project-key"
                value={keyAlias}
                onChange={(e) => setKeyAlias(e.target.value)}
              />
            </div>

            {/* Models (read-only) */}
            {selectedTeam && (
              <div className="space-y-2">
                <Label>사용 가능한 모델 (팀 설정 기준)</Label>
                <div className="flex flex-wrap gap-2">
                  {selectedTeam.models.length > 0 ? (
                    selectedTeam.models.map((model) => (
                      <Badge key={model} variant="secondary">
                        {model}
                      </Badge>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">배정된 모델이 없습니다.</p>
                  )}
                </div>
              </div>
            )}

            {/* TPM / RPM (read-only from portal settings) */}
            {portalSettings && (
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>TPM (Tokens Per Minute)</Label>
                  <Input
                    value={portalSettings.default_tpm_limit.toLocaleString()}
                    disabled
                  />
                </div>
                <div className="space-y-2">
                  <Label>RPM (Requests Per Minute)</Label>
                  <Input
                    value={portalSettings.default_rpm_limit.toLocaleString()}
                    disabled
                  />
                </div>
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              className="w-full"
              disabled={!selectedTeamId || !keyAlias.trim() || createKeyMutation.isPending}
            >
              {createKeyMutation.isPending && (
                <Loader2 className="size-4 animate-spin" />
              )}
              키 생성
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
