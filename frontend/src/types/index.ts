export type UserRole = "user" | "team_admin" | "super_user";
export type ModelStatus = "testing" | "prerelease" | "lts" | "deprecating" | "deprecated";
export type JoinRequestStatus = "pending" | "approved" | "rejected";

export type Locale = "ko" | "en";

/** Personal-key grant state on the LiteLLM user row (see backend personal_access). */
export type PersonalAccess = "none" | "all" | "custom";

export interface User {
  user_id: string; // 사번 (employee ID)
  email?: string;
  display_name?: string;
  role: UserRole;
  locale: Locale;
  teams: Team[];
  spend?: number;
  max_budget?: number | null;
  budget_duration?: string | null;
  /** User-level TPM/RPM: LiteLLM counts these across every key of the user. */
  tpm_limit?: number | null;
  rpm_limit?: number | null;
  /** The raw grant (model / access-group names, or a sentinel). */
  models?: string[];
  personal_access?: PersonalAccess;
  personal_keys_beta_enabled?: boolean;
  /** beta on ∧ grant present → a personal key can be minted now. */
  personal_keys_enabled?: boolean;
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
  /** LiteLLM unified access groups linked via the team's `access_group_ids`. */
  unified_access_groups?: UnifiedAccessGroup[];
  members: string[];
  admins: string[];
  member_count?: number;
  admin_count?: number;
}

export interface UnifiedAccessGroup {
  access_group_id: string;
  access_group_name: string;
  /** Model names (LiteLLM `access_model_names`); may name model access groups too. */
  models: string[];
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
  tpm_limit: number | null;
  rpm_limit: number | null;
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
  budget_request_max_amount: number | null;
  budget_request_allowed_days: string[] | null;
}

export interface ApiKey {
  token: string;
  key?: string;
  key_name: string | null;
  key_alias: string | null;
  team_id: string | null;
  /** Team-less key governed by the user row (Beta). */
  personal?: boolean;
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
    access_groups?: string[] | null;
    [key: string]: unknown;
  };
}

export interface ModelWithCatalog {
  registrations?: LiteLLMModelInfo[];
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
  requested_duration_days: number | null;
  status: JoinRequestStatus;
  reviewed_by: string | null;
  review_comment: string | null;
  created_at: string;
  updated_at: string;
}

// Request body types
export interface CreateKeyRequest {
  /** null → a personal key (Beta). */
  team_id: string | null;
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
  requested_duration_days?: number | null;
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
  /** The member's active budget boost, reported directly by the members
   * endpoint (null when none). Drives the boost badge — no separate fetch. */
  active_boost: MemberBudgetBoost | null;
  keys: MemberKey[];
}

export interface TeamMembersResponse {
  members: TeamMember[];
  total: number;
  page: number;
  page_size: number;
}

