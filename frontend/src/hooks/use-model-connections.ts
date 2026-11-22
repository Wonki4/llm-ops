"use client";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface ModelConnection {
  id: string; model_name: string; model: string; provider: string; api_base: string | null;
  api_version: string | null; db_model: boolean; editable: boolean; origin: "cluster" | "cloud" | "unknown";
  managed: {kind: "deployment" | "external"; id: string; cluster_id: string | null; namespace: string} | null;
  access_groups: string[]; prices: Record<string, number | null>;
}
export function useModelConnections(modelName?: string) {
  return useQuery({queryKey: ["model-connections", modelName ?? "all"],
    queryFn: () => apiFetch<{connections: ModelConnection[]}>(`/api/admin/model-connections${modelName ? `?model_name=${encodeURIComponent(modelName)}` : ""}`).then(r => r.connections)});
}
