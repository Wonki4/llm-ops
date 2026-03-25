"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  User,
  Team,
  DiscoverTeam,
  TeamDetail,
  TeamMembersResponse,
  ApiKey,
  ModelWithCatalog,
  ModelCatalog,
  ModelStatusHistory,
  ModelStatusHistorySummary,
  TeamJoinRequest,
  CreateKeyRequest,
  CreateJoinRequestBody,
  CreateBudgetRequestBody,
  ReviewRequestBody,
  CreateModelCatalogRequest,
  UpdateModelCatalogRequest,
  BudgetListResponse,
  BudgetDetails,
} from "@/types";

// ─── Query Keys ──────────────────────────────────────────────

export const queryKeys = {
  me: ["me"] as const,
  myTeams: ["teams", "mine"] as const,
  teamDetail: (teamId: string) => ["teams", teamId] as const,
  discoverTeams: ["teams", "discover"] as const,
  myKeys: (teamId?: string) => ["keys", { teamId }] as const,
  joinRequests: (teamId?: string, status?: string) =>
    ["join-requests", { teamId, status }] as const,
  models: ["models"] as const,
  modelCatalog: ["models", "catalog"] as const,
  modelStatusHistory: (catalogId: string) => ["models", "catalog", catalogId, "history"] as const,
  allStatusHistory: (filters?: Record<string, string>) => ["models", "catalog", "all-history", filters ?? {}] as const,
  historySummary: (filters?: Record<string, string>) => ["models", "catalog", "history-summary", filters ?? {}] as const,
  teamMembers: (teamId: string, page: number, pageSize: number, search: string) =>
    ["teams", teamId, "members", { page, pageSize, search }] as const,
};

// ─── User ────────────────────────────────────────────────────

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => apiFetch<User>("/api/me"),
  });
}

// ─── Teams ───────────────────────────────────────────────────

export function useMyTeams() {
  return useQuery({
    queryKey: queryKeys.myTeams,
    queryFn: () => apiFetch<{ teams: Team[] }>("/api/teams").then((r) => r.teams),
  });
}

export function useTeamDetail(teamId: string) {
  return useQuery({
    queryKey: queryKeys.teamDetail(teamId),
    queryFn: () => apiFetch<TeamDetail>(`/api/teams/${teamId}`),
    enabled: !!teamId,
  });
}

export function useTeamMembers(
  teamId: string,
  page: number,
  pageSize: number,
  search: string,
  enabled: boolean = true,
) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (search) params.set("search", search);

  return useQuery({
    queryKey: queryKeys.teamMembers(teamId, page, pageSize, search),
    queryFn: () =>
      apiFetch<TeamMembersResponse>(`/api/teams/${teamId}/members?${params.toString()}`),
    enabled: enabled && !!teamId,
  });
}

export function useChangeMemberRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, userId, role }: { teamId: string; userId: string; role: "admin" | "member" }) =>
      apiFetch<{ status: string }>(`/api/teams/${teamId}/members/role`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, role }),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.teamDetail(variables.teamId) });
      qc.invalidateQueries({ queryKey: ["teams", variables.teamId, "members"] });
    },
  });
}

export function useDiscoverTeams() {
  return useQuery({
    queryKey: queryKeys.discoverTeams,
    queryFn: () =>
      apiFetch<{ teams: DiscoverTeam[] }>("/api/teams/discover").then((r) => r.teams),
  });
}

// ─── Keys ────────────────────────────────────────────────────

export function useMyKeys(teamId?: string) {
  const params = teamId ? `?team_id=${teamId}` : "";
  return useQuery({
    queryKey: queryKeys.myKeys(teamId),
    queryFn: () =>
      apiFetch<{ keys: ApiKey[] }>(`/api/keys${params}`).then((r) => r.keys),
  });
}

export function useCreateKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateKeyRequest) =>
      apiFetch<ApiKey>("/api/keys", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.myKeys(variables.team_id) });
      qc.invalidateQueries({ queryKey: queryKeys.myKeys() });
    },
  });
}

