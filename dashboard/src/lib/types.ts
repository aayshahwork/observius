export type TaskStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled";

export interface TaskResponse {
  task_id: string;
  status: TaskStatus;
  success: boolean;
  result: Record<string, unknown> | null;
  error: string | null;
  replay_url: string | null;
  steps: number;
  duration_ms: number;
  created_at: string;
  completed_at: string | null;
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
