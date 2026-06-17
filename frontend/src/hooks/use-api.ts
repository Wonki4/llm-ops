"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
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
  RedisCatalogEntry,
  RedisCatalogListResponse,
  AdminUserListResponse,
  AdminUserDetail,
  Announcement,
  CreateAnnouncementRequest,
  UpdateAnnouncementRequest,
  BenchmarkRun,
  BenchmarkListResponse,
  CreateBenchmarkRequest,
  ModelDeployment,
  K8sClusterSummary,
  ClusterTestResult,
  ArgocdConnectionSummary,
  ArgocdTestResult,
  LlmdStackSummary,
} from "@/types";

// ─── Query Keys ──────────────────────────────────────────────

export const queryKeys = {
  me: ["me"] as const,
  myTeams: ["teams", "mine"] as const,
  teamDetail: (teamId: string) => ["teams", teamId] as const,
  discoverTeams: ["teams", "discover"] as const,
  myKeys: (teamId?: string) => ["keys", { teamId }] as const,
  joinRequests: (teamId?: string, status?: string, mineOnly?: boolean) =>
    ["join-requests", { teamId, status, mineOnly }] as const,
  models: ["models"] as const,
  modelCatalog: ["models", "catalog"] as const,
  modelStatusHistory: (catalogId: string) => ["models", "catalog", catalogId, "history"] as const,
  allStatusHistory: (filters?: Record<string, string>) => ["models", "catalog", "all-history", filters ?? {}] as const,
  historySummary: (filters?: Record<string, string>) => ["models", "catalog", "history-summary", filters ?? {}] as const,
  teamMembers: (
    teamId: string,
    page: number,
    pageSize: number,
    search: string,
    sortBy: string,
    sortDir: string,
  ) => ["teams", teamId, "members", { page, pageSize, search, sortBy, sortDir }] as const,
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
  sortBy: "user_id" | "spend" | "budget" | "key_count" = "user_id",
  sortDir: "asc" | "desc" = "asc",
  enabled: boolean = true,
) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (search) params.set("search", search);
  params.set("sort_by", sortBy);
  params.set("sort_dir", sortDir);

  return useQuery({
    queryKey: queryKeys.teamMembers(teamId, page, pageSize, search, sortBy, sortDir),
    queryFn: () =>
      apiFetch<TeamMembersResponse>(`/api/teams/${teamId}/members?${params.toString()}`),
    enabled: enabled && !!teamId,
  });
}

export function useRemoveTeamMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, userId }: { teamId: string; userId: string }) =>
      apiFetch<{ status: string }>(`/api/teams/${teamId}/members/${userId}`, {
        method: "DELETE",
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.teamDetail(variables.teamId) });
      qc.invalidateQueries({ queryKey: ["teams", variables.teamId, "members"] });
    },
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

export function useChangeMemberBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, userId, maxBudget }: { teamId: string; userId: string; maxBudget: number }) =>
      apiFetch<{ status: string }>(`/api/teams/${teamId}/members/${userId}/budget`, {
        method: "PUT",
        body: JSON.stringify({ max_budget: maxBudget }),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.teamDetail(variables.teamId) });
      qc.invalidateQueries({ queryKey: ["teams", variables.teamId, "members"] });
    },
  });
}

export function useSetMemberExpiry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, userId, expiresAt }: { teamId: string; userId: string; expiresAt: string | null }) =>
      apiFetch<{ status: string }>(`/api/teams/${teamId}/members/${userId}/expiry`, {
        method: "PUT",
        body: JSON.stringify({ expires_at: expiresAt }),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.teamDetail(variables.teamId) });
      qc.invalidateQueries({ queryKey: ["teams", variables.teamId, "members"] });
    },
  });
}

export function useUpdateMemberKeyLimits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      teamId,
      userId,
      token,
      tpmLimit,
      rpmLimit,
    }: {
      teamId: string;
      userId: string;
      token: string;
      tpmLimit: number | null;
      rpmLimit: number | null;
    }) =>
      apiFetch<{ status: string }>(
        `/api/teams/${teamId}/members/${userId}/keys/${token}/limits`,
        {
          method: "PATCH",
          body: JSON.stringify({ tpm_limit: tpmLimit, rpm_limit: rpmLimit }),
        },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["teams", variables.teamId, "members"] });
    },
  });
}

