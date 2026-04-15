export type UserRole = "user" | "team_admin" | "super_user";
export type ModelStatus = "testing" | "prerelease" | "lts" | "deprecating" | "deprecated";
export type JoinRequestStatus = "pending" | "approved" | "rejected";

export interface User {
  user_id: string; // 사번 (employee ID)
  email?: string;
  display_name?: string;
  role: UserRole;
  teams: Team[];
  spend?: number;
  max_budget?: number | null;
}

export interface Team {
  team_id: string;
  team_alias: string;
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
  membership_duration: string | null;
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
}

export type StatusSchedule = Partial<Record<ModelStatus, string>>;

export interface ModelCatalog {
  id: string;
  model_name: string;
  display_name: string;
  description: string | null;
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
  status?: ModelStatus;
  status_schedule?: StatusSchedule;
}

export interface UpdateModelCatalogRequest {
  display_name?: string;
  description?: string;
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
}

export interface TeamMember {
  user_id: string;
  is_admin: boolean;
  key_count: number;
  total_spend: number;
  total_max_budget: number | null;
  keys: MemberKey[];
}

export interface TeamMembersResponse {
  members: TeamMember[];
  total: number;
  page: number;
  page_size: number;
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