export interface MemberBudgetBoost {
  id: string;
  team_id: string;
  user_id: string;
  original_max_budget: number;
  boost_max_budget: number;
  expires_at: string | null;
  status: "active" | "reverted" | "cancelled";
  reverted_at: string | null;
  created_by: string | null;
  created_at: string | null;
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

// ─── Usage breakdown: user → key → model group ───────────────

export interface UsageModelRow extends UsageTokenBreakdown {
  model: string;
  total_tokens: number;
  api_requests: number;
  spend: number;
}

export interface UsageKeyRow extends UsageTokenBreakdown {
  /** Hashed key token (LiteLLM_VerificationToken.token). */
  api_key: string;
  /** Portal alias, else LiteLLM's key alias/name; null when the key is unknown. */
  alias: string | null;
  team_id: string | null;
  team_alias: string | null;
  personal: boolean;
  sso: boolean;
  /** "deleted": key removed but its usage kept; "unknown": key in neither key table. */
  state: "active" | "deleted" | "unknown";
  total_tokens: number;
  api_requests: number;
  spend: number;
  models: UsageModelRow[];
}

export interface UsageByKeyResponse {
  user_id: string | null;
  keys: UsageKeyRow[];
}

// ─── Admin Usage (global, per user) ───────────────────────────

export interface AdminUsageRow extends UsageTokenBreakdown {
  /** null collects usage that carries no user. */
  user_id: string | null;
  email: string | null;
  display_name: string | null;
  key_count: number;
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
  personal_access?: PersonalAccess;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUserListResponse {
  users: AdminUserSummary[];
  total: number;
  page: number;
  page_size: number;
}

// ─── Model access groups (admin) ───────────────────────────────

/** A group member model; `locked` = a config.yaml deployment carries the group (not editable here). */
export interface AccessGroupModel {
  model_name: string;
  locked: boolean;
}

export interface AccessGroup {
  name: string;
  models: AccessGroupModel[];
  /** Some member is locked: the group cannot be deleted from the portal. */
  locked: boolean;
  /** Users whose personal-key grant names the group. */
  user_count: number;
}

/** A model as the group editor sees it; `editable` = it has a DB deployment LiteLLM can tag. */
export interface AccessGroupCatalogModel {
  model_name: string;
  editable: boolean;
  access_groups: string[];
}

export interface AccessGroupList {
  groups: AccessGroup[];
  models: AccessGroupCatalogModel[];
}

export interface AccessGroupPerson {
  user_id: string;
  display_name: string | null;
  email: string | null;
  /** Has signed in to the portal at least once. */
  logged_in: boolean;
}

export interface AccessGroupMember extends AccessGroupPerson {
  personal_access: PersonalAccess;
  personal_key_count: number;
}

export type AccessGroupGrantStatus = "added" | "pregranted" | "already" | "invalid" | "error";

export interface AccessGroupGrantResult {
  input: string;
  user_id: string | null;
  status: AccessGroupGrantStatus;
  logged_in: boolean;
  display_name: string | null;
}

export type AccessGroupRevokeStatus = "removed" | "not_member" | "invalid" | "error";

export interface AccessGroupRevokeResult {
  input: string;
  user_id: string | null;
  status: AccessGroupRevokeStatus;
}

/** A user whose personal grant reaches a model: "direct", "all" and/or group names. */
export interface ModelPersonalUser extends AccessGroupPerson {
  via: string[];
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
  budget_duration?: string | null;
  models?: string[];
  personal_access?: PersonalAccess;
}

export interface PersonalAccessBody {
  access: PersonalAccess;
  models?: string[];
  max_budget?: number | null;
  budget_duration?: string | null;
  tpm_limit?: number | null;
  rpm_limit?: number | null;
  delete_personal_keys?: boolean;
}

export interface AdminUserKey {
  token: string;
  key_alias: string | null;
  key_name: string | null;
  team_id: string | null;
  personal?: boolean;
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
  /** "external" for runs cloned from an externally-discovered serving,
   * "endpoint" for a raw base-URL target; absent for portal-managed
   * deployments. */
  source?: "external" | "endpoint";
  /** The base URL benchmarked, for "endpoint" snapshots. */
  base_url?: string;
  /** Absent for "endpoint" snapshots (no portal-managed serving image). */
  image?: string;
  model_path: string;
  vllm_extra_args: string[];
  env: Record<string, string>;
  pvc_name?: string | null;
  pvc_mount_path?: string | null;
  storages?: ResolvedStorageEntry[];
  // The fields below are only captured for portal-managed deployments —
  // external-clone snapshots omit them entirely.
  engine?: string;
  replicas?: number;
  resources?: ServingResources;
  node_selector?: Record<string, string>;
  namespace?: string;
}

export interface BenchmarkRun {
  validation_id?: string | null;
  validation_attempt_id?: string | null;
  id: string;
  model_name: string;
  deployment_id: string | null;
  serving_snapshot: ServingSnapshot | null;
  ephemeral: boolean;
  serving_torn_down: boolean;
  tool: BenchmarkTool;
  kind: BenchmarkKind;
  params: Record<string, unknown>;
  label: string | null;
  note: string | null;
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

export interface BenchmarkModelGroup {
  model_name: string;
  run_count: number;
  succeeded_count: number;
  failed_count: number;
  active_count: number;
  latest_created_at: string;
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
  base_url?: string;
  tool: BenchmarkTool;
  params: Record<string, unknown>;
  namespace?: string;
  image?: string;
  api_key?: string;
  label?: string;
  note?: string;
}

export interface BenchmarkPresetSettings { presets: Record<string, LoadPreset>; default_preset: string; }

export interface LoadPreset {
  name?: string;
  workload_type?: "synthetic" | "agentic_replay";
  num_sessions?: number;
  cache_ratio?: number;
  random_input_len: number;
  random_output_len: number;
  num_prompts: number;
  max_concurrency: number;
}

/** Portal-managed serving deployment. */
export interface KVObservation {
  role: "aggregated" | "prefill" | "decode"; pod_name: string; pod_uid: string; restart: number; active: boolean;
  engine: string; image: string; image_id: string | null; revision: string | null; gpu_type: string | null;
  capacity_tokens: number | null; budget_bytes: number | null; budget_bytes_max?: number;
  capacity_tokens_source?: "startup_log" | "engine_metrics";
  estimate?: {
    capacity_tokens?: number | null; capacity_range?: [number, number]; rule_version?: string | null;
    budget_source?: string; kv_dtype?: string; tp?: number; pp?: number; dcp?: number;
    same_revision?: boolean; components?: { kind: string; layers: number }[];
  };
  delta_tokens: number | null; delta_percent: number | null; utilization: number | null;
  prefix_hit_rate: number | null; prefix_rate_scope: "since_startup" | "engine_window" | null;
  observed_at: string | null; last_seen: string;
}

export interface ModelDeployment {
  kv_observations?: KVObservation[];
  validation_id?: string | null;
  validation_state?: string | null;
  id: string;
  model_name: string;
  cluster_id: string | null;
  /** Recipe this deployment was launched from, or captured into. Informational. */
  recipe_id?: string | null;
  recipe_name?: string | null;
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
  storages: ResolvedStorageEntry[];
  storage_stale?: string[];
  /** Present on create/update responses only. */
  warnings?: string[];
  model_path: string;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  engine: ServingEngine;
  engine_args: EngineArgs | null;
  probes: ProbesSpec | null;
  /** GPU type profile name; resolved per cluster at deploy time. */
  gpu_type: string | null;
  serving_mode: ServingMode;
  aggregated_router?: AggregatedRouterConfig | null;
  pd_config: PdConfig | null;
  runtime: RuntimeOptions | null;
  /** P/D: per-role observed status from the reconciler. */
  pd_status: PdStatus | null;
  /** P/D: `D 2/2 · P 1/1` for list cells. */
  pd_summary: string | null;
  router_stack_id: string | null;
  router_stack_created: boolean;
  router_apply_error?: string | null;
  /** Detail endpoint: the linked router with live ArgoCD state. */
  router_stack?: RouterStackSummary | null;
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
  status: string;
  /** List endpoint: number of llm-d stacks whose selector picks this deployment. */
  llmd_stack_count?: number;
  /** Detail endpoint: those stacks with live ArgoCD status. */
  llmd_stacks?: LinkedLlmdStack[];
  status_message: string | null;
  ready_replicas: number;
  service_cluster_ip: string | null;
  litellm_model_id: string | null;
  /** Name in LiteLLM; null = model_name. Deployments may share one (LiteLLM load-balances them). */
  litellm_model_name: string | null;
  /** Detail endpoint: what LiteLLM sends the server (`<provider>/<served name>`). */
  litellm_model?: string | null;
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

/** One deployment field a recipe re-apply would overwrite (GET …/recipe-diff). */
export interface RecipeDiffChange {
  field: string;
  current: unknown;
  proposed: unknown;
}

/** One row of a side-by-side YAML diff; a side is `[line number, text]` or null (nothing on that side). */
export interface ManifestDiffRow {
  op: "equal" | "replace" | "delete" | "insert";
  left: [number, string] | null;
  right: [number, string] | null;
}

/** A K8s manifest rendered from the deployment (left) and as the re-apply would send it (right). */
export interface ManifestDiff {
  kind: string;
  name: string;
  /** Rows that are not `equal`; 0 = the manifest stays as it is. */
  changed: number;
  rows: ManifestDiffRow[];
}

// GET /api/model-deployments/{id}/recipe-diff — what re-applying the recipe changes
// and what the rollout needs. `blocked` is the server's reason when the re-apply
// would be refused (a `replace` rollout at one replica, a storage that does not
// resolve); `manifests` is empty when the proposed side cannot be rendered.
export interface RecipeDiff {
  recipe: { id: string; name: string };
  changes: RecipeDiffChange[];
  manifests: ManifestDiff[];
  rollout: { mode: RolloutMode; extra_gpus: number; gpu_resource_key: string; blocked: string | null };
  /** LiteLLM would be told something else (served name / key): unregister now, re-register on Ready. */
  reregister: boolean;
}

/** Registered K8s cluster — masked (never includes the kubeconfig). */
export interface K8sClusterSummary {
  id: string;
  name: string;
  context: string;
  namespace: string;
  argocd_namespace: string;
  argocd_host_cluster_id: string | null;
  argocd_dest_server: string | null;
  api_server: string | null;
  is_default: boolean;
  description: string | null;
  bench_storage_id: string | null;
  bench_mount_path: string | null;
  /** Node label key marking GPU kinds on this cluster (default gpu-type). */
  gpu_label_key: string;
  prometheus_url: string | null;
  prometheus_shared: boolean;
  prometheus_cluster_label: string | null;
  prometheus_cluster_value: string | null;
  prometheus_has_token: boolean;
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
  chart_overrides: {
    chart_repo: string | null;
    chart_name: string | null;
    chart_version: string | null;
    epp_registry: string | null;
    epp_repository: string | null;
    epp_tag: string | null;
  };
  ingress_host: string;
  ingress_class: string;
  ingress_overrides: {
    ingress_host: string | null;
    ingress_class: string | null;
  };
  direct_route_enabled: boolean;
  direct_ingress_host: string;
  direct_service: string;
  direct_overrides: {
    ingress_host: string | null;
  };
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
  /** Router label selector (router.modelServers.matchLabels, or the legacy endpointSelector). */
  selector: Record<string, string>;
  /** Model servers the selector actually picks: portal deployments + scanned external servings. */
  linked_servers: LinkedServer[];
  live_values: Record<string, unknown> | null;
  resources: LlmdAppliedResource[];
  revision: string | null;
  live_error: string | null;
}

// ─── Serving Recipes ──────────────────────────────────────────

export type ServingEngine = "vllm" | "sglang";
export type EngineArgs = Record<string, string | number | boolean>;

// ─── Prefill/decode disaggregation ───────────────────────────
export interface AggregatedRouterConfig {
  chart_repo?: string;
  chart_name?: string;
  chart_version?: string;
  values_yaml?: string;
}

export type ServingMode = "aggregated" | "pd";

/**
 * Everything one serving pool carries: the recipe's own fields for an aggregated
 * recipe, and each prefill/decode pool's fields for a P/D recipe.
 */
export interface PoolConfig {
  image: string;
  model_path: string;
  gpu_count: number;
  gpu_type: string | null;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  /** null = inherit from the recipe (pools). */
  storages: StorageEntry[] | null;
  engine_args: EngineArgs | null;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  probes: ProbesSpec | null;
  runtime: RuntimeOptions | null;
  node_selector: Record<string, string> | null;
  tolerations: unknown[] | null;
}

/** One P/D pool: a complete PoolConfig plus its replica count. Missing fields fall back to the recipe row. */
/** One pool's P/D-only pod settings, layered over its general config at render time. */
export interface PdExtras {
  env?: Record<string, string>;
  args?: string[];
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
  resources?: Record<string, string>;
  /** The vLLM container's securityContext; absent = root + IPC_LOCK / SYS_RESOURCE (llm-d default). */
  security_context?: PdSecurityContext | null;
}

export interface PdSecurityContext {
  run_as_user?: number | null;
  /** Linux capabilities to add, e.g. IPC_LOCK. */
  capabilities?: string[];
}

/** The decode pool's routing sidecar (initContainer); a blank image uses the portal's sidecar image. */
export interface SidecarSpec {
  image?: string | null;
  args?: string[];
  env?: Record<string, string>;
  requests?: Record<string, string>;
  limits?: Record<string, string>;
}

export interface PdRoleOverride extends Partial<PoolConfig> {
  replicas?: number;
  pd_extras?: PdExtras | null;
  sidecar?: SidecarSpec | null;
}

/** The llm-d router a P/D deployment creates: EPP tuning + stack-level overrides. */
export interface PdRouterConfig {
  peak_prefill_throughput?: number;
  prefix_tokens_to_match?: number;
  epp_registry?: string | null;
  epp_repository?: string | null;
  epp_tag?: string | null;
  epp_replicas?: number | null;
  ingress_class?: string | null;
  /** The router chart's values.yaml as written; supersedes the structured EPP/scheduler fields. */
  values_yaml?: string | null;
  chart_repo?: string | null;
  chart_name?: string | null;
  chart_version?: string | null;
}

/** POST /api/admin/serving-recipes/router-defaults */
export interface RouterDefaults {
  values_yaml: string;
  chart_repo: string;
  chart_name: string;
  chart_version: string;
}

/** Who orchestrates a P/D pair (router + KV plumbing); only llm-d today. */
export type PdFramework = "llm-d";

export interface PdConfig {
  framework?: PdFramework;
  prefill?: PdRoleOverride;
  decode?: PdRoleOverride;
  nixl_port?: number;
  kv_transfer_extra?: Record<string, unknown>;
  sidecar_image?: string | null;
  router?: PdRouterConfig;
}

/** `surge`: new pod first (needs spare GPUs); `replace`: old pod first (replicas ≥ 2, capacity dips). */
export type RolloutMode = "surge" | "replace";

/** Pod-level knobs that map 1:1 to the docker-run flags people start from. */
export interface RuntimeOptions {
  shm_size_gi?: number | null;
  host_ipc?: boolean;
  privileged?: boolean;
  extra_resources?: Record<string, string>;
  /** Extra TCP containerPorts on the serving container, by port name. */
  extra_ports?: Record<string, number>;
  /** Unset = surge. */
  rollout?: RolloutMode | null;
  /** Unset = 120. */
  termination_grace_seconds?: number | null;
  /** Unset = 10; 0 = no preStop hook. */
  prestop_sleep_seconds?: number | null;
  /** vLLM KV-cache events (`--kv-events-config`); present = on. */
  kv_events?: KvEventsOptions | null;
  /** Unset = portal defaults; [] = log every endpoint. */
  access_log_exclude_paths?: string[] | null;
}

/** vLLM's ZMQ KV-event publisher. Blank topic = `kv@$(POD_IP):<port>@<served model name>`. */
export interface KvEventsOptions {
  endpoint?: string;
  replay_endpoint?: string | null;
  topic?: string | null;
  buffer_steps?: number | null;
  hwm?: number | null;
  max_queue_size?: number | null;
}

export interface PdRoleStatus {
  ready: number;
  desired: number;
  status?: string;
  message?: string | null;
}

export interface PdStatus {
  prefill?: PdRoleStatus;
  decode?: PdRoleStatus;
  router?: { ready: boolean; reason?: string | null; api_base?: string | null };
}

/** The llm-d router a P/D deployment registers through (detail endpoint). */
export interface RouterStackSummary {
  id: string;
  name: string;
  namespace: string;
  ingress_host: string;
  created_by_deployment: boolean;
  sync_status?: string;
  health_status?: string;
  status_message?: string | null;
}

export interface ServingRecipe {
  id: string;
  name: string;
  description: string | null;
  hf_repo_id?: string | null;
  model_path: string;
  image: string;
  gpu_count: number;
  gpu_resource_key: string;
  cpu_request: string | null;
  cpu_limit: string | null;
  memory_request: string | null;
  memory_limit: string | null;
  node_selector: Record<string, string> | null;
  tolerations: unknown[] | null;
  storages: StorageEntry[];
  /** Present on create/update responses only. */
  warnings?: string[];
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  engine: ServingEngine;
  engine_args: EngineArgs | null;
  probes: ProbesSpec | null;
  /** GPU type profile name; resolved per cluster at deploy time. */
  gpu_type: string | null;
  serving_mode: ServingMode;
  aggregated_router?: AggregatedRouterConfig | null;
  pd_config: PdConfig | null;
  runtime: RuntimeOptions | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** One HTTP probe; every field optional (unset = portal default). Port is always the serving port. */
export interface ProbeSpec {
  path?: string;
  initial_delay_seconds?: number;
  period_seconds?: number;
  timeout_seconds?: number;
  failure_threshold?: number;
  /** Kubernetes only accepts 1 on liveness and startup probes. */
  success_threshold?: number;
}

/** `liveness: null` = no liveness probe (default); `{}` = liveness on with defaults. */
export interface ProbesSpec {
  readiness?: ProbeSpec | null;
  liveness?: ProbeSpec | null;
  /** `null`/unset = none for aggregated servings; P/D pools get a portal default. */
  startup?: ProbeSpec | null;
}

// Editable fields for create/update (server sets id/audit/timestamps).
export type ServingRecipeInput = Omit<
  ServingRecipe,
  "id" | "created_by" | "updated_by" | "created_at" | "updated_at"
>;

// POST body: a recipe captured from a portal deployment back-links it
// (deployment.recipe_id) when that deployment has no recipe yet.
export type ServingRecipeCreateBody = ServingRecipeInput & { source_deployment_id?: string | null };

// POST /api/admin/serving-recipes/preview: an unsaved recipe rendered for a deploy target.
export type CapacityScenario = { context_tokens: number; requests: number; shared_prefix_tokens: number };
export interface RecipeDSparkEstimate {
  draft_model: string;
  revision: string | null;
  assumed_revision: boolean;
  draft_tokens: number;
  scenario_requests: number;
  max_num_seqs: number | null;
  active_requests: number;
  candidate_tokens: number;
  lookahead_tokens_per_request: number;
  lookahead_tokens: number;
  draft_weight_bytes_per_gpu: number | null;
  draft_kv_bytes_per_gpu: number;
  verification_workspace_bytes_per_gpu?: number;
  context_limited_capacity_tokens?: number;
  draft_cache_dtype: string;
  draft_tp: number;
  draft_dcp: number;
  draft_window_tokens: number;
}
export type RecipeCapacity = {
  rule_version: string;
  pools: Array<{
    role: string; gpu_type: string | null; gpu_count?: number; gpu_profile_resolved?: boolean; gpu_vram_gb?: number | null;
    fingerprint: string; image: string; engine_version?: string;
    pending?: boolean; missing?: string[]; assumptions?: string[];
    capacity_tokens: number | null; complete_requests: number | null;
    capacity_range?: number[]; complete_requests_range?: number[]; required_bytes_per_gpu?: number;
    required_bytes_per_replica?: number; shared_required_bytes_per_gpu?: number;
    budget_source?: "explicit" | "startup" | "profile_upper_bound" | "unknown";
    budget_exceeds_vram?: boolean; payload_lower_bound?: boolean;
    weights_exceed_vram?: boolean;
    weight_fit_unverified?: boolean;
    device_only_weight_bound?: boolean;
    resident_weight_floor_bytes_per_gpu?: number;
    weight_offload?: { requested_bytes_per_gpu: number; min_bytes_per_gpu?: number | null; max_bytes_per_gpu?: number | null };
    host_kv_offload?: {
      source: string; configured_bytes: number | null;
      scope: "all_workers" | "per_worker" | "external" | "unknown";
      device_pool_ratio?: number;
    };
    speculation?: RecipeDSparkEstimate;
    integrated_speculation?: {
      method: string; draft_tokens: number; active_requests: number; candidate_tokens: number; max_num_seqs?: number | null;
    };
    weight_residency?: {
      checkpoint_bytes: number; resident_weight_bytes: number;
      excluded_bytes: Partial<Record<"draft" | "vision" | "audio" | "engram_embeddings" | "routed_experts", number>>;
    };
    budget_bytes_per_gpu?: number[]; revision?: string; metadata_digest?: string; metadata_source?: string;
    observed?: {
      capacity_tokens: number | null; budget_bytes_per_gpu: number | null;
      deployment_id: string; observed_at: string | null; pod_name?: string | null;
    } | null;
    tp?: number; pp?: number; dcp?: number; kv_dtype?: string; block_tokens?: number;
    max_model_len?: number | null; max_num_seqs?: number | null;
    components?: Array<{ pipeline_rank: number; kind: string; layers: number; bytes_per_gpu: number; unit: "token" | "request" }>;
  }>;
};
export type SavedRecipeCapacity = RecipeCapacity & {
  recipe_id: string; cluster_id: string | null; namespace: string; warnings: string[];
};
export interface ThroughputScenario {
  input_tokens: number;
  output_tokens: number;
  concurrency: number;
  shared_prefix_percent: number;
}
export interface CacheThroughputEstimate {
  rpm: [number, number];
  input_tpm: [number, number];
  output_tpm: [number, number];
  reused_input_tokens: number;
  effective_concurrency: number;
  phase_seconds?: { prefill: [number, number]; decode: [number, number] };
}
type RangePair = [number, number];
export interface RecipeCalibration {
  repetitions: number;
  forecast_rpm: RangePair | null;
  forecast_version?: string | null;
  measured_rpm: RangePair;
  target_requests_per_second?: number;
  search_stop_reason?: string | null;
  observed_request_seconds?: Record<"prefill" | "decode", RangePair> | null;
  cache_scenario: "cold" | "warm" | null;
  slo_rates: {rpm: RangePair; input_tpm: RangePair; output_tpm: RangePair} | null;
  phases: Record<"prefill" | "decode", {
    predicted_seconds: RangePair; measured_seconds: RangePair; deviation_percent: number | null;
  }> | null;
  phase_reason: string | null;
}
export interface SavedRecipeThroughput {
  version?: string;
  recipe_id: string;
  cluster_id: string | null;
  namespace: string;
  scenario: ThroughputScenario;
  status: "estimated" | "unavailable" | "infeasible";
  reason: string | null;
  draft_method?: string;
  draft_missing?: string;
  shorter_workload?: {scenario: ThroughputScenario; status: SavedRecipeThroughput["status"]; reason: string | null};
  calibration?: RecipeCalibration;
  pending: boolean;
  rpm: [number, number] | null;
  input_tpm: [number, number] | null;
  output_tpm: [number, number] | null;
  cache_scenarios?: { cold: CacheThroughputEstimate; warm?: CacheThroughputEstimate };
  assumptions: string[];
  sources: Array<{ label: string; url: string }>;
  effective_concurrency?: [number, number] | null;
  efficiency?: { compute: [number, number]; memory: [number, number] };
  method?: string;
  benchmark_id?: string;
  scope?: string;
  pools: Array<{
    role: string;
    gpu_type: string | null;
    gpu_count: number | null;
    replicas: number;
    max_model_len?: number | null;
    gpu_vram_gb?: number | null;
    weight_bytes?: number | null;
    metadata_issue?: {code: string; source?: string; format?: string} | null;
    draft_method?: string | null;
    draft_tokens?: number | null;
    tp?: number | null; pp?: number | null; dcp?: number | null;
    model_path: string;
    engine: string;
  }>;
}
export type ServingRecipePreviewBody = ServingRecipeInput & {
  capacity_scenario?: CapacityScenario;
  cluster_id: string | null;
  /** null = the cluster's default namespace. */
  namespace: string | null;
  model_name: string | null;
};

export interface ManifestPreview {
  kind: string;
  name: string;
  yaml: string;
}

export interface ServingRecipePreview {
  kv_capacity?: RecipeCapacity;
  router_images?: { epp: string; envoy: string };
  manifests: ManifestPreview[];
  warnings: string[];
}

/** One thing the reverse parser guessed or dropped; `code` maps to i18n `servingRecipes.warn.<code>`. */
export interface RecipeDraftWarning {
  code: string;
  detail?: string;
}

// GET /api/model-deployments/external/recipe-draft — recipe fields parsed from
// a live Deployment. `engine_args` holds every long flag found; the form moves
// flags it has no field for into the free-text extra args.
export interface RecipeDraftResponse {
  draft: ServingRecipeInput;
  warnings: RecipeDraftWarning[];
  source: {
    namespace: string | null;
    deployment_name: string | null;
    replicas: number | null;
    command: string[];
    args: string[];
  };
}

// Body for POST /api/model-deployments (recipe serving fields + instance fields).
export interface CreateDeploymentBody {
  model_name: string;
  /** Name in LiteLLM; blank/null = model_name. */
  litellm_model_name?: string | null;
  litellm_api_base?: string | null;
  cluster_id: string | null;
  /** Recipe this deployment is launched from (informational; stored on the row). */
  recipe_id?: string | null;
  /** null = the cluster's default namespace (resolved by the backend). */
  namespace: string | null;
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
  storages: StorageEntry[];
  model_path: string;
  vllm_extra_args: string[] | null;
  env: Record<string, string> | null;
  engine: ServingEngine;
  engine_args: EngineArgs | null;
  probes: ProbesSpec | null;
  /** GPU type profile name; resolved per cluster at deploy time. */
  gpu_type: string | null;
  /** P/D: the router's host (the pools get no Ingress of their own). */
  ingress_host: string;
  ingress_path: string;
  ingress_class: string;
  serving_mode?: ServingMode;
  aggregated_router?: AggregatedRouterConfig | null;
  pd_config?: PdConfig | null;
  runtime?: RuntimeOptions | null;
  /** P/D: link this llm-d stack instead of auto-creating `<model>-router`. */
  router_stack_id?: string | null;
}

// ─── Serving home (GET /api/admin/serving/overview) ───────────
export interface LinkedServer {
  kind: "portal" | "external";
  id: string | null;
  model_name: string | null;
  name: string;
  namespace: string;
  status: string | null;
  /** Portal servers only: `pd` when the server is a prefill/decode pair. */
  serving_mode?: ServingMode;
}

/** An llm-d stack as seen from a model server it routes to. */
export interface LinkedLlmdStack {
  id: string;
  name: string;
  /** Serving overview: the stack's cluster (null = portal default). */
  cluster_id?: string | null;
  namespace?: string;
  selector: Record<string, string>;
  servers?: { kind: "portal" | "external"; name: string; namespace: string }[];
  sync_status?: string;
  health_status?: string;
  status_message?: string | null;
}

export interface ServingOverview {
  scopes: { cluster_id: string | null; namespace: string }[];
  errors: { cluster: string; namespace: string; message: string }[];
  models: ServingOverviewRow[];
  /** Stacks whose selector picks no known server. */
  unlinked_stacks: (LinkedLlmdStack & { target_model_name: string })[];
}

export interface ServingOverviewRow {
  external_servings: {
    deployment_name: string; cluster_id: string | null; namespace: string; engine: ServingEngine;
    status: string; replicas: number; ready_replicas: number; litellm_model_id: string | null;
  }[];
  model_name: string;
  recipes: { id: string; name: string; engine: ServingEngine }[];
  deployments: {
    id: string; status: string; ready_replicas: number; replicas: number; engine: ServingEngine; litellm_model_id: string | null;
    cluster_id: string | null;
    namespace: string;
    recipe_id?: string | null;
    gpu_type?: string | null;
    serving_mode?: ServingMode;
    pd_summary?: string | null;
    router_stack_id?: string | null;
  }[];
  llmd_stacks: LinkedLlmdStack[];
  performance: {
    run_id: string; tool: string; finished_at: string | null;
    output_throughput?: number; request_throughput?: number; mean_ttft_ms?: number; p99_ttft_ms?: number; mean_tpot_ms?: number;
  } | null;
  accuracy: { run_id: string; tool: string; finished_at: string | null; metric: { name: string; value: number } | null } | null;
  catalog: { id: string; display_name: string; status: ModelStatus; visible: boolean } | null;
  litellm_registered: boolean;
}

// ─── Storage ─────────────────────────────────────────────────────────────────

export type StorageKind = "weights" | "cache" | "data" | "other";
export interface StorageEntry { storage_id: string; mount_path: string; read_only: boolean }
export interface StorageSnapshot {
  binding_id: string; type: "pvc" | "nfs"; claim_name: string | null; namespace: string | null;
  nfs_server: string | null; nfs_path: string | null; sub_path: string | null;
}
export interface ResolvedStorageEntry extends StorageEntry { volume_name: string; resolved: StorageSnapshot }
export interface StorageBoundOn { cluster_id: string | null; cluster_name: string; namespace: string | null }
export interface Storage {
  id: string; name: string; description: string | null; kind: StorageKind; created_by: string | null; updated_at: string | null;
  binding_count?: number; bound_on?: StorageBoundOn[]; recipe_count?: number; deployment_count?: number;
}
export interface StorageBinding {
  id: string; storage_id: string; cluster_id: string | null; cluster_name: string; namespace: string | null;
  type: "pvc" | "nfs"; claim_name: string | null; nfs_server: string | null; nfs_path: string | null;
  sub_path: string | null; updated_at: string | null;
}
export interface StorageDetail extends Storage {
  bindings: StorageBinding[];
  usage: {
    recipes: { id: string; name: string }[];
    deployments: { id: string; model_name: string; stale: boolean }[];
    clusters: { id: string; name: string }[];
  };
}
export type StorageInput = Pick<Storage, "name" | "description" | "kind">;
export type StorageBindingInput = Omit<StorageBinding, "id" | "storage_id" | "cluster_name" | "updated_at">;
export type PvcPhase = "Bound" | "Pending" | "Lost" | "missing" | "error";
export interface ClusterPvc { name: string; phase: string | null; capacity: string | null; storage_class: string | null; access_modes: string[] }
export interface StorageResolution { storage_id: string; name: string | null; binding: StorageSnapshot | null; pvc_phase: PvcPhase | null }

// ─── GPU type profiles ───────────────────────────────────────────────────────

export interface GpuProfile {
  id: string;
  cluster_id: string | null;
  name: string;
  label_key: string | null;
  /** label_key, or the cluster's gpu_label_key when null. */
  effective_label_key: string;
  label_value: string;
  gpu_resource_key: string;
  tolerations: unknown[] | null;
  vram_gb: number | null;
  description: string | null;
  enabled: boolean;
  created_by: string | null;
  updated_at: string | null;
}

export type GpuProfileInput = Omit<GpuProfile, "id" | "cluster_id" | "effective_label_key" | "created_by" | "updated_at">;

export interface GpuProfilesResponse {
  label_key: string;
  profiles: GpuProfile[];
}

export interface GpuNodeGroup {
  label_value: string | null;
  nodes: number;
  allocatable: Record<string, number>;
  requested: Record<string, number>;
  available: Record<string, number>;
  /** Profile on this cluster whose label value matches, if any. */
  profile_name: string | null;
}

export interface GpuNodesResponse {
  label_key: string;
  resource_keys: string[];
  groups: GpuNodeGroup[];
  nodes: { name: string; label_value: string | null; schedulable: boolean; allocatable: Record<string, number>; requested: Record<string, number> }[];
  errors: string[];
}

export interface GpuTypeOption {
  name: string;
  clusters: { cluster_id: string | null; cluster_name: string; profile_id: string; gpu_resource_key: string; vram_gb: number | null; label_value: string }[];
}