export function useUpdateTeamSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ teamId, body }: { teamId: string; body: { default_member_budget?: number | null; membership_duration?: string | null; default_tpm_limit?: number | null; default_rpm_limit?: number | null; model_tpm_limit?: Record<string, number>; model_rpm_limit?: Record<string, number> } }) =>
      apiFetch<{ status: string }>(`/api/teams/${teamId}/settings`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.teamDetail(variables.teamId) });
      qc.invalidateQueries({ queryKey: queryKeys.myTeams });
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

export function useRevealKey() {
  return useMutation({
    mutationFn: (keyHash: string) =>
      apiFetch<{ key: string }>(`/api/keys/${keyHash}/reveal`),
  });
}

export function useDeleteKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyHash: string) =>
      apiFetch<{ deleted: boolean }>(`/api/keys/${keyHash}`, { method: "DELETE" }),
    onSuccess: () => {
      // /keys 페이지 (useMyKeys → queryKey ["keys", { teamId }])
      qc.invalidateQueries({ queryKey: ["keys"] });
      // 팀 상세 화면도 my_keys를 들고 있으니 같이 새로고침
      qc.invalidateQueries({ queryKey: ["teams"] });
    },
  });
}

// ─── Team Join Requests ──────────────────────────────────────

export function useJoinRequests(teamId?: string, status?: string, mineOnly?: boolean) {
  const params = new URLSearchParams();
  if (teamId) params.set("team_id", teamId);
  if (status) params.set("status_filter", status);
  if (mineOnly) params.set("mine_only", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: queryKeys.joinRequests(teamId, status, mineOnly),
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

export function useBudgets(page: number, pageSize: number, searchId: string, searchAmount: string, orphansOnly: boolean = false) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (searchId) params.set("search_id", searchId);
  if (searchAmount) params.set("search_amount", searchAmount);
  if (orphansOnly) params.set("orphans_only", "true");

  return useQuery({
    queryKey: ["budgets", { page, pageSize, searchId, searchAmount, orphansOnly }],
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

export function useOrphanBudgets() {
  return useQuery({
    queryKey: ["budgets", "orphans"],
    queryFn: () => apiFetch<{ orphans: import("@/types").OrphanBudget[]; count: number }>("/api/budgets/orphans"),
  });
}

export function useDeleteOrphanBudgets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<{ deleted: number }>("/api/budgets/orphans", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budgets"] });
    },
  });
}

export function useDeleteBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (budgetId: string) =>
      apiFetch<{ deleted: boolean }>(`/api/budgets/${budgetId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budgets"] });
    },
  });
}

export function useDeleteBudgetsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (budgetIds: string[]) =>
      apiFetch<{ deleted: number; skipped: number }>("/api/budgets/batch", {
        method: "DELETE",
        body: JSON.stringify(budgetIds),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budgets"] });
    },
  });
}

// ─── Portal Settings ────────────────────────────────────────────

export interface PortalSettings {
  default_tpm_limit: number;
  default_rpm_limit: number;
  default_team_id: string;
  hidden_teams: string[];
}

export function usePortalSettings() {
  return useQuery({
    queryKey: ["portal-settings"],
    queryFn: () => apiFetch<PortalSettings>("/api/settings"),
  });
}

export function useUpdatePortalSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<PortalSettings>) =>
      apiFetch<PortalSettings>("/api/settings", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portal-settings"] });
    },
  });
}

// ─── Default Team Rules ──────────────────────────────────────────

export interface DefaultTeamRule {
  prefix: string;
  teams: string[];
}

export function useDefaultTeamRules() {
  return useQuery({
    queryKey: ["default-team-rules"],
    queryFn: () =>
      apiFetch<{ rules: DefaultTeamRule[] }>("/api/settings/default-team-rules").then(
        (r) => r.rules,
      ),
  });
}

export function useUpdateDefaultTeamRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rules: DefaultTeamRule[]) =>
      apiFetch<{ rules: DefaultTeamRule[] }>("/api/settings/default-team-rules", {
        method: "PUT",
        body: JSON.stringify(rules),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["default-team-rules"] });
    },
  });
}

// ─── Hidden Teams ──────────────────────────────────────────────

export function useHiddenTeams() {
  return useQuery({
    queryKey: ["hidden-teams"],
    queryFn: () =>
      apiFetch<{ hidden_teams: string[] }>("/api/settings/hidden-teams").then(
        (r) => r.hidden_teams,
      ),
  });
}

export function useUpdateHiddenTeams() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (teamIds: string[]) =>
      apiFetch<{ hidden_teams: string[] }>("/api/settings/hidden-teams", {
        method: "PUT",
        body: JSON.stringify(teamIds),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hidden-teams"] });
      qc.invalidateQueries({ queryKey: ["portal-settings"] });
    },
  });
}

// ─── Redis Catalog ──────────────────────────────────────────────

export function useCatalogList() {
  return useQuery({
    queryKey: ["catalog-list"],
    queryFn: () => apiFetch<{ catalogs: string[] }>("/api/catalog/catalogs"),
  });
}

export function useUpdateCatalogList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalogs: string[]) =>
      apiFetch<{ catalogs: string[] }>("/api/catalog/catalogs", {
        method: "PUT",
        body: JSON.stringify(catalogs),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["catalog-list"] });
    },
  });
}

export function useRedisCatalog(catalog: string) {
  return useQuery({
    queryKey: ["redis-catalog", catalog],
    queryFn: () => apiFetch<RedisCatalogListResponse>(`/api/catalog?catalog=${encodeURIComponent(catalog)}`),
    enabled: !!catalog,
  });
}

export function useCreateRedisCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ catalog, body }: { catalog: string; body: { display_name: string; entry: Omit<RedisCatalogEntry, "display_name"> } }) =>
      apiFetch<RedisCatalogEntry>(`/api/catalog?catalog=${encodeURIComponent(catalog)}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["redis-catalog"] });
    },
  });
}

