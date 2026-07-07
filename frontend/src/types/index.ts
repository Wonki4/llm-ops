export type UserRole = "user" | "team_admin" | "super_user";
export type ModelStatus = "testing" | "prerelease" | "lts" | "deprecating" | "deprecated";
export type JoinRequestStatus = "pending" | "approved" | "rejected";

export type Locale = "ko" | "en";

export interface User {
  user_id: string; // 사번 (employee ID)
  email?: string;
  display_name?: string;
  role: UserRole;
  locale: Locale;
  teams: Team[];
  spend?: number;
  max_budget?: number | null;
}

export interface Team {
  team_id: string;
  team_alias: string;
  description?: string | null;
  max_budget: number | null;
  spend: number;
  budget_duration: string | null;
  budget_reset_at: string | null;
  models: string[];
  members: string[];
  admins: string[];
  member_count?: number;
  admin_count?: number;
}

export interface DiscoverTeam extends Team {
  is_member: boolean;
  has_pending_request: boolean;
}

export interface TeamMembership {
  spend: number;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

export interface TeamDetail {
  team: Team;
  my_keys: ApiKey[];
  default_member_budget: number | null;
  default_member_tpm_limit: number | null;
  default_member_rpm_limit: number | null;
  membership_duration: string | null;
  default_tpm_limit: number | null;
  default_rpm_limit: number | null;
  is_admin: boolean;
  my_membership: TeamMembership;
}

export interface ApiKey {
  token: string;
  key?: string;
  key_name: string | null;
  key_alias: string | null;
  team_id: string | null;
  user_id: string | null;
  spend: number;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
  models: string[];
  expires: string | null;
  created_at: string;
  tpm_limit: number | null;
  rpm_limit: number | null;
  model_tpm_limit: Record<string, number> | null;
  model_rpm_limit: Record<string, number> | null;
  model_tpm_inherited: boolean;
  model_rpm_inherited: boolean;
}

export type StatusSchedule = Partial<Record<ModelStatus, string>>;

export interface ModelCatalog {
  id: string;
  model_name: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  status: ModelStatus;
  status_schedule: StatusSchedule | null;
  visible: boolean;
  status_change_date: string | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface LiteLLMModelInfo {
  model_name: string;
  litellm_params: {
    model: string;
    rpm?: number;
    tpm?: number;
    [key: string]: unknown;
  };
  model_info: {
    id: string;
    db_model: boolean;
    litellm_provider: string;
    input_cost_per_token: number | null;
    output_cost_per_token: number | null;
    cache_read_input_token_cost: number | null;
    max_tokens: number | null;
    max_input_tokens: number | null;
    max_output_tokens: number | null;
    supports_vision: boolean | null;
    supports_function_calling: boolean | null;
    mode: string | null;
    [key: string]: unknown;
  };
}

export interface ModelWithCatalog {
  model_name: string;
  litellm_info: LiteLLMModelInfo | null;
  catalog: ModelCatalog | null;
}

export interface ModelStatusHistory {
  id: string;
  catalog_id: string;
  model_name: string;
  previous_status: ModelStatus | null;
  new_status: ModelStatus;
  changed_by: string;
  comment: string | null;
  changed_at: string;
}

export interface StatusHistorySummaryBucket {
  bucket: string;
  count: number;
}

export interface StatusHistoryTransition {
  from_status: ModelStatus | null;
  to_status: ModelStatus;
  count: number;
}

export interface StatusHistoryTopModel {
  model_name: string;
  count: number;
}

export interface ModelStatusHistorySummary {
  total_changes: number;
  unique_models: number;
  to_status: Partial<Record<ModelStatus, number>>;
  series: StatusHistorySummaryBucket[];
  transitions: StatusHistoryTransition[];
  top_models: StatusHistoryTopModel[];
}

export type RequestType = "join" | "budget";

export interface TeamJoinRequest {
  id: string;
  requester_id: string;
  team_id: string;
  team_alias?: string;
  request_type: RequestType;
  message: string | null;
  requested_budget: number | null;
  status: JoinRequestStatus;
  reviewed_by: string | null;
  review_comment: string | null;
  created_at: string;
  updated_at: string;
}

// Request body types
export interface CreateKeyRequest {
  team_id: string;
  key_alias: string;
  models?: string[];
  max_budget?: number;
  budget_duration?: string;
}

export interface CreateJoinRequestBody {
  team_id: string;
  message?: string;
}

export interface CreateBudgetRequestBody {
  team_id: string;
  requested_budget: number;
  message?: string;
}

export interface ReviewRequestBody {
  comment?: string;
}

export interface CreateModelCatalogRequest {
  model_name: string;
  display_name: string;
  description?: string;
  icon_url?: string | null;
  status?: ModelStatus;
  status_schedule?: StatusSchedule;
  is_external?: boolean;
}

export interface UpdateModelCatalogRequest {
  display_name?: string;
  description?: string;
  icon_url?: string | null;
  status?: ModelStatus;
  status_schedule?: StatusSchedule;
  visible?: boolean;
}

// ─── Budgets ──────────────────────────────────────────────────

export interface Budget {
  budget_id: string;
  max_budget: number | null;
  soft_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
  tpm_limit: number | null;
  rpm_limit: number | null;
  created_at: string | null;
  created_by: string;
  updated_at: string | null;
  updated_by: string;
  team_membership_count: number;
  key_count: number;
  org_count: number;
  project_count: number;
  end_user_count: number;
  tag_count: number;
  org_membership_count: number;
}

export interface BudgetListResponse {
  budgets: Budget[];
  total: number;
  page: number;
  page_size: number;
}

export interface BudgetDetails {
  team_memberships: { user_id: string; team_id: string; team_alias: string | null; spend: number }[];
  keys: { token: string; key_alias: string | null; key_name: string | null; user_id: string | null; team_id: string | null; spend: number }[];
  organizations: { organization_id: string; organization_alias: string | null }[];
  projects: { project_id: string; project_name: string | null }[];
  end_users: { user_id: string; alias: string | null; spend: number }[];
  tags: string[];
  org_memberships: { user_id: string; organization_id: string; spend: number }[];
}

export interface OrphanBudget {
  budget_id: string;
  max_budget: number | null;
  created_at: string | null;
}

// ─── Team Members ─────────────────────────────────────────────

export interface MemberKey {
  token: string;
  key_alias: string | null;
  key_name: string | null;
  spend: number;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
  models: string[];
  created_at: string;
  tpm_limit: number | null;
  rpm_limit: number | null;
}

export interface TeamMember {
  user_id: string;
  is_admin: boolean;
  key_count: number;
  total_spend: number;
  total_max_budget: number | null;
  total_tpm_limit: number | null;
  total_rpm_limit: number | null;
  expires_at: string | null;
  expiry_status: string | null;
  keys: MemberKey[];
}

export interface TeamMembersResponse {
  members: TeamMember[];
  total: number;
  page: number;
  page_size: number;
}

// ─── Team Usage ───────────────────────────────────────────────

export interface UsageTokenBreakdown {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
}

export interface UsageMember extends UsageTokenBreakdown {
  user_id: string;
  total_tokens: number;
  api_requests: number;
  spend: number;
}

export interface UsageSeriesPoint extends UsageTokenBreakdown {
  bucket: string;
  total_tokens: number;
  api_requests: number;
  spend: number;
}

export interface TeamUsageResponse {
  members: UsageMember[];
  series: UsageSeriesPoint[];
  totals: UsageTokenBreakdown & { total_tokens: number; api_requests: number; spend: number };
}

// ─── Admin Usage (global, per user×team) ──────────────────────

export interface AdminUsageRow extends UsageTokenBreakdown {
  user_id: string;
  email: string | null;
  display_name: string | null;
  team_id: string | null;
  team_alias: string | null;
  total_tokens: number;
  api_requests: number;
  spend: number;
}

export interface AdminUsageResponse {
  rows: AdminUsageRow[];
  totals: UsageTokenBreakdown & { total_tokens: number; api_requests: number; spend: number };
  teams: { team_id: string; team_alias: string | null }[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUsageDay extends UsageTokenBreakdown {
  date: string;
  total_tokens: number;
  api_requests: number;
  spend: number;
}

export interface AdminUsageDailyResponse {
  days: AdminUsageDay[];
  totals: UsageTokenBreakdown & { total_tokens: number; api_requests: number; spend: number };
}

// ─── Redis Catalog ────────────────────────────────────────────

export interface RedisCatalogEntry {
  display_name: string;
  model: string;
  apiBase: string;
  apiKey: string;
  options: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RedisCatalogListResponse {
  entries: RedisCatalogEntry[];
  total: number;
}

// ─── Admin Users ──────────────────────────────────────────────

export type GlobalRole = "user" | "super_user";

export interface AdminUserSummary {
  user_id: string;
  email: string | null;
  display_name: string | null;
  global_role: GlobalRole;
  key_count: number;
  team_count: number;
  spend: number;
  max_budget: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUserListResponse {
  users: AdminUserSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUserDetailProfile {
  user_id: string;
  email: string | null;
  display_name: string | null;
  global_role: GlobalRole;
  litellm_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  spend: number;
  max_budget: number | null;
  tpm_limit: number | null;
  rpm_limit: number | null;
}

export interface AdminUserKey {
  token: string;
  key_alias: string | null;
  key_name: string | null;
  team_id: string | null;
  spend: number;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
  models: string[];
  tpm_limit: number | null;
  rpm_limit: number | null;
  model_tpm_limit: Record<string, number> | null;
  model_rpm_limit: Record<string, number> | null;
  model_tpm_inherited: boolean;
  model_rpm_inherited: boolean;
  expires: string | null;
  created_at: string | null;
}

export interface AdminUserTeam {
  team_id: string;
  team_alias: string | null;
  is_admin: boolean;
  spend: number;
  max_budget: number | null;
  expires_at: string | null;
  expiry_status: string | null;
}

export interface AdminUserDetail {
  user: AdminUserDetailProfile;
  keys: AdminUserKey[];
  teams: AdminUserTeam[];
}

// ─── Announcements ──────────────────────────────────────────────

export interface Announcement {
  id: string;
  title: string;
  content: string;
  author_id: string;
  is_published: boolean;
  is_pinned: boolean;
  is_featured: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreateAnnouncementRequest {
  title: string;
  content: string;
  is_published?: boolean;
  is_pinned?: boolean;
  is_featured?: boolean;
}

export interface UpdateAnnouncementRequest {
  title?: string;
  content?: string;
  is_published?: boolean;
  is_pinned?: boolean;
  is_featured?: boolean;
}

// ─── Benchmark Runs ─────────────────────────────────────────────

export type BenchmarkTool = "vllm_serving" | "sglang_serving" | "lm_eval";
export type BenchmarkKind = "performance" | "accuracy";
export type BenchmarkStatus =
  | "provisioning"
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ServingResources {
  gpu_count: number;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
}

/** Frozen serving config captured on a benchmark run (the deployment it tested). */
export interface ServingSnapshot {
  engine: string;
  image: string;
  model_path: string;
  vllm_extra_args: string[];
  env: Record<string, string>;
  replicas: number;
  resources: ServingResources;
  node_selector: Record<string, string>;
  namespace: string;
}

export interface BenchmarkRun {
  id: string;
  model_name: string;
  deployment_id: string | null;
  serving_snapshot: ServingSnapshot | null;
  ephemeral: boolean;
  serving_torn_down: boolean;
  tool: BenchmarkTool;
  kind: BenchmarkKind;
  params: Record<string, unknown>;
  bench_image: string | null;
  cluster_id: string | null;
  status: BenchmarkStatus;
  k8s_job_name: string | null;
  k8s_namespace: string | null;
  result: Record<string, unknown> | null;
  error_message: string | null;
  created_by: string;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface BenchmarkListResponse {
  runs: BenchmarkRun[];
}

export interface CreateBenchmarkRequest {
  model_name?: string;
  cluster_id?: string;
  deployment_id?: string;
  ephemeral?: boolean;
  serving_overrides?: Record<string, unknown>;
  external_target?: {
    cluster_id: string | null;
    namespace: string;
    deployment_name: string;
  } | null;
  tool: BenchmarkTool;
  params: Record<string, unknown>;
  namespace?: string;
  image?: string;
  api_key?: string;
}

/** Portal-managed serving deployment. */
export interface ModelDeployment {
  id: string;
  model_name: string;
  cluster_id: string | null;
  namespace: string;
  image: string;
  replicas: number;
  gpu_count: number;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  node_selector: Record<string, string> | null;
  tolerations: unknown[] | null;
  pvc_name: string | null;
  pvc_mount_path: string | null;
  model_path: string;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
  status: string;
  status_message: string | null;
  ready_replicas: number;
  service_cluster_ip: string | null;
  litellm_model_id: string | null;
  last_synced_at: string | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelDeploymentEvent {
  id: string;
  deployment_id: string;
  event_type: string;
  severity: string;
  from_status: string | null;
  to_status: string | null;
  message: string | null;
  seen: boolean;
  alert_sent: boolean;
  created_at: string | null;
}

/** Registered K8s cluster — masked (never includes the kubeconfig). */
export interface K8sClusterSummary {
  id: string;
  name: string;
  context: string;
  namespace: string;
  argocd_namespace: string;
  api_server: string | null;
  is_default: boolean;
  description: string | null;
  default_nfs_server: string | null;
  default_nfs_path: string | null;
  default_nfs_mount_path: string | null;
  has_kubeconfig: boolean;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ClusterTestResult {
  ok: boolean;
  server_version: string | null;
  message: string;
}

/** An llm-d serving stack (ArgoCD-managed); status read live from ArgoCD. */
export interface LlmdStackSummary {
  id: string;
  name: string;
  target_model_name: string;
  cluster_id: string | null;
  namespace: string;
  argo_app_name: string;
  chart_repo: string;
  chart_name: string;
  chart_version: string;
  epp_image: string;
  helm_values: Record<string, unknown>;
  values_yaml: string;
  sync_status: string;
  health_status: string;
  status_message: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface LlmdAppliedResource {
  group: string;
  version: string;
  kind: string | null;
  name: string | null;
  namespace: string | null;
  status: string | null;
  health: string | null;
}

export interface LlmdAppliedResponse {
  effective_values: Record<string, unknown>;
  live_values: Record<string, unknown> | null;
  resources: LlmdAppliedResource[];
  revision: string | null;
  live_error: string | null;
}
