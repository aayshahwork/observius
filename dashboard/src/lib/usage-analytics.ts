import type { TaskResponse, ErrorCategory } from "./types";

// ── Types ──────────────────────────────────────────────────────────────

export interface SummaryStats {
  totalCostCents: number;
  avgCostCents: number;
  totalTokens: number;
  retryRate: number;
  totalTasks: number;
}

export interface DailyCost {
  date: string;
  cost: number;
  taskCount: number;
}

export interface ErrorDistributionEntry {
  category: ErrorCategory;
  label: string;
  count: number;
  percentage: number;
  color: string;
}

export interface RetryStats {
  retrySuccessRate: number;
  totalRetried: number;
  mostCommonFailure: {
    category: ErrorCategory;
    label: string;
    count: number;
    url: string | null;
  } | null;
}

export interface ExecutorStats {
  avgCostCents: number;
  avgDurationMs: number;
  count: number;
}

export interface ExecutorComparison {
  browser_use: ExecutorStats;
  native: ExecutorStats;
}

// ── Constants ──────────────────────────────────────────────────────────

const ERROR_CHART_COLORS: Record<string, string> = {
  transient_llm: "#f59e0b",
  transient_network: "#f59e0b",
  transient_browser: "#f59e0b",
  rate_limited: "#a855f7",
  permanent_llm: "#ef4444",
  permanent_browser: "#ef4444",
  permanent_task: "#ef4444",
  unknown: "#6b7280",
};

const ERROR_LABELS: Record<ErrorCategory, string> = {
  transient_llm: "Transient (LLM)",
  rate_limited: "Rate Limited",
  transient_network: "Transient (Network)",
  transient_browser: "Transient (Browser)",
  permanent_llm: "Permanent (LLM)",
  permanent_browser: "Permanent (Browser)",
  permanent_task: "Permanent (Task)",
  unknown: "Unknown",
};

// ── Computations ───────────────────────────────────────────────────────

export function computeSummaryStats(tasks: TaskResponse[]): SummaryStats {
  const totalCostCents = tasks.reduce((sum, t) => sum + (t.cost_cents || 0), 0);
  const totalTokens = tasks.reduce(
    (sum, t) => sum + (t.total_tokens_in || 0) + (t.total_tokens_out || 0),
    0,
  );
  const tasksWithRetries = tasks.filter((t) => t.retry_count > 0).length;

  return {
    totalCostCents,
    avgCostCents: tasks.length > 0 ? totalCostCents / tasks.length : 0,
    totalTokens,
    retryRate: tasks.length > 0 ? (tasksWithRetries / tasks.length) * 100 : 0,
    totalTasks: tasks.length,
  };
}

export function computeDailyCosts(tasks: TaskResponse[]): DailyCost[] {
  const byDate = new Map<string, { cost: number; count: number }>();

  for (const task of tasks) {
    const date = task.created_at.slice(0, 10);
    const entry = byDate.get(date) ?? { cost: 0, count: 0 };
    entry.cost += (task.cost_cents || 0) / 100;
    entry.count += 1;
    byDate.set(date, entry);
  }

  return Array.from(byDate.entries())
    .map(([date, { cost, count }]) => ({
      date,
      cost: Math.round(cost * 100) / 100,
      taskCount: count,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function computeErrorDistribution(
  tasks: TaskResponse[],
): ErrorDistributionEntry[] {
  const failed = tasks.filter(
    (t) => t.status === "failed" && t.error_category,
  );
  if (failed.length === 0) return [];

  const counts = new Map<ErrorCategory, number>();
  for (const t of failed) {
    if (t.error_category) {
      counts.set(t.error_category, (counts.get(t.error_category) || 0) + 1);
    }
  }

  return Array.from(counts.entries())
    .map(([category, count]) => ({
      category,
      label: ERROR_LABELS[category] ?? category,
      count,
      percentage: Math.round((count / failed.length) * 100),
      color: ERROR_CHART_COLORS[category] ?? "#6b7280",
    }))
    .sort((a, b) => b.count - a.count);
}

export function computeRetryStats(tasks: TaskResponse[]): RetryStats {
  const retriedTasks = tasks.filter((t) => t.retry_of_task_id != null);
  const successfulRetries = retriedTasks.filter(
    (t) => t.status === "completed",
  );

  const failed = tasks.filter(
    (t) => t.status === "failed" && t.error_category,
  );
  let mostCommonFailure: RetryStats["mostCommonFailure"] = null;

  if (failed.length > 0) {
    const byCat = new Map<
      ErrorCategory,
      { count: number; urls: Map<string, number> }
    >();
    for (const t of failed) {
      if (!t.error_category) continue;
      const entry = byCat.get(t.error_category) ?? {
        count: 0,
        urls: new Map(),
      };
      entry.count += 1;
      if (t.url) {
        entry.urls.set(t.url, (entry.urls.get(t.url) || 0) + 1);
      }
      byCat.set(t.error_category, entry);
    }

    let maxCat: ErrorCategory | null = null;
    let maxCount = 0;
    for (const [cat, { count }] of byCat) {
      if (count > maxCount) {
        maxCount = count;
        maxCat = cat;
      }
    }

    if (maxCat) {
      const entry = byCat.get(maxCat)!;
      let topUrl: string | null = null;
      let topUrlCount = 0;
      for (const [url, count] of entry.urls) {
        if (count > topUrlCount) {
          topUrlCount = count;
          topUrl = url;
        }
      }
      mostCommonFailure = {
        category: maxCat,
        label: ERROR_LABELS[maxCat] ?? maxCat,
        count: maxCount,
        url: topUrl,
      };
    }
  }

  return {
    retrySuccessRate:
      retriedTasks.length > 0
        ? Math.round((successfulRetries.length / retriedTasks.length) * 100)
        : 0,
    totalRetried: retriedTasks.length,
    mostCommonFailure,
  };
}

export function computeExecutorComparison(
  tasks: TaskResponse[],
): ExecutorComparison | null {
  const nativeTasks = tasks.filter((t) => t.executor_mode === "native");
  if (nativeTasks.length === 0) return null;

  const browserTasks = tasks.filter(
    (t) => t.executor_mode === "browser_use" || !t.executor_mode,
  );

  function stats(list: TaskResponse[]): ExecutorStats {
    if (list.length === 0)
      return { avgCostCents: 0, avgDurationMs: 0, count: 0 };
    const totalCost = list.reduce((s, t) => s + (t.cost_cents || 0), 0);
    const totalDuration = list.reduce((s, t) => s + (t.duration_ms || 0), 0);
    return {
      avgCostCents: totalCost / list.length,
      avgDurationMs: totalDuration / list.length,
      count: list.length,
    };
  }

  return {
    browser_use: stats(browserTasks),
    native: stats(nativeTasks),
  };
}
