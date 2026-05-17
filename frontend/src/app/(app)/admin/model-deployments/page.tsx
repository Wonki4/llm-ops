"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import {
  Loader2,
  Plus,
  Server,
  Trash2,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Circle,
  Activity,
} from "lucide-react";

import {
  useModelDeployments,
  useModelDeployment,
  useCreateModelDeployment,
  useUpdateModelDeployment,
  useDeleteModelDeployment,
  useDeploymentEvents,
  useAckDeploymentEvent,
  useK8sClusters,
  useModelCatalog,
  useUpdateCatalogEntry,
} from "@/hooks/use-api";
import type {
  ModelDeployment,
  CreateModelDeploymentRequest,
  UpdateModelDeploymentRequest,
} from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";

// ─── Helpers ──────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  Ready: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  Updating: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  Pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
  Unhealthy: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
  Failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  Stopped: "bg-gray-200 text-gray-700 dark:bg-gray-800/30 dark:text-gray-400",
  Missing: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <Badge className={`text-xs ${STATUS_STYLES[status] ?? "bg-gray-100 text-gray-700"}`}>
      {status}
    </Badge>
  );
}

const SEVERITY_ICON: Record<string, typeof AlertCircle> = {
  info: Circle,
  warning: AlertCircle,
  error: AlertCircle,
};

const SEVERITY_COLOR: Record<string, string> = {
  info: "text-muted-foreground",
  warning: "text-orange-500",
  error: "text-red-500",
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ─── Form state ──────────────────────────────────────────────

interface FormState {
  model_name: string;
  cluster_id: string;
  namespace: string;
  image: string;
  replicas: string;
  gpu_count: string;
  gpu_resource_key: string;
  cpu_request: string;
  cpu_limit: string;
  memory_request: string;
  memory_limit: string;
  node_selector: string; // JSON string
  tolerations: string; // JSON string
  pvc_name: string;
  pvc_mount_path: string;
  model_path: string;
  vllm_extra_args: string; // newline-separated
  env: string; // JSON string
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
}

const EMPTY_FORM: FormState = {
  model_name: "",
  cluster_id: "",
  namespace: "default",
  image: "vllm/vllm-openai:latest",
  replicas: "1",
  gpu_count: "1",
  gpu_resource_key: "nvidia.com/gpu",
  cpu_request: "",
  cpu_limit: "",
  memory_request: "",
  memory_limit: "",
  node_selector: "",
  tolerations: "",
  pvc_name: "",
  pvc_mount_path: "",
  model_path: "",
  vllm_extra_args: "",
  env: "",
  ingress_host: "",
  ingress_path: "/",
  ingress_class: "nginx",
};

function depToForm(d: ModelDeployment): FormState {
  return {
    model_name: d.model_name,
    cluster_id: d.cluster_id ?? "",
    namespace: d.namespace,
    image: d.image,
    replicas: String(d.replicas),
    gpu_count: String(d.gpu_count),
    gpu_resource_key: d.gpu_resource_key,
    cpu_request: d.cpu_request ?? "",
    cpu_limit: d.cpu_limit ?? "",
    memory_request: d.memory_request ?? "",
    memory_limit: d.memory_limit ?? "",
    node_selector: d.node_selector ? JSON.stringify(d.node_selector, null, 2) : "",
    tolerations: d.tolerations ? JSON.stringify(d.tolerations, null, 2) : "",
    pvc_name: d.pvc_name ?? "",
    pvc_mount_path: d.pvc_mount_path ?? "",
    model_path: d.model_path,
    vllm_extra_args: (d.vllm_extra_args ?? []).join("\n"),
    env: d.env ? JSON.stringify(d.env, null, 2) : "",
    ingress_host: d.ingress_host,
    ingress_path: d.ingress_path,
    ingress_class: d.ingress_class,
  };
}

function formToBody(form: FormState): CreateModelDeploymentRequest | string {
  if (!form.model_name.trim()) return "model_name은 필수입니다.";
  if (!form.cluster_id) return "cluster를 선택하세요.";
  if (!form.model_path.trim()) return "model_path는 필수입니다.";
  if (!form.ingress_host.trim()) return "ingress_host는 필수입니다.";

  const parseJson = (raw: string, label: string): unknown | null | string => {
    if (!raw.trim()) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return `${label} JSON 형식이 잘못되었습니다`;
    }
  };

  const nodeSel = parseJson(form.node_selector, "node_selector");
  if (typeof nodeSel === "string") return nodeSel;
  const tols = parseJson(form.tolerations, "tolerations");
  if (typeof tols === "string") return tols;
  const env = parseJson(form.env, "env");
  if (typeof env === "string") return env;

  return {
    model_name: form.model_name.trim(),
    cluster_id: form.cluster_id,
    namespace: form.namespace.trim() || "default",
    image: form.image.trim(),
    replicas: Number(form.replicas) || 0,
    gpu_count: Number(form.gpu_count) || 0,
    gpu_resource_key: form.gpu_resource_key.trim() || "nvidia.com/gpu",
    cpu_request: form.cpu_request.trim() || null,
    cpu_limit: form.cpu_limit.trim() || null,
    memory_request: form.memory_request.trim() || null,
    memory_limit: form.memory_limit.trim() || null,
    node_selector: (nodeSel as Record<string, string>) ?? null,
    tolerations: (tols as unknown[]) ?? null,
    pvc_name: form.pvc_name.trim() || null,
    pvc_mount_path: form.pvc_mount_path.trim() || null,
    model_path: form.model_path.trim(),
    vllm_extra_args:
      form.vllm_extra_args
        .split("\n")
        .map(s => s.trim())
        .filter(Boolean) || null,
    env: (env as Record<string, string>) ?? null,
    ingress_host: form.ingress_host.trim(),
    ingress_path: form.ingress_path.trim() || "/",
    ingress_class: form.ingress_class.trim() || "nginx",
  };
}