export function useUpdateRedisCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ catalog, displayName, body }: { catalog: string; displayName: string; body: { entry?: Omit<RedisCatalogEntry, "display_name">; new_display_name?: string } }) =>
      apiFetch<RedisCatalogEntry>(`/api/catalog/entry/${encodeURIComponent(displayName)}?catalog=${encodeURIComponent(catalog)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["redis-catalog"] });
    },
  });
}

export function useDeleteRedisCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ catalog, displayName }: { catalog: string; displayName: string }) =>
      apiFetch<{ deleted: boolean }>(`/api/catalog/entry/${encodeURIComponent(displayName)}?catalog=${encodeURIComponent(catalog)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["redis-catalog"] });
    },
  });
}

export function useSyncCatalogToRedis() {
  return useMutation({
    mutationFn: ({ catalog }: { catalog: string }) => {
      const params = new URLSearchParams({ catalog });
      return apiFetch<{ synced: number }>(`/api/catalog/sync-to-redis?${params}`, { method: "POST" });
    },
  });
}

// ─── Per-model Redis cache (split-pane right panel) ─────────────

export interface ModelCacheEntry {
  model: string;
  apiBase: string;
  apiKey: string;
  options?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ModelCacheResponse {
  model_name: string;
  suffixes: string[];
  entries: Record<string, ModelCacheEntry | null>;
}

export function useModelCache(modelName: string | null) {
  return useQuery({
    queryKey: ["model-cache", modelName],
    queryFn: () =>
      apiFetch<ModelCacheResponse>(`/api/models/${encodeURIComponent(modelName!)}/cache`),
    enabled: !!modelName,
  });
}

export interface ModelSummaryBenchmark {
  tool: string;
  result: Record<string, unknown> | null;
  params: Record<string, unknown> | null;
  finished_at: string | null;
}

export interface ModelSummary {
  model_name: string;
  catalog: {
    display_name?: string;
    description?: string | null;
    icon_url?: string | null;
    status?: string;
    default_input_cost_per_token?: number | null;
    default_output_cost_per_token?: number | null;
  } | null;
  litellm_info: Record<string, unknown> | null;
  cost_schedule: Array<{
    days_of_week: number[];
    hour_start_utc: number;
    hour_end_utc: number;
    input_cost_per_token: number;
    output_cost_per_token: number;
    priority: number;
  }>;
  performance: ModelSummaryBenchmark | null;
  accuracy: ModelSummaryBenchmark | null;
}

export function useModelSummary(modelName: string | null) {
  return useQuery({
    queryKey: ["model-summary", modelName],
    queryFn: () =>
      apiFetch<ModelSummary>(`/api/models/${encodeURIComponent(modelName!)}/summary`),
    enabled: !!modelName,
  });
}

export function useSetModelCacheEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      modelName,
      suffix,
      entry,
    }: {
      modelName: string;
      suffix: string;
      entry: ModelCacheEntry;
    }) =>
      apiFetch<ModelCacheEntry>(
        `/api/models/${encodeURIComponent(modelName)}/cache/${encodeURIComponent(suffix)}`,
        { method: "PUT", body: JSON.stringify(entry) },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["model-cache", vars.modelName] });
    },
  });
}