export function useDeleteKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyHash: string) =>
      apiFetch<{ deleted: boolean }>(`/api/keys/${keyHash}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys"] });
    },
  });
}

// ─── Team Join Requests ──────────────────────────────────────

export function useJoinRequests(teamId?: string, status?: string) {
  const params = new URLSearchParams();
  if (teamId) params.set("team_id", teamId);
  if (status) params.set("status_filter", status);
  const qs = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: queryKeys.joinRequests(teamId, status),
    queryFn: () =>
      apiFetch<{ requests: TeamJoinRequest[] }>(`/api/team-requests${qs}`).then(
        (r) => r.requests,
      ),
  });
}

export function useCreateJoinRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateJoinRequestBody) =>
      apiFetch<TeamJoinRequest>("/api/team-requests", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["join-requests"] });
      qc.invalidateQueries({ queryKey: queryKeys.discoverTeams });
    },
  });
}

export function useCreateBudgetRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBudgetRequestBody) =>
      apiFetch<TeamJoinRequest>("/api/team-requests/budget", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["join-requests"] });
    },
  });
}

export function useApproveRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, body }: { requestId: string; body?: ReviewRequestBody }) =>
      apiFetch<{ status: string }>(`/api/team-requests/${requestId}/approve`, {
        method: "POST",
        body: JSON.stringify(body || {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["join-requests"] });
    },
  });
}

export function useRejectRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, body }: { requestId: string; body?: ReviewRequestBody }) =>
      apiFetch<{ status: string }>(`/api/team-requests/${requestId}/reject`, {
        method: "POST",
        body: JSON.stringify(body || {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["join-requests"] });
    },
  });
}

// ─── Models ──────────────────────────────────────────────────

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: () =>
      apiFetch<{ models: ModelWithCatalog[] }>("/api/models").then((r) => r.models),
  });
}

export function useModelCatalog() {
  return useQuery({
    queryKey: queryKeys.modelCatalog,
    queryFn: () =>
      apiFetch<{ catalog: ModelCatalog[] }>("/api/models/catalog").then((r) => r.catalog),
  });
}

export function useModelStatusHistory(catalogId?: string) {
  return useQuery({
    queryKey: queryKeys.modelStatusHistory(catalogId ?? ""),
    queryFn: () =>
      apiFetch<{ history: ModelStatusHistory[] }>(`/api/models/catalog/${catalogId}/history`).then(
        (r) => r.history,
      ),
    enabled: !!catalogId,
  });
}

export interface AllHistoryFilters {
  model_name?: string;
  status_filter?: string;
  changed_by?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export function useAllModelStatusHistory(filters: AllHistoryFilters = {}) {
  const params = new URLSearchParams();
  if (filters.model_name) params.set("model_name", filters.model_name);
  if (filters.status_filter) params.set("status_filter", filters.status_filter);
  if (filters.changed_by) params.set("changed_by", filters.changed_by);
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  const qs = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: queryKeys.allStatusHistory(Object.fromEntries(params)),
    queryFn: () =>
      apiFetch<{ history: ModelStatusHistory[]; total: number }>(
        `/api/models/catalog/history${qs}`,
      ),
  });
}

// ─── History Summary ────────────────────────────────────────

export interface HistorySummaryFilters {
  date_from?: string;
  date_to?: string;
  bucket?: "day" | "month";
  top_n?: number;
}

export function useModelStatusHistorySummary(filters: HistorySummaryFilters = {}) {
  const params = new URLSearchParams();
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (filters.bucket) params.set("bucket", filters.bucket);
  if (filters.top_n) params.set("top_n", String(filters.top_n));
  const qs = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: queryKeys.historySummary(Object.fromEntries(params)),
    queryFn: () =>
      apiFetch<ModelStatusHistorySummary>(
        `/api/models/catalog/history/summary${qs}`,
      ),
  });
}

export function useCreateCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateModelCatalogRequest) =>
      apiFetch<ModelCatalog>("/api/models/catalog", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelCatalog });
      qc.invalidateQueries({ queryKey: queryKeys.models });
    },
  });
}

export function useUpdateCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ catalogId, body }: { catalogId: string; body: UpdateModelCatalogRequest }) =>
      apiFetch<ModelCatalog>(`/api/models/catalog/${catalogId}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.modelCatalog });
      qc.invalidateQueries({ queryKey: queryKeys.models });
      qc.invalidateQueries({ queryKey: queryKeys.modelStatusHistory(variables.catalogId) });
    },
  });
}

export function useDeleteCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalogId: string) =>
      apiFetch<{ deleted: boolean }>(`/api/models/catalog/${catalogId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelCatalog });
      qc.invalidateQueries({ queryKey: queryKeys.models });
    },
  });
}

// ─── Budgets ────────────────────────────────────────────────────

export function useBudgets(page: number, pageSize: number, searchId: string, searchAmount: string) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (searchId) params.set("search_id", searchId);
  if (searchAmount) params.set("search_amount", searchAmount);

  return useQuery({
    queryKey: ["budgets", { page, pageSize, searchId, searchAmount }],
    queryFn: () => apiFetch<BudgetListResponse>(`/api/budgets?${params.toString()}`),
  });
}

export function useBudgetDetails(budgetId: string | null) {
  return useQuery({
    queryKey: ["budgets", budgetId, "details"],
    queryFn: () => apiFetch<BudgetDetails>(`/api/budgets/${budgetId}/details`),
    enabled: !!budgetId,
  });
}