// ─── Form Dialog ──────────────────────────────────────────────

function DeploymentFormDialog({
  open,
  onOpenChange,
  editing,
  initialForm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: ModelDeployment | null;
  initialForm: FormState;
}) {
  const createMut = useCreateModelDeployment();
  const updateMut = useUpdateModelDeployment();
  const { data: clusters } = useK8sClusters();
  const pending = createMut.isPending || updateMut.isPending;
  const [form, setForm] = useState<FormState>(initialForm);

  useEffect(() => {
    if (open) setForm(initialForm);
  }, [open, initialForm]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm(f => ({ ...f, [key]: value }));
  }

  function handleSave() {
    const body = formToBody(form);
    if (typeof body === "string") {
      toast.error(body);
      return;
    }
    if (editing) {
      const { model_name: _, ...rest } = body;
      const patchBody: UpdateModelDeploymentRequest = rest;
      updateMut.mutate(
        { id: editing.id, body: patchBody },
        {
          onSuccess: () => {
            toast.success("배포가 수정되었습니다.");
            onOpenChange(false);
          },
          onError: e => toast.error(e instanceof Error ? e.message : "수정 실패"),
        },
      );
    } else {
      createMut.mutate(body, {
        onSuccess: () => {
          toast.success("배포가 생성되었습니다. Reconciler가 K8s 상태를 곧 가져옵니다.");
          onOpenChange(false);
        },
        onError: e => toast.error(e instanceof Error ? e.message : "생성 실패"),
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? "배포 수정" : "새 모델 배포"}</DialogTitle>
          <DialogDescription>
            K8s 클러스터에 vLLM 모델 서버를 배포합니다. Reconciler가 60초마다 상태를 갱신하고
            Ready 진입 시 LiteLLM에 자동 등록 + catalog row를 생성합니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>모델명 *</Label>
              <Input
                value={form.model_name}
                onChange={e => set("model_name", e.target.value)}
                disabled={!!editing}
                placeholder="예: llama-3-8b-instruct"
              />
            </div>
            <div>
              <Label>클러스터 *</Label>
              <Select
                value={form.cluster_id}
                onValueChange={v => {
                  set("cluster_id", v);
                  // Prefill namespace from cluster default if user hasn't customized.
                  const c = clusters?.find(cc => cc.id === v);
                  if (c && (!form.namespace || form.namespace === "default")) {
                    set("namespace", c.default_namespace);
                  }
                }}
                disabled={!!editing}
              >
                <SelectTrigger>
                  <SelectValue placeholder="배포할 K8s 클러스터 선택" />
                </SelectTrigger>
                <SelectContent>
                  {(clusters ?? [])
                    .filter(c => c.enabled)
                    .map(c => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  {(clusters?.filter(c => c.enabled).length ?? 0) === 0 && (
                    <div className="px-2 py-1.5 text-xs text-muted-foreground">
                      활성 클러스터가 없습니다. 포털 설정에서 추가하세요.
                    </div>
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label>네임스페이스</Label>
            <Input value={form.namespace} onChange={e => set("namespace", e.target.value)} />
          </div>
          <div>
            <Label>이미지</Label>
            <Input value={form.image} onChange={e => set("image", e.target.value)} />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Replicas</Label>
              <Input
                type="number"
                min="0"
                value={form.replicas}
                onChange={e => set("replicas", e.target.value)}
              />
            </div>
            <div>
              <Label>GPU 개수</Label>
              <Input
                type="number"
                min="0"
                value={form.gpu_count}
                onChange={e => set("gpu_count", e.target.value)}
              />
            </div>
            <div>
              <Label>GPU 리소스 키</Label>
              <Input
                value={form.gpu_resource_key}
                onChange={e => set("gpu_resource_key", e.target.value)}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>CPU request</Label>
              <Input value={form.cpu_request} onChange={e => set("cpu_request", e.target.value)} placeholder="예: 4" />
            </div>
            <div>
              <Label>CPU limit</Label>
              <Input value={form.cpu_limit} onChange={e => set("cpu_limit", e.target.value)} placeholder="예: 8" />
            </div>
            <div>
              <Label>Memory request</Label>
              <Input
                value={form.memory_request}
                onChange={e => set("memory_request", e.target.value)}
                placeholder="예: 16Gi"
              />
            </div>
            <div>
              <Label>Memory limit</Label>
              <Input
                value={form.memory_limit}
                onChange={e => set("memory_limit", e.target.value)}
                placeholder="예: 32Gi"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>node_selector (JSON)</Label>
              <textarea
                value={form.node_selector}
                onChange={e => set("node_selector", e.target.value)}
                rows={3}
                placeholder='{"gpu": "a100"}'
                className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
              />
            </div>
            <div>
              <Label>tolerations (JSON array)</Label>
              <textarea
                value={form.tolerations}
                onChange={e => set("tolerations", e.target.value)}
                rows={3}
                placeholder='[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"}]'
                className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>PVC 이름</Label>
              <Input value={form.pvc_name} onChange={e => set("pvc_name", e.target.value)} placeholder="예: model-weights" />
            </div>
            <div>
              <Label>PVC 마운트 경로</Label>
              <Input
                value={form.pvc_mount_path}
                onChange={e => set("pvc_mount_path", e.target.value)}
                placeholder="예: /mnt/models"
              />
            </div>
          </div>
          <div>
            <Label>model_path *</Label>
            <Input
              value={form.model_path}
              onChange={e => set("model_path", e.target.value)}
              placeholder="vLLM --model 인자. 보통 PVC 마운트 하위 경로 (예: /mnt/models/llama-3-8b)"
            />
          </div>
          <div>
            <Label>vLLM 추가 args (줄바꿈 구분)</Label>
            <textarea
              value={form.vllm_extra_args}
              onChange={e => set("vllm_extra_args", e.target.value)}
              rows={3}
              placeholder={"--tensor-parallel-size\n2\n--gpu-memory-utilization\n0.9"}
              className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
            />
          </div>
          <div>
            <Label>env (JSON)</Label>
            <textarea
              value={form.env}
              onChange={e => set("env", e.target.value)}
              rows={2}
              placeholder='{"HF_HUB_OFFLINE":"1"}'
              className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-1">
              <Label>Ingress host *</Label>
              <Input
                value={form.ingress_host}
                onChange={e => set("ingress_host", e.target.value)}
                placeholder="예: llama.llm.example.com"
              />
            </div>
            <div>
              <Label>Ingress path</Label>
              <Input value={form.ingress_path} onChange={e => set("ingress_path", e.target.value)} />
            </div>
            <div>
              <Label>Ingress class</Label>
              <Input value={form.ingress_class} onChange={e => set("ingress_class", e.target.value)} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">취소</Button>
          </DialogClose>
          <Button onClick={handleSave} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {editing ? "수정" : "배포"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Detail Sheet ─────────────────────────────────────────────

function ServingModelsSection({
  deploymentId,
  deploymentModelName,
}: {
  deploymentId: string;
  deploymentModelName: string;
}) {
  const { data: catalog, isLoading } = useModelCatalog();
  const updateMut = useUpdateCatalogEntry();
  const [attaching, setAttaching] = useState(false);
  const [selectedId, setSelectedId] = useState("");

  const attached = (catalog ?? []).filter((c) => c.deployment_id === deploymentId);
  const unattached = (catalog ?? []).filter((c) => !c.deployment_id);

  function handleAttach() {
    if (!selectedId) return;
    updateMut.mutate(
      { catalogId: selectedId, body: { deployment_id: deploymentId } },
      {
        onSuccess: () => {
          toast.success("모델이 attach되었습니다.");
          setAttaching(false);
          setSelectedId("");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "attach 실패"),
      },
    );
  }

  function handleDetach(catalogId: string) {
    if (!confirm("이 모델을 deployment에서 detach할까요? LiteLLM 라우팅이 끊깁니다.")) return;
    updateMut.mutate(
      { catalogId, body: { deployment_id: null } },
      {
        onSuccess: () => toast.success("Detach되었습니다."),
        onError: (e) => toast.error(e instanceof Error ? e.message : "detach 실패"),
      },
    );
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Server className="size-4" />
          서빙 모델
          <span className="text-xs font-normal text-muted-foreground">
            ({attached.length}개)
          </span>
        </h4>
        {!attaching && (
          <Button size="sm" variant="outline" onClick={() => setAttaching(true)}>
            <Plus className="size-3.5" />
            attach
          </Button>
        )}
      </div>

      {attaching && (
        <div className="flex gap-2 mb-2">
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="flex-1 rounded-md border border-input bg-transparent px-2 py-1 text-xs"
          >
            <option value="">attach할 카탈로그 선택</option>
            {unattached.map((c) => (
              <option key={c.id} value={c.id}>
                {c.model_name}
              </option>
            ))}
          </select>
          <Button size="sm" onClick={handleAttach} disabled={!selectedId || updateMut.isPending}>
            확인
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { setAttaching(false); setSelectedId(""); }}>
            취소
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-3">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : attached.length === 0 ? (
        <div className="text-xs text-muted-foreground py-2">
          attach된 모델이 없습니다. 기본적으로 deployment의 model_name(
          <span className="font-mono">{deploymentModelName}</span>
          )이 자동으로 attach됩니다.
        </div>
      ) : (
        <div className="space-y-1.5">
          {attached.map((c) => (
            <div
              key={c.id}
              className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-xs"
            >
              <div className="min-w-0">
                <div className="font-mono font-medium truncate">{c.model_name}</div>
                {c.litellm_model_id && (
                  <div className="text-[10px] text-muted-foreground font-mono">
                    LiteLLM id: {c.litellm_model_id}
                  </div>
                )}
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                onClick={() => handleDetach(c.id)}
                disabled={updateMut.isPending}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function EventsList({ deploymentId }: { deploymentId: string }) {
  const { data: events, isLoading } = useDeploymentEvents(deploymentId);
  const ackMut = useAckDeploymentEvent();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!events || events.length === 0) {
    return <div className="text-xs text-muted-foreground py-2">이벤트가 없습니다.</div>;
  }
  return (
    <div className="space-y-2">
      {events.map(e => {
        const Icon = SEVERITY_ICON[e.severity] ?? Circle;
        return (
          <div
            key={e.id}
            className={`rounded-md border px-3 py-2 text-xs ${e.seen ? "opacity-60" : ""}`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Icon className={`size-3.5 ${SEVERITY_COLOR[e.severity] ?? ""}`} />
                <span className="font-medium">{e.event_type}</span>
                {e.from_status && e.to_status && (
                  <span className="text-muted-foreground">
                    {e.from_status} → {e.to_status}
                  </span>
                )}
                {e.alert_sent && (
                  <Badge variant="outline" className="text-[10px]">
                    Slack
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">{formatDate(e.created_at)}</span>
                {!e.seen && (
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() => ackMut.mutate({ deploymentId, eventId: e.id })}
                    disabled={ackMut.isPending}
                  >
                    확인
                  </button>
                )}
              </div>
            </div>
            {e.message && <p className="mt-1 text-muted-foreground">{e.message}</p>}
          </div>
        );
      })}
    </div>
  );
}

function DeploymentDetailSheet({
  open,
  deploymentId,
  onOpenChange,
  onEdit,
  onDelete,
}: {
  open: boolean;
  deploymentId: string | null;
  onOpenChange: (open: boolean) => void;
  onEdit: (d: ModelDeployment) => void;
  onDelete: (d: ModelDeployment) => void;
}) {
  const { data: dep, isLoading } = useModelDeployment(deploymentId);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg w-[560px]">
        <SheetHeader className="px-6 pt-6 pb-4">
          <div className="flex items-center gap-2 flex-wrap">
            <SheetTitle>{dep?.model_name ?? "배포 상세"}</SheetTitle>
            {dep && <StatusBadge status={dep.status} />}
          </div>
          <SheetDescription>
            {dep
              ? `namespace=${dep.namespace} · ${dep.ready_replicas}/${dep.replicas} ready`
              : "로딩 중..."}
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-4">
          {isLoading || !dep ? (
            <div className="flex justify-center py-8">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              {dep.status_message && (
                <div className="rounded-md bg-muted p-2 text-xs">{dep.status_message}</div>
              )}

              <section className="space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Image</span>
                  <span className="font-mono text-xs break-all text-right">{dep.image}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">GPU</span>
                  <span className="font-mono text-xs">
                    {dep.gpu_count} ({dep.gpu_resource_key})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Ingress</span>
                  <a
                    href={`https://${dep.ingress_host}${dep.ingress_path}`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs text-primary hover:underline truncate ml-2"
                  >
                    {dep.ingress_host}
                    {dep.ingress_path}
                  </a>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">model_path</span>
                  <span className="font-mono text-xs break-all text-right">{dep.model_path}</span>
                </div>
                {dep.last_synced_at && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">마지막 동기화</span>
                    <span className="text-xs">{formatDate(dep.last_synced_at)}</span>
                  </div>
                )}
              </section>

              <Separator />
              <ServingModelsSection deploymentId={dep.id} deploymentModelName={dep.model_name} />

              <Separator />
              <section>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Activity className="size-4" />
                  이벤트
                </h4>
                <EventsList deploymentId={dep.id} />
              </section>

              <div className="flex gap-2 pt-2">
                <Button variant="outline" size="sm" onClick={() => onEdit(dep)} className="flex-1">
                  수정
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => onDelete(dep)}
                  className="flex-1"
                >
                  <Trash2 className="size-4" />
                  삭제
                </Button>
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ─── Main Page ────────────────────────────────────────────────

export default function ModelDeploymentsPage() {
  const { data: deployments, isLoading, refetch } = useModelDeployments();
  const deleteMut = useDeleteModelDeployment();
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ModelDeployment | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [deletingDep, setDeletingDep] = useState<ModelDeployment | null>(null);

  const formInitial = useMemo<FormState>(
    () => (editing ? depToForm(editing) : EMPTY_FORM),
    [editing],
  );

  function handleCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function handleEdit(d: ModelDeployment) {
    setEditing(d);
    setDetailId(null);
    setFormOpen(true);
  }

  function handleConfirmDelete() {
    if (!deletingDep) return;
    deleteMut.mutate(deletingDep.id, {
      onSuccess: () => {
        toast.success(`'${deletingDep.model_name}' 삭제 완료`);
        setDeletingDep(null);
        setDetailId(null);
      },
      onError: e => toast.error(e instanceof Error ? e.message : "삭제 실패"),
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Server className="size-6" />
            모델 배포
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            K8s 클러스터에 배포된 vLLM 모델 서버 관리. Reconciler가 60초마다 상태를 갱신하고
            Ready 진입 시 LiteLLM에 자동 등록합니다.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="size-4" />
            새로고침
          </Button>
          <Button asChild size="sm">
            <Link href="/admin/model-deployments/new">
              <Plus className="size-4" />
              배포 추가
            </Link>
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : !deployments || deployments.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">아직 배포된 모델이 없습니다</CardTitle>
            <CardDescription>
              우측 상단의 "배포 추가" 버튼으로 첫 vLLM 모델을 K8s에 배포해보세요.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>모델</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>네임스페이스</TableHead>
                <TableHead>이미지</TableHead>
                <TableHead>GPU</TableHead>
                <TableHead>Replicas</TableHead>
                <TableHead>Ingress</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {deployments.map(d => (
                <TableRow key={d.id} className="cursor-pointer" onClick={() => setDetailId(d.id)}>
                  <TableCell className="font-medium">{d.model_name}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <StatusBadge status={d.status} />
                      {d.status === "Ready" && <CheckCircle2 className="size-3.5 text-green-600" />}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs font-mono">{d.namespace}</TableCell>
                  <TableCell className="text-xs font-mono max-w-[200px] truncate">{d.image}</TableCell>
                  <TableCell>{d.gpu_count}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {d.ready_replicas}/{d.replicas}
                  </TableCell>
                  <TableCell className="text-xs font-mono max-w-[180px] truncate">
                    {d.ingress_host}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={e => {
                        e.stopPropagation();
                        setDeletingDep(d);
                      }}
                    >
                      <Trash2 className="size-3.5 text-destructive" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <DeploymentFormDialog
        open={formOpen}
        onOpenChange={open => {
          setFormOpen(open);
          if (!open) setEditing(null);
        }}
        editing={editing}
        initialForm={formInitial}
      />

      <DeploymentDetailSheet
        open={!!detailId}
        deploymentId={detailId}
        onOpenChange={open => {
          if (!open) setDetailId(null);
        }}
        onEdit={handleEdit}
        onDelete={d => setDeletingDep(d)}
      />

      <Dialog open={!!deletingDep} onOpenChange={open => !open && setDeletingDep(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>배포 삭제</DialogTitle>
            <DialogDescription>
              <span className="font-semibold">{deletingDep?.model_name}</span> 배포의 K8s 리소스
              (Deployment / Service / Ingress) 와 DB row를 모두 삭제합니다. 되돌릴 수 없습니다.
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
    </div>
  );
}
