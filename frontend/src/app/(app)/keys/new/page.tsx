"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useMyTeams, useCreateKey, usePortalSettings } from "@/hooks/use-api";
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
  AlertTriangle,
  Key,
} from "lucide-react";
import { toast } from "sonner";
import type { CreateKeyRequest, Team } from "@/types";

const BUDGET_DURATION_OPTIONS = [
  { value: "none", label: "없음" },
  { value: "1h", label: "1시간" },
  { value: "24h", label: "1일" },
  { value: "7d", label: "7일" },
  { value: "30d", label: "30일" },
];

function ModelSelector({
  models,
  selectedModels,
  onToggle,
}: {
  models: string[];
  selectedModels: Set<string>;
  onToggle: (model: string) => void;
}) {
  if (models.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        선택한 팀에 배정된 모델이 없습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {models.map((model) => {
        const isSelected = selectedModels.has(model);
        return (
          <Badge
            key={model}
            variant={isSelected ? "default" : "outline"}
            className="cursor-pointer select-none"
            onClick={() => onToggle(model)}
          >
            {isSelected && <Check className="size-3" />}
            {model}
          </Badge>
        );
      })}
    </div>
  );
}

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
    await navigator.clipboard.writeText(token);
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
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400 mt-2">
              <AlertTriangle className="size-4 shrink-0" />
              이 키는 다시 볼 수 없습니다. 안전한 곳에 저장하세요.
            </span>
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
  const { data: teams, isLoading: teamsLoading } = useMyTeams();
  const createKeyMutation = useCreateKey();
  const { data: portalSettings } = usePortalSettings();

  const [selectedTeamId, setSelectedTeamId] = useState<string>(
    params.team_id ?? ""
  );
  const [keyAlias, setKeyAlias] = useState("");
  const [maxBudget, setMaxBudget] = useState("");
  const [budgetDuration, setBudgetDuration] = useState("none");
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const selectedTeam: Team | undefined = teams?.find(
    (t) => t.team_id === selectedTeamId
  );

  const handleToggleModel = (model: string) => {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(model)) {
        next.delete(model);
      } else {
        next.add(model);
      }
      return next;
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedTeamId) {
      toast.error("팀을 선택해주세요.");
      return;
    }

    const body: CreateKeyRequest = {
      team_id: selectedTeamId,
    };

    if (keyAlias.trim()) {
      body.key_alias = keyAlias.trim();
    }

    if (maxBudget && Number(maxBudget) > 0) {
      body.max_budget = Number(maxBudget);
    }

    if (budgetDuration !== "none") {
      body.budget_duration = budgetDuration;
    }

    if (selectedModels.size > 0) {
      body.models = Array.from(selectedModels);
    }

    createKeyMutation.mutate(body, {
      onSuccess: (data) => {
        setCreatedToken(data.token);
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
    setMaxBudget("");
    setBudgetDuration("none");
    setSelectedModels(new Set());
  };

  // When team changes, reset model selection
  const handleTeamChange = (teamId: string) => {
    setSelectedTeamId(teamId);
    setSelectedModels(new Set());
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
            필수 항목은 팀 선택뿐이며, 나머지는 선택사항입니다.
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
              <Label htmlFor="key-alias">키 별칭</Label>
              <Input
                id="key-alias"
                placeholder="예: my-project-key"
                value={keyAlias}
                onChange={(e) => setKeyAlias(e.target.value)}
              />
            </div>

            {/* Max Budget */}
            <div className="space-y-2">
              <Label htmlFor="max-budget">예산 한도</Label>
              <Input
                id="max-budget"
                type="number"
                min="0"
                step="0.01"
                placeholder="예산 한도 (USD)"
                value={maxBudget}
                onChange={(e) => setMaxBudget(e.target.value)}
              />
            </div>

            {/* Budget Duration */}
            <div className="space-y-2">
              <Label htmlFor="budget-duration">예산 주기</Label>
              <Select
                value={budgetDuration}
                onValueChange={setBudgetDuration}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {BUDGET_DURATION_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Models */}
            {selectedTeam && (
              <div className="space-y-2">
                <Label>모델 제한 (선택하지 않으면 전체 모델 사용)</Label>
                <ModelSelector
                  models={selectedTeam.models}
                  selectedModels={selectedModels}
                  onToggle={handleToggleModel}
                />
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
              disabled={!selectedTeamId || createKeyMutation.isPending}
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