export function useDeleteModelCacheEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ modelName, suffix }: { modelName: string; suffix: string }) =>
      apiFetch<{ deleted: boolean }>(
        `/api/models/${encodeURIComponent(modelName)}/cache/${encodeURIComponent(suffix)}`,
        { method: "DELETE" },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["model-cache", vars.modelName] });
    },
  });
}

// ─── Admin Users ────────────────────────────────────────────────

export function useAdminUsers(
  page: number,
  pageSize: number,
  search: string,
  role: string,
) {
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (search) params.set("search", search);
  if (role) params.set("role", role);

  return useQuery({
    queryKey: ["admin-users", { page, pageSize, search, role }],
    queryFn: () => apiFetch<AdminUserListResponse>(`/api/admin/users?${params.toString()}`),
  });
}

export function useAdminUserDetail(userId: string) {
  return useQuery({
    queryKey: ["admin-users", userId, "detail"],
    queryFn: () => apiFetch<AdminUserDetail>(`/api/admin/users/${encodeURIComponent(userId)}`),
    enabled: !!userId,
  });
}

export function useAdminUpdateKeyLimits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      token,
      tpmLimit,
      rpmLimit,
      modelTpmLimit,
      modelRpmLimit,
    }: {
      userId: string;
      token: string;
      tpmLimit: number | null;
      rpmLimit: number | null;
      modelTpmLimit?: Record<string, number>;
      modelRpmLimit?: Record<string, number>;
    }) =>
      apiFetch<{ status: string }>(
        `/api/admin/users/${encodeURIComponent(userId)}/keys/${encodeURIComponent(token)}/limits`,
        {
          method: "PATCH",
          body: JSON.stringify({
            tpm_limit: tpmLimit,
            rpm_limit: rpmLimit,
            ...(modelTpmLimit !== undefined ? { model_tpm_limit: modelTpmLimit } : {}),
            ...(modelRpmLimit !== undefined ? { model_rpm_limit: modelRpmLimit } : {}),
          }),
        },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["admin-users", variables.userId, "detail"] });
    },
  });
}

export function useAdminRemoveUserFromTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, teamId }: { userId: string; teamId: string }) =>
      apiFetch<{ status: string }>(
        `/api/admin/users/${encodeURIComponent(userId)}/teams/${encodeURIComponent(teamId)}`,
        { method: "DELETE" },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["admin-users", variables.userId, "detail"] });
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

export function useAdminAssignUserToTeam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      teamId,
      role,
    }: {
      userId: string;
      teamId: string;
      role: "user" | "admin";
    }) =>
      apiFetch<{ status: string }>(
        `/api/admin/users/${encodeURIComponent(userId)}/teams`,
        {
          method: "POST",
          body: JSON.stringify({ team_id: teamId, role }),
        },
      ),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["admin-users", variables.userId, "detail"] });
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

// ─── Announcements ──────────────────────────────────────────────

export function useAnnouncements(includeUnpublished: boolean = false) {
  const qs = includeUnpublished ? "?include_unpublished=true" : "";
  return useQuery({
    queryKey: ["announcements", { includeUnpublished }],
    queryFn: () =>
      apiFetch<{ announcements: Announcement[] }>(`/api/announcements${qs}`).then(
        (r) => r.announcements,
      ),
  });
}

export function useCreateAnnouncement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAnnouncementRequest) =>
      apiFetch<Announcement>("/api/announcements", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["announcements"] }),
  });
}

