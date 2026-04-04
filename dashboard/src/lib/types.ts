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

export type ExecutorMode = "browser_use" | "native" | "sdk";

export interface AnalysisFinding {
  tier: number;
  category: string;
  summary: string;
  suggestion: string;
  confidence: number;
}

export interface AttemptDiagnosis {
  category: string;
  subcategory: string;
  root_cause: string;
  retry_hint: string;
  analysis_cost_cents: number;
  analysis_method: string;
  confidence: number;
  is_retryable: boolean;
}

export interface AttemptRecoveryPlan {
  should_retry: boolean;
  fresh_browser: boolean;
  stealth_mode: boolean;
  clear_cookies: boolean;
  increase_timeout: boolean;
  reduce_max_actions: boolean;
  extend_system_message: string;
  modified_task: string;
}

export interface RetryAttempt {
  attempt: number;
  status: string;
  diagnosis: AttemptDiagnosis | null;
  recovery_plan: AttemptRecoveryPlan | null;
}

export interface RunAnalysis {
  summary: string;
  primary_suggestion: string;
  findings: AnalysisFinding[];
  wasted_steps: number;
  wasted_cost_cents: number;
  tiers_executed: number[];
  // Adaptive retry (AR3) — present when wrap() used adaptive retry
  attempts?: RetryAttempt[];
  total_attempts?: number;
  adaptive_retry_used?: boolean;
}

export interface CompiledSelector {
  type: string;
  value: string;
  confidence: number;
}

export interface CompiledStep {
  action_type: string;
  selectors: CompiledSelector[];
  fill_value_template: string;
  expected_url_pattern: string;
  expected_element: string;
  expected_text: string;
  intent: string;
  timeout_ms: number;
  pre_url: string;
}

export interface CompiledWorkflow {
  name: string;
  steps: CompiledStep[];
  start_url: string;
  parameters: Record<string, string>;
  source_task_id: string;
  compiled_at: string;
}

export interface TaskResponse {
  task_id: string;
  url: string | null;
  task_description: string | null;
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
  analysis?: RunAnalysis | null;
  compiled_workflow?: CompiledWorkflow | null;
  playwright_script?: string | null;
}

export interface TaskListResponse {
  tasks: TaskResponse[];
  total: number;
  has_more: boolean;
}

export interface StepResponse {
  step_number: number;
  action_type: string;
  description: string | null;
  screenshot_url: string | null;
  tokens_in: number;
  tokens_out: number;
  duration_ms: number;
  success: boolean;
  error: string | null;
  created_at: string | null;
  context?: Record<string, unknown> | null;
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

// Scripts

export interface ScriptEntry {
  task_id: string;
  task_description: string;
  url: string;
  status: string;
  created_at: string | null;
  playwright_script: string;
}

export interface ScriptListResponse {
  scripts: ScriptEntry[];
  total: number;
  has_more: boolean;
}

// Alerts

export interface AlertResponse {
  id: string;
  alert_type: string;
  message: string;
  task_id: string | null;
  acknowledged: boolean;
  created_at: string;
}

export interface AlertListResponse {
  alerts: AlertResponse[];
  total: number;
  has_more: boolean;
}

// Health Analytics

export type AnalyticsPeriod = "1h" | "6h" | "24h" | "7d" | "30d";

export interface ErrorCategoryCount {
  category: string;
  count: number;
}

export interface FailingUrl {
  url: string;
  failure_count: number;
  last_failure: string;
}

export interface HourlyBucket {
  hour: string;
  completed: number;
  failed: number;
  cost_cents: number;
}

export interface ExecutorStatsResponse {
  count: number;
  success_rate: number;
  avg_cost: number;
}

export interface ExecutorBreakdown {
  browser_use: ExecutorStatsResponse;
  native: ExecutorStatsResponse;
  sdk: ExecutorStatsResponse;
}

export interface RetryStatsResponse {
  total_retried: number;
  retry_success_rate: number;
  avg_attempts: number;
}

export interface AlertSummary {
  id: string;
  alert_type: string;
  message: string;
  created_at: string;
}

export interface HealthAnalyticsResponse {
  period: AnalyticsPeriod;
  total_runs: number;
  completed: number;
  failed: number;
  timeout: number;
  success_rate: number;
  success_rate_trend: number;
  total_cost_cents: number;
  avg_cost_per_run: number;
  total_tokens: number;
  avg_duration_ms: number;
  top_errors: ErrorCategoryCount[];
  top_failing_urls: FailingUrl[];
  hourly_breakdown: HourlyBucket[];
  executor_breakdown: ExecutorBreakdown;
  retry_stats: RetryStatsResponse;
  alerts: AlertSummary[];
}
