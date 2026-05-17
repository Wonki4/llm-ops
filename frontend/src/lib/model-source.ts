import type { ModelWithCatalog } from "@/types";

export type ModelSource = "k8s" | "external_api" | "catalog_only";

export interface ModelSourceInfo {
  source: ModelSource;
  label: string;
  description: string;
}

const META: Record<ModelSource, ModelSourceInfo> = {
  k8s: {
    source: "k8s",
    label: "자체 배포",
    description: "우리 K8s 클러스터에서 직접 운영하는 vLLM 배포",
  },
  external_api: {
    source: "external_api",
    label: "외부 API",
    description: "LiteLLM에 등록된 외부 모델 (OpenAI, Azure, Bedrock 등)",
  },
  catalog_only: {
    source: "catalog_only",
    label: "카탈로그",
    description: "라우팅 없이 카탈로그에만 등록된 모델",
  },
};

export function classifyModel(model: ModelWithCatalog): ModelSourceInfo {
  // K8s wins: a catalog entry that points at a deployment is always "our" deployment,
  // even if it also happens to be registered in LiteLLM (which it should be).
  if (model.catalog?.deployment_id) return META.k8s;
  if (model.litellm_info) return META.external_api;
  return META.catalog_only;
}
