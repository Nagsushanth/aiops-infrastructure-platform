
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const TOKEN_KEY = "aiops-access-token";

export function getAccessToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setAccessToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface Conversation {
  id: number;
  session_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  authenticated = true,
): Promise<T> {
  const token = authenticated ? getAccessToken() : null;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`API error ${response.status}: ${detail}`);
  }

  return response.json() as Promise<T>;
}

export async function checkBackendHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health", {}, false);
}

export interface AuthUser {
  id: number;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
}

export async function registerUser(
  email: string,
  password: string,
): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }, false);
}

export async function loginUser(
  email: string,
  password: string,
): Promise<AuthTokenResponse> {
  return apiRequest<AuthTokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }, false);
}

export async function getCurrentUser(): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/me");
}

export async function createConversation(
  sessionId: string,
  title: string,
): Promise<Conversation> {
  return apiRequest<Conversation>("/conversations", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      title,
    }),
  });
}

export async function getConversationBySession(
  sessionId: string,
): Promise<Conversation> {
  return apiRequest<Conversation>(
    `/conversations/by-session/${encodeURIComponent(sessionId)}`,
  );
}


export async function sendMessage(
  conversationId: number,
  role: "user" | "assistant",
  content: string,
): Promise<ChatMessage> {
  return apiRequest<ChatMessage>(
    `/conversations/${conversationId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ role, content }),
    },
  );
}

export async function getMessages(
  conversationId: number,
): Promise<ChatMessage[]> {
  return apiRequest<ChatMessage[]>(
    `/conversations/${conversationId}/messages`,
  );
}


export interface TerraformPreviewResource {
  address: string;
  type: string;
  name: string;
  action: string;
  description: string;
}


export interface TerraformCostEstimate {
  status: "estimated" | "unavailable";
  currency: string;
  instance_type: string;
  region: string;
  estimated_monthly_compute_cost: string | null;
  budget_threshold: string | null;
  budget_status:
    | "within_threshold"
    | "exceeds_threshold"
    | "not_configured"
    | "not_evaluated";
  pricing_source?: string;
  pricing_verified_on?: string;
  assumptions?: {
    hours_per_month: string;
    hourly_rate_usd?: string;
    includes?: string[];
    excludes?: string[];
  };
  note: string;
}

export interface TerraformPreviewResponse {
  preview_type: string;
  is_real_terraform_plan: boolean;
  deployment_enabled: boolean;
  status: string;
  cloud_provider: string;
  region: string;
  environment: string;
  resource_summary: {
    create: number;
    update: number;
    delete: number;
    total_managed_resources: number;
  };
  resources: TerraformPreviewResource[];
  configuration?: {
    resource_type: string;
    instance_type: string;
    vcpus: number;
    memory_gb: number;
    operating_system: string;
    public_ip_associated: boolean;
    inbound_security_group_rules: number;
    root_volume_encrypted: boolean;
    imds_v2_required: boolean;
  };
  estimated_monthly_cost: string | null;
  cost_estimate_note: string;
  cost_estimate: TerraformCostEstimate;
  approval: {
    technical_review: string;
    cost_review: string;
    security_review: string;
  };
  warnings: string[];
  terraform_configuration?: string;
}


export async function createTerraformPreview(
  specification: {
    cloud_provider: "aws" | "azure" | "gcp";
    resource_type: string;
    environment: "development" | "testing" | "staging" | "production";
    region: string;
    vcpus: number;
    memory_gb: number;
    public_access: boolean;
    operating_system: string;
  },
): Promise<TerraformPreviewResponse> {
  return apiRequest<TerraformPreviewResponse>("/terraform/preview", {
    method: "POST",
    body: JSON.stringify(specification),
  });
}


export interface TerraformPlanResource {
  address: string | null;
  mode: string;
  type: string | null;
  name: string | null;
  provider_name: string | null;
  actions: string[];
  before: unknown;
  after: unknown;
  after_unknown: unknown;
  action_reason: string | null;
}

export interface TerraformPlanReviewResponse {
  preview_type: string;
  is_real_terraform_plan: boolean;
  deployment_enabled: boolean;
  terraform_version: string | null;
  format_version: string | null;
  resource_summary: {
    create: number;
    update: number;
    delete: number;
    replace: number;
    read: number;
    no_op: number;
    other: number;
    total_resource_changes: number;
  };
  resources: TerraformPlanResource[];
}

export async function parseTerraformPlan(
  plan: Record<string, unknown>,
): Promise<TerraformPlanReviewResponse> {
  return apiRequest<TerraformPlanReviewResponse>("/terraform/plan/parse", {
    method: "POST",
    body: JSON.stringify({ plan }),
  });
}

export interface ApprovalResponse {
  id: number;
  infrastructure_request_id: number;
  requester_id: number;
  reviewer_id: number | null;
  status: string;
  plan_summary: Record<string, unknown>;
  reviewer_comments: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}


export interface ApprovalResponse {
  id: number;
  infrastructure_request_id: number;
  terraform_plan_record_id: number | null;
  requester_id: number;
  reviewer_id: number | null;
  status: string;
  plan_summary: Record<string, unknown>;
  reviewer_comments: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TrustedTerraformPlanResponse {
  id: number;
  infrastructure_request_id: number;
  status: "succeeded" | "failed";
  plan_fingerprint?: string;
  specification_fingerprint?: string;
  plan_summary: Record<string, unknown>;
  deployment_enabled: false;
  created_at: string;
  error_code?: string;
}

export async function generateTrustedTerraformPlan(
  infrastructureRequestId: number,
): Promise<TrustedTerraformPlanResponse> {
  return apiRequest<TrustedTerraformPlanResponse>(
    `/terraform/requests/${infrastructureRequestId}/plan`,
    { method: "POST" },
  );
}

export async function createApproval(
  infrastructureRequestId: number,
  terraformPlanRecordId: number,
): Promise<ApprovalResponse> {
  return apiRequest<ApprovalResponse>("/approvals", {
    method: "POST",
    body: JSON.stringify({
      infrastructure_request_id: infrastructureRequestId,
      terraform_plan_record_id: terraformPlanRecordId,
    }),
  });
}