export function useUpdateAnnouncement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateAnnouncementRequest }) =>
      apiFetch<Announcement>(`/api/announcements/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["announcements"] }),
  });
}

export function useDeleteAnnouncement() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ status: string }>(`/api/announcements/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["announcements"] }),
  });
}

// ─── Model Deployments (serving) ─────────────────────────────

export function useModelDeployments() {
  return useQuery({
    queryKey: ["model-deployments"],
    queryFn: () =>
      apiFetch<{ deployments: ModelDeployment[] }>("/api/model-deployments").then(
        (r) => r.deployments,
      ),
  });
}

// ─── Benchmark Runs ─────────────────────────────────────────

export function useBenchmarks(filters: {
  model_name?: string;
  tool?: string;
  status?: string;
  limit?: number;
}) {
  const params = new URLSearchParams();
  if (filters.model_name) params.set("model_name", filters.model_name);
  if (filters.tool) params.set("tool", filters.tool);
  if (filters.status) params.set("status_filter", filters.status);
  if (filters.limit) params.set("limit", String(filters.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: ["benchmarks", filters],
    queryFn: () =>
      apiFetch<BenchmarkListResponse>(`/api/benchmarks${qs}`).then((r) => r.runs),
    // Refresh while jobs are still pending/running.
    refetchInterval: 5000,
  });
}

export function useBenchmark(id: string) {
  return useQuery({
    queryKey: ["benchmarks", id],
    queryFn: () => apiFetch<BenchmarkRun>(`/api/benchmarks/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data as BenchmarkRun | undefined;
      if (!data) return 5000;
      return data.status === "pending" || data.status === "running" ? 5000 : false;
    },
  });
}

export function useCancelBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<BenchmarkRun>(`/api/benchmarks/${id}/cancel`, { method: "POST" }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["benchmarks"] });
      qc.invalidateQueries({ queryKey: ["benchmarks", id] });
    },
  });
}

export function useBenchmarkBulk(ids: string[]) {
  // Fetch N runs in parallel. Reuses the per-run query cache so opening a
  // comparison after viewing a detail page hits cache without re-fetching.
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["benchmarks", id],
      queryFn: () => apiFetch<BenchmarkRun>(`/api/benchmarks/${id}`),
      enabled: !!id,
    })),
  });
  return {
    runs: results.map((r) => r.data).filter((r): r is BenchmarkRun => !!r),
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
  };
}

export function useCreateBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBenchmarkRequest) =>
      apiFetch<BenchmarkRun>("/api/benchmarks", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["benchmarks"] }),
  });
}

// ─── K8s Clusters (admin) ──────────────────────────────────────────

export interface CreateK8sClusterBody {
  name: string;
  context: string;
  namespace?: string;
  kubeconfig: string;
  description?: string | null;
  is_default?: boolean;
  default_nfs_server?: string | null;
  default_nfs_path?: string | null;
  default_nfs_mount_path?: string | null;
}

export type UpdateK8sClusterBody = Partial<CreateK8sClusterBody>;

export function useK8sClusters() {
  return useQuery({
    queryKey: ["k8s-clusters"],
    queryFn: () =>
      apiFetch<{ clusters: K8sClusterSummary[] }>("/api/admin/k8s-clusters").then(
        (r) => r.clusters,
      ),
  });
}

export function useCreateK8sCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateK8sClusterBody) =>
      apiFetch<K8sClusterSummary>("/api/admin/k8s-clusters", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["k8s-clusters"] }),
  });
}

export function useUpdateK8sCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateK8sClusterBody }) =>
      apiFetch<K8sClusterSummary>(`/api/admin/k8s-clusters/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["k8s-clusters"] }),
  });
}

