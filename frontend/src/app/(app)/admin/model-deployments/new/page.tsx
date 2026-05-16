"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Save, ArrowLeft, Server } from "lucide-react";
import { toast } from "sonner";

import { useCreateModelDeployment, useK8sClusters } from "@/hooks/use-api";
import type { CreateModelDeploymentRequest } from "@/types";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
  node_selector: string;
  tolerations: string;
  pvc_name: string;
  pvc_mount_path: string;
  model_path: string;
  vllm_extra_args: string;
  env: string;
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
}

const INITIAL: FormState = {
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

function buildBody(form: FormState): CreateModelDeploymentRequest | string {
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
      form.vllm_extra_args.split("\n").map(s => s.trim()).filter(Boolean) || null,
    env: (env as Record<string, string>) ?? null,
    ingress_host: form.ingress_host.trim(),
    ingress_path: form.ingress_path.trim() || "/",
    ingress_class: form.ingress_class.trim() || "nginx",
  };
}

export default function NewDeploymentPage() {
  const router = useRouter();
  const { data: clusters } = useK8sClusters();
  const createMut = useCreateModelDeployment();
  const [form, setForm] = useState<FormState>(INITIAL);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm(f => ({ ...f, [key]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const body = buildBody(form);
    if (typeof body === "string") {
      toast.error(body);
      return;
    }
    createMut.mutate(body, {
      onSuccess: () => {
        toast.success("배포가 생성되었습니다. Reconciler가 K8s 상태를 곧 가져옵니다.");
        router.push("/admin/model-deployments");
      },
      onError: e => toast.error(e instanceof Error ? e.message : "생성 실패"),
    });
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/admin/model-deployments">
          <ArrowLeft className="size-4" />
          돌아가기
        </Link>
      </Button>

      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Server className="size-6" />
          새 모델 배포
        </h1>
        <p className="text-muted-foreground mt-1 text-sm">
          K8s 클러스터에 vLLM 모델 서버를 배포합니다. Reconciler가 60초마다 상태를 갱신하고
          Ready 진입 시 LiteLLM에 자동 등록 + catalog row를 생성합니다.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">기본</CardTitle>
            <CardDescription>모델명과 배포 대상 클러스터</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>모델명 *</Label>
                <Input
                  value={form.model_name}
                  onChange={e => set("model_name", e.target.value)}
                  placeholder="예: llama-3-8b-instruct"
                />
              </div>
              <div className="space-y-1">
                <Label>클러스터 *</Label>
                <Select
                  value={form.cluster_id}
                  onValueChange={v => {
                    set("cluster_id", v);
                    const c = clusters?.find(cc => cc.id === v);
                    if (c && (!form.namespace || form.namespace === "default")) {
                      set("namespace", c.default_namespace);
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="K8s 클러스터 선택" />
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
            <div className="space-y-1">
              <Label>네임스페이스</Label>
              <Input value={form.namespace} onChange={e => set("namespace", e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>이미지</Label>
              <Input value={form.image} onChange={e => set("image", e.target.value)} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">리소스</CardTitle>
            <CardDescription>레플리카, GPU, CPU/Memory 요청·제한</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label>Replicas</Label>
                <Input
                  type="number"
                  min="0"
                  value={form.replicas}
                  onChange={e => set("replicas", e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>GPU 개수</Label>
                <Input
                  type="number"
                  min="0"
                  value={form.gpu_count}
                  onChange={e => set("gpu_count", e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>GPU 리소스 키</Label>
                <Input
                  value={form.gpu_resource_key}
                  onChange={e => set("gpu_resource_key", e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>CPU request</Label>
                <Input
                  value={form.cpu_request}
                  onChange={e => set("cpu_request", e.target.value)}
                  placeholder="예: 4"
                />
              </div>
              <div className="space-y-1">
                <Label>CPU limit</Label>
                <Input
                  value={form.cpu_limit}
                  onChange={e => set("cpu_limit", e.target.value)}
                  placeholder="예: 8"
                />
              </div>
              <div className="space-y-1">
                <Label>Memory request</Label>
                <Input
                  value={form.memory_request}
                  onChange={e => set("memory_request", e.target.value)}
                  placeholder="예: 16Gi"
                />
              </div>
              <div className="space-y-1">
                <Label>Memory limit</Label>
                <Input
                  value={form.memory_limit}
                  onChange={e => set("memory_limit", e.target.value)}
                  placeholder="예: 32Gi"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>node_selector (JSON)</Label>
                <textarea
                  value={form.node_selector}
                  onChange={e => set("node_selector", e.target.value)}
                  rows={3}
                  placeholder='{"gpu": "a100"}'
                  className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
                />
              </div>
              <div className="space-y-1">
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">모델 / vLLM</CardTitle>
            <CardDescription>가중치 위치와 vLLM 인자</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>PVC 이름</Label>
                <Input
                  value={form.pvc_name}
                  onChange={e => set("pvc_name", e.target.value)}
                  placeholder="예: model-weights"
                />
              </div>
              <div className="space-y-1">
                <Label>PVC 마운트 경로</Label>
                <Input
                  value={form.pvc_mount_path}
                  onChange={e => set("pvc_mount_path", e.target.value)}
                  placeholder="예: /mnt/models"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label>model_path *</Label>
              <Input
                value={form.model_path}
                onChange={e => set("model_path", e.target.value)}
                placeholder="vLLM --model 인자. 보통 PVC 마운트 하위 경로 (예: /mnt/models/llama-3-8b)"
              />
            </div>
            <div className="space-y-1">
              <Label>vLLM 추가 args (줄바꿈 구분)</Label>
              <textarea
                value={form.vllm_extra_args}
                onChange={e => set("vllm_extra_args", e.target.value)}
                rows={3}
                placeholder={"--tensor-parallel-size\n2\n--gpu-memory-utilization\n0.9"}
                className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label>env (JSON)</Label>
              <textarea
                value={form.env}
                onChange={e => set("env", e.target.value)}
                rows={2}
                placeholder='{"HF_HUB_OFFLINE":"1"}'
                className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs"
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Ingress</CardTitle>
            <CardDescription>외부 노출 호스트와 nginx 설정</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label>Ingress host *</Label>
                <Input
                  value={form.ingress_host}
                  onChange={e => set("ingress_host", e.target.value)}
                  placeholder="예: llama.llm.example.com"
                />
              </div>
              <div className="space-y-1">
                <Label>Ingress path</Label>
                <Input
                  value={form.ingress_path}
                  onChange={e => set("ingress_path", e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>Ingress class</Label>
                <Input
                  value={form.ingress_class}
                  onChange={e => set("ingress_class", e.target.value)}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-2">
          <Button variant="outline" asChild>
            <Link href="/admin/model-deployments">취소</Link>
          </Button>
          <Button type="submit" disabled={createMut.isPending}>
            {createMut.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            배포 생성
          </Button>
        </div>
      </form>
    </div>
  );
}
