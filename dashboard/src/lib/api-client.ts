import { api } from "./api";
import type {
  TaskResponse,
  TaskListResponse,
  TaskCreateRequest,
  SessionResponse,
  ReplayResponse,
  ErrorResponse,
  UsageResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  error_code: string;
  details?: string[];

  constructor(status: number, response: ErrorResponse) {
    super(response.message);
    this.name = "ApiError";
    this.status = status;
    this.error_code = response.error_code;
    this.details = response.details;
  }
}

function extractStatus(error: unknown): number {
  if (error instanceof Error) {
    const match = error.message.match(/API error: (\d+)/);
    if (match) return parseInt(match[1], 10);
  }
  return 500;
}

async function apiCall<T>(path: string, options: { method?: string; body?: unknown; headers: Record<string, string> }): Promise<T> {
  try {
    return await api<T>(path, options);
  } catch (error) {
    const status = extractStatus(error);
    // Try to provide a typed error
    throw new ApiError(status, {
      error_code: status === 401 ? "UNAUTHORIZED" : status === 404 ? "NOT_FOUND" : "INTERNAL_ERROR",
      message: error instanceof Error ? error.message : "Unknown error",
    });
  }
}

export class ApiClient {
  private headers: Record<string, string>;

  constructor(apiKey: string) {
    this.headers = { "X-API-Key": apiKey };
  }

  // Tasks
  async listTasks(params?: {
    limit?: number;
    offset?: number;
    status?: string;
    since?: string;
  }): Promise<TaskListResponse> {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    if (params?.status) qs.set("status", params.status);
    if (params?.since) qs.set("since", params.since);
    const query = qs.toString();
    return apiCall<TaskListResponse>(
      `/api/v1/tasks${query ? `?${query}` : ""}`,
      { headers: this.headers }
    );
  }

  async getTask(taskId: string): Promise<TaskResponse> {
    return apiCall<TaskResponse>(`/api/v1/tasks/${taskId}`, {
      headers: this.headers,
    });
  }

  async createTask(body: TaskCreateRequest): Promise<TaskResponse> {
    return apiCall<TaskResponse>("/api/v1/tasks", {
      method: "POST",
      body,
      headers: this.headers,
    });
  }

  async cancelTask(
    taskId: string
  ): Promise<{ task_id: string; status: string }> {
    return apiCall(`/api/v1/tasks/${taskId}`, {
      method: "DELETE",
      headers: this.headers,
    });
  }

  async retryTask(taskId: string): Promise<TaskResponse> {
    return apiCall<TaskResponse>(`/api/v1/tasks/${taskId}/retry`, {
      method: "POST",
      headers: this.headers,
    });
  }

  async getReplay(taskId: string): Promise<ReplayResponse> {
    return apiCall<ReplayResponse>(`/api/v1/tasks/${taskId}/replay`, {
      headers: this.headers,
    });
  }

  // Sessions
  // Note: List endpoint doesn't exist yet — returns [] on 404, throws on 401/500
  async listSessions(): Promise<SessionResponse[]> {
    try {
      return await apiCall<SessionResponse[]>("/api/v1/sessions", {
        headers: this.headers,
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return [];
      throw error;
    }
  }

  async getSession(sessionId: string): Promise<SessionResponse> {
    return apiCall<SessionResponse>(`/api/v1/sessions/${sessionId}`, {
      headers: this.headers,
    });
  }

  async deleteSession(
    sessionId: string
  ): Promise<{ session_id: string; message: string }> {
    return apiCall(`/api/v1/sessions/${sessionId}`, {
      method: "DELETE",
      headers: this.headers,
    });
  }

  // Usage
  // Note: Endpoint doesn't exist yet — returns defaults on 404, throws on 401/500
  async getUsage(): Promise<UsageResponse> {
    try {
      return await apiCall<UsageResponse>("/api/v1/account/usage", {
        headers: this.headers,
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return {
          monthly_steps_used: 0,
          monthly_step_limit: 500,
          tier: "free",
          daily_usage: [],
        };
      }
      throw error;
    }
  }

  // Validate API key by attempting to list tasks
  async validateKey(): Promise<boolean> {
    try {
      await this.listTasks({ limit: 1 });
      return true;
    } catch {
      return false;
    }
  }
}