export function useDeleteK8sCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ ok: boolean }>(`/api/admin/k8s-clusters/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["k8s-clusters"] }),
  });
}

// ─── Benchmark manifest preview ────────────────────────────────────

export interface BenchmarkPreviewManifest {
  kind: string;
  name: string;
  yaml: string;
}

export interface BenchmarkPreviewResponse {
  manifests: BenchmarkPreviewManifest[];
  note: string | null;
}

/** Render the exact K8s manifests a benchmark run would apply (no writes). */
export function useBenchmarkPreview() {
  return useMutation({
    mutationFn: (body: CreateBenchmarkRequest) =>
      apiFetch<BenchmarkPreviewResponse>("/api/benchmarks/preview", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}

/** Test an unsaved kubeconfig+context (before the cluster is created). */
export function useTestK8sCluster() {
  return useMutation({
    mutationFn: (body: { kubeconfig: string; context: string }) =>
      apiFetch<ClusterTestResult>("/api/admin/k8s-clusters/test", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}

/** Test a stored cluster's connection by id. */
export function useTestSavedK8sCluster() {
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<ClusterTestResult>(`/api/admin/k8s-clusters/${id}/test`, { method: "POST" }),
  });
}

// ─── ArgoCD connections ────────────────────────────────────────────

export interface CreateArgocdConnectionBody {
  name: string;
  server_url: string;
  token: string;
  insecure_skip_verify?: boolean;
  description?: string | null;
  is_default?: boolean;
}

export type UpdateArgocdConnectionBody = Partial<CreateArgocdConnectionBody>;

export function useArgocdConnections() {
  return useQuery({
    queryKey: ["argocd-connections"],
    queryFn: () =>
      apiFetch<{ connections: ArgocdConnectionSummary[] }>("/api/admin/argocd-connections").then(
        (r) => r.connections,
      ),
  });
}

export function useCreateArgocdConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateArgocdConnectionBody) =>
      apiFetch<ArgocdConnectionSummary>("/api/admin/argocd-connections", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["argocd-connections"] }),
  });
}

export function useUpdateArgocdConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateArgocdConnectionBody }) =>
      apiFetch<ArgocdConnectionSummary>(`/api/admin/argocd-connections/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["argocd-connections"] }),
  });
}

export function useDeleteArgocdConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ ok: boolean }>(`/api/admin/argocd-connections/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["argocd-connections"] }),
  });
}

export function useTestArgocdConnection() {
  return useMutation({
    mutationFn: (body: { server_url: string; token: string; insecure_skip_verify: boolean }) =>
      apiFetch<ArgocdTestResult>("/api/admin/argocd-connections/test", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}

export function useTestSavedArgocdConnection() {
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<ArgocdTestResult>(`/api/admin/argocd-connections/${id}/test`, { method: "POST" }),
  });
}

// ─── llm-d stacks ────────────────────────────────────────────

export interface CreateLlmdStackBody {
  name: string;
  target_model_name: string;
  argocd_connection_id: string;
  namespace?: string;
  replicas?: number;
  model_server_type?: string;
  target_port?: number;
  endpoint_selector?: string | null;
  values_override?: Record<string, unknown>;
}

export type UpdateLlmdStackBody = Partial<
  Omit<CreateLlmdStackBody, "name" | "target_model_name" | "argocd_connection_id">
>;

export function useLlmdStacks() {
  return useQuery({
    queryKey: ["llmd-stacks"],
    queryFn: () =>
      apiFetch<{ stacks: LlmdStackSummary[] }>("/api/admin/llmd-stacks").then((r) => r.stacks),
  });
}

export function useCreateLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateLlmdStackBody) =>
      apiFetch<LlmdStackSummary>("/api/admin/llmd-stacks", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}

export function useUpdateLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateLlmdStackBody }) =>
      apiFetch<LlmdStackSummary>(`/api/admin/llmd-stacks/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}

export function useDeleteLlmdStack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ ok: boolean }>(`/api/admin/llmd-stacks/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llmd-stacks"] }),
  });
}

export interface LlmdPreviewManifest {
  kind: string;
  name: string;
  yaml: string;
}

interface LlmdPreviewResponse {
  manifests: LlmdPreviewManifest[];
  note: string | null;
}

export interface PreviewLlmdStackBody {
  name?: string;
  target_model_name?: string;
  namespace?: string;
  replicas?: number;
  model_server_type?: string;
  target_port?: number;
  endpoint_selector?: string | null;
  values_override?: Record<string, unknown>;
}

export function useLlmdStackPreview() {
  return useMutation({
    mutationFn: (body: PreviewLlmdStackBody) =>
      apiFetch<LlmdPreviewResponse>("/api/admin/llmd-stacks/preview", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}
