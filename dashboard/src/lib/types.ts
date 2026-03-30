export type TaskStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled";

export type ErrorCategory =
  | "transient_llm"
  | "rate_limited"
  | "transient_network"
  | "transient_browser"
  | "permanent_llm"
  | "permanent_browser"
  | "permanent_task"
  | "unknown";

export type ExecutorMode = "browser_use" | "native";

export interface TaskResponse {
  task_id: string;
  url: string | null;
  status: TaskStatus;
  success: boolean;
  result: Record<string, unknown> | null;
  error: string | null;
  replay_url: string | null;
  steps: number;
  duration_ms: number;
  created_at: string;
  completed_at: string | null;
  retry_count: number;
  retry_of_task_id: string | null;
  error_category: ErrorCategory | null;
  cost_cents: number;
  total_tokens_in: number;
  total_tokens_out: number;
  executor_mode: ExecutorMode;
}

export interface TaskListResponse {
  tasks: TaskResponse[];
  total: number;
  has_more: boolean;
}

export interface TaskCreateRequest {
  url: string;
  task: string;
  output_schema?: Record<string, unknown>;
  credentials?: Record<string, string>;
  timeout_seconds?: number;
  max_retries?: number;
  session_id?: string;
  webhook_url?: string;
  max_cost_cents?: number;
  executor_mode?: ExecutorMode;
}

export interface SessionResponse {
  session_id: string;
  origin_domain: string;
  auth_state: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string | null;
}

export interface ReplayResponse {
  task_id: string;
  replay_url: string;
}

export interface ErrorResponse {
  error_code: string;
  message: string;
  details?: string[];
}

export interface UsageResponse {
  monthly_steps_used: number;
  monthly_step_limit: number;
  tier: string;
  daily_usage?: { date: string; steps: number }[];
}

// Billing

export interface BillingUsageResponse {
  monthly_steps_used: number;
  monthly_step_limit: number;
  tier: string;
  billing_period_end: string | null;
}

export interface CheckoutResponse {
  checkout_url: string;
}

export interface PortalResponse {
  portal_url: string;
}

// API Keys

export interface ApiKeyResponse {
  id: string;
  key_prefix: string;
  key_suffix: string;
  label: string | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreateResponse {
  id: string;
  key: string;
  key_prefix: string;
  key_suffix: string;
  label: string | null;
  created_at: string;
}
