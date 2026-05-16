"use client";

import { useState } from "react";
import { toast } from "sonner";
import {
  Loader2,
  Plus,
  Pencil,
  Trash2,
  CheckCircle2,
  XCircle,
  Activity,
  Save,
  X,
} from "lucide-react";

import {
  useK8sClusters,
  useK8sCluster,
  useCreateK8sCluster,
  useUpdateK8sCluster,
  useDeleteK8sCluster,
  usePingK8sCluster,
} from "@/hooks/use-api";
import type { K8sCluster } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface FormState {
  name: string;
  kubeconfig_content: string;
  default_namespace: string;
  description: string;
  enabled: boolean;
}

const EMPTY: FormState = {
  name: "",
  kubeconfig_content: "",
  default_namespace: "default",
  description: "",
  enabled: true,
};

function clusterToForm(c: K8sCluster): FormState {
  return {
    name: c.name,
    kubeconfig_content: c.kubeconfig_content ?? "",
    default_namespace: c.default_namespace,
    description: c.description ?? "",
    enabled: c.enabled,
  };
}

function ClusterFormDialog({
  open,
  onOpenChange,
  editingId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingId: string | null;
}) {
  const { data: editing } = useK8sCluster(editingId);
  const createMut = useCreateK8sCluster();
  const updateMut = useUpdateK8sCluster();
  const pending = createMut.isPending || updateMut.isPending;
  const [form, setForm] = useState<FormState>(EMPTY);

  // Initialize form when dialog opens
  if (open && editingId && editing && form.name === EMPTY.name) {
    setForm(clusterToForm(editing));
  }

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm(f => ({ ...f, [key]: value }));
  }

  function handleClose() {
    setForm(EMPTY);
    onOpenChange(false);
  }

  function handleSave() {
    if (!editingId && !form.name.trim()) {
      toast.error("이름은 필수입니다.");
      return;
    }
    if (!form.kubeconfig_content.trim()) {
      toast.error("kubeconfig 내용은 필수입니다.");
      return;
    }

    if (editingId) {
      updateMut.mutate(
        {
          id: editingId,
          body: {
            kubeconfig_content: form.kubeconfig_content,
            default_namespace: form.default_namespace || "default",
            description: form.description || null,
            enabled: form.enabled,
          },
        },
        {
          onSuccess: () => {
            toast.success("클러스터가 수정되었습니다.");
            handleClose();
          },
          onError: e => toast.error(e instanceof Error ? e.message : "수정 실패"),
        },
      );
    } else {
      createMut.mutate(
        {
          name: form.name.trim(),
          kubeconfig_content: form.kubeconfig_content,
          default_namespace: form.default_namespace || "default",
          description: form.description || null,
          enabled: form.enabled,
        },
        {
          onSuccess: () => {
            toast.success("클러스터가 등록되었습니다.");
            handleClose();
          },
          onError: e => toast.error(e instanceof Error ? e.message : "등록 실패"),
        },
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={v => (v ? onOpenChange(v) : handleClose())}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editingId ? "클러스터 수정" : "새 클러스터 등록"}</DialogTitle>
          <DialogDescription>
            kubeconfig YAML 원문을 그대로 붙여넣으세요. 클러스터 이름은 등록 후 변경할 수 없습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>이름 *</Label>
              <Input
                value={form.name}
                onChange={e => set("name", e.target.value)}
                disabled={!!editingId}
                placeholder="예: prod-gpu-asia"
              />
            </div>
            <div>
              <Label>기본 네임스페이스</Label>
              <Input
                value={form.default_namespace}
                onChange={e => set("default_namespace", e.target.value)}
              />
            </div>
          </div>
          <div>
            <Label>설명</Label>
            <Input value={form.description} onChange={e => set("description", e.target.value)} />
          </div>
          <div>
            <Label>kubeconfig (YAML) *</Label>
            <textarea
              value={form.kubeconfig_content}
              onChange={e => set("kubeconfig_content", e.target.value)}
              rows={14}
              placeholder="apiVersion: v1\nkind: Config\nclusters: ..."
              className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={e => set("enabled", e.target.checked)}
            />
            활성화 (비활성 클러스터는 reconciler/배포 생성에서 사용되지 않음)
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            <X className="size-4" />
            취소
          </Button>
          <Button onClick={handleSave} disabled={pending}>
            {pending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {editingId ? "수정" : "등록"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ClusterRow({
  cluster,
  onEdit,
  onDelete,
}: {
  cluster: K8sCluster;
  onEdit: (id: string) => void;
  onDelete: (cluster: K8sCluster) => void;
}) {
  const pingMut = usePingK8sCluster();
  const [pingResult, setPingResult] = useState<{ ok: boolean; msg: string } | null>(null);

  function handlePing() {
    pingMut.mutate(cluster.id, {
      onSuccess: data => {
        if (data.ok) {
          setPingResult({ ok: true, msg: `${data.git_version ?? ""} (${data.platform ?? ""})` });
        } else {
          setPingResult({ ok: false, msg: data.error ?? "연결 실패" });
        }
      },
      onError: e => setPingResult({ ok: false, msg: e instanceof Error ? e.message : "ping 실패" }),
    });
  }

  return (
    <div className="rounded-md border p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-medium">{cluster.name}</span>
            {cluster.enabled ? (
              <Badge variant="outline" className="text-[10px]">활성</Badge>
            ) : (
              <Badge variant="secondary" className="text-[10px]">비활성</Badge>
            )}
            <Badge variant="outline" className="text-[10px]">
              ns: {cluster.default_namespace}
            </Badge>
          </div>
          {cluster.description && (
            <p className="text-xs text-muted-foreground mt-1">{cluster.description}</p>
          )}
          {pingResult && (
            <div
              className={`mt-2 flex items-center gap-1.5 text-xs ${pingResult.ok ? "text-green-600" : "text-red-600"}`}
            >
              {pingResult.ok ? (
                <CheckCircle2 className="size-3.5" />
              ) : (
                <XCircle className="size-3.5" />
              )}
              <span className="font-mono">{pingResult.msg}</span>
            </div>
          )}
        </div>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={handlePing}
            disabled={pingMut.isPending}
            title="API 서버 연결 확인"
          >
            {pingMut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Activity className="size-3.5" />
            )}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onEdit(cluster.id)}>
            <Pencil className="size-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive"
            onClick={() => onDelete(cluster)}
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

export function K8sClustersTab() {
  const { data: clusters, isLoading } = useK8sClusters();
  const deleteMut = useDeleteK8sCluster();
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<K8sCluster | null>(null);

  function handleCreate() {
    setEditingId(null);
    setFormOpen(true);
  }

  function handleEdit(id: string) {
    setEditingId(id);
    setFormOpen(true);
  }

  function handleConfirmDelete() {
    if (!deleting) return;
    deleteMut.mutate(deleting.id, {
      onSuccess: () => {
        toast.success(`'${deleting.name}' 삭제됨`);
        setDeleting(null);
      },
      onError: e => toast.error(e instanceof Error ? e.message : "삭제 실패"),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">K8s 클러스터</CardTitle>
        <CardDescription>
          모델 배포가 사용할 Kubernetes 클러스터의 kubeconfig를 등록합니다. 배포마다 클러스터를
          선택할 수 있습니다.
        </CardDescription>
        <CardAction>
          <Button size="sm" onClick={handleCreate}>
            <Plus className="size-4" />
            클러스터 등록
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : !clusters || clusters.length === 0 ? (
          <div className="text-sm text-muted-foreground py-4">
            등록된 클러스터가 없습니다.
          </div>
        ) : (
          <div className="space-y-2">
            {clusters.map(c => (
              <ClusterRow key={c.id} cluster={c} onEdit={handleEdit} onDelete={setDeleting} />
            ))}
          </div>
        )}
      </CardContent>

      <ClusterFormDialog
        open={formOpen}
        onOpenChange={v => {
          setFormOpen(v);
          if (!v) setEditingId(null);
        }}
        editingId={editingId}
      />

      <Dialog open={!!deleting} onOpenChange={open => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>클러스터 삭제</DialogTitle>
            <DialogDescription>
              <span className="font-mono font-semibold">{deleting?.name}</span> 클러스터를
              삭제합니다. 이 클러스터를 참조하는 배포가 하나라도 있으면 거부됩니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">취소</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending && <Loader2 className="size-4 animate-spin" />}
              삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
