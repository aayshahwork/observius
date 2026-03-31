"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  Loader2,
  Activity,
  TrendingUp,
  DollarSign,
  Globe,
  Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/status-badge";
import { useApiClient } from "@/hooks/use-api-client";

import {
  cn,
  formatCost,
  getErrorCategoryColor,
  getErrorCategoryLabel,
} from "@/lib/utils";
import type { TaskResponse, SessionResponse, ErrorCategory } from "@/lib/types";

function truncateUrl(url: string, maxLen = 30): string {
  try {
    const u = new URL(url);
    const display = u.hostname + u.pathname;
    return display.length > maxLen
      ? display.slice(0, maxLen) + "\u2026"
      : display;
  } catch {
    return url.length > maxLen ? url.slice(0, maxLen) + "\u2026" : url;
  }
}

export default function OverviewPage() {
  const client = useApiClient();
  const router = useRouter();

  const [tasks, setTasks] = useState<TaskResponse[]>([]);
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  // ── Data fetching ──────────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    if (!client) return;
    try {
      const since = new Date();
      since.setDate(since.getDate() - 7);
      const [taskRes, sessionRes] = await Promise.all([
        client.listTasks({ limit: 100, since: since.toISOString() }),
        client.listSessions(),
      ]);
      setTasks(taskRes.tasks);
      setSessions(sessionRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  // ── Derived metrics ────────────────────────────────────────────────
  const metrics = useMemo(() => {
    const now = new Date();
    const startOfToday = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate()
    );
    const startOfYesterday = new Date(startOfToday);
    startOfYesterday.setDate(startOfYesterday.getDate() - 1);
    const twentyFourHoursAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);

    const todayTasks = tasks.filter(
      (t) => new Date(t.created_at) >= startOfToday
    );
    const yesterdayTasks = tasks.filter((t) => {
      const d = new Date(t.created_at);
      return d >= startOfYesterday && d < startOfToday;
    });

    const recentFinished = tasks.filter((t) => {
      const d = new Date(t.created_at);
      return (
        d >= twentyFourHoursAgo &&
        (t.status === "completed" || t.status === "failed")
      );
    });
    const completedCount = recentFinished.filter(
      (t) => t.status === "completed"
    ).length;
    const successRate =
      recentFinished.length > 0
        ? Math.round((completedCount / recentFinished.length) * 100)
        : null;

    const costToday = todayTasks.reduce(
      (sum, t) => sum + (t.cost_cents ?? 0),
      0
    );

    const activeSessions = sessions.filter(
      (s) => s.auth_state !== "stale" && s.auth_state !== "expired"
    ).length;

    const chartData: { date: string; completed: number; failed: number }[] = [];
    for (let i = 6; i >= 0; i--) {
      const date = new Date(startOfToday);
      date.setDate(date.getDate() - i);
      const nextDate = new Date(date);
      nextDate.setDate(nextDate.getDate() + 1);
      const dayTasks = tasks.filter((t) => {
        const d = new Date(t.created_at);
        return d >= date && d < nextDate;
      });
      chartData.push({
        date: date.toISOString().slice(0, 10),
        completed: dayTasks.filter((t) => t.status === "completed").length,
        failed: dayTasks.filter((t) => t.status === "failed").length,
      });
    }

    const recentFailures = tasks
      .filter((t) => t.status === "failed")
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
      .slice(0, 5);

    const retryActivity = tasks
      .filter((t) => t.retry_count > 0)
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
      .slice(0, 5);

    return {
      tasksToday: todayTasks.length,
      tasksTrend: todayTasks.length - yesterdayTasks.length,
      hasYesterday: yesterdayTasks.length > 0,
      successRate,
      costToday,
      activeSessions,
      chartData,
      recentFailures,
      retryActivity,
    };
  }, [tasks, sessions]);

  // ── Retry handler ──────────────────────────────────────────────────
  const handleRetry = async (taskId: string) => {
    if (!client) return;
    setRetryingId(taskId);
    try {
      await client.retryTask(taskId);
      await fetchData();
    } catch {
      // Retry failed — loading state cleared in finally
    } finally {
      setRetryingId(null);
    }
  };

  // ── Loading skeleton ───────────────────────────────────────────────
  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-32" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-72" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  // ── Error state ────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Overview</h1>
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Overview</h1>
        <Button onClick={() => router.push("/tasks/new")}>
          <Plus className="mr-2 size-4" />
          New Task
        </Button>
      </div>

      {/* ── Metric cards ──────────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Tasks Today
              </CardTitle>
              <Activity className="size-4 text-muted-foreground" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold">{metrics.tasksToday}</span>
              {metrics.hasYesterday && metrics.tasksTrend !== 0 && (
                <span
                  className={cn(
                    "text-sm",
                    metrics.tasksTrend > 0
                      ? "text-green-600 dark:text-green-400"
                      : "text-red-600 dark:text-red-400"
                  )}
                >
                  {metrics.tasksTrend > 0 ? "\u2191" : "\u2193"}{" "}
                  {Math.abs(metrics.tasksTrend)} vs yesterday
                </span>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Success Rate (24h)
              </CardTitle>
              <TrendingUp className="size-4 text-muted-foreground" />
            </div>
          </CardHeader>
          <CardContent>
            {metrics.successRate !== null ? (
              <span
                className={cn(
                  "text-2xl font-bold",
                  metrics.successRate > 90
                    ? "text-green-600 dark:text-green-400"
                    : metrics.successRate >= 70
                      ? "text-amber-600 dark:text-amber-400"
                      : "text-red-600 dark:text-red-400"
                )}
              >
                {metrics.successRate}%
              </span>
            ) : (
              <span className="text-2xl font-bold text-muted-foreground">
                —
              </span>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Cost Today
              </CardTitle>
              <DollarSign className="size-4 text-muted-foreground" />
            </div>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              {formatCost(metrics.costToday)}
            </span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Active Sessions
              </CardTitle>
              <Globe className="size-4 text-muted-foreground" />
            </div>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">
              {metrics.activeSessions}
            </span>
          </CardContent>
        </Card>
      </div>

      {/* ── Task activity chart ───────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            Task Activity (Last 7 Days)
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {metrics.chartData.every(
            (d) => d.completed === 0 && d.failed === 0
          ) ? (
            <div className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              <p>No task activity in the last 7 days</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => router.push("/tasks/new")}
              >
                Create your first task
              </Button>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={metrics.chartData}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    borderRadius: "0.5rem",
                    fontSize: "0.75rem",
                    backgroundColor: "hsl(var(--card))",
                    borderColor: "hsl(var(--border))",
                  }}
                  labelStyle={{ color: "hsl(var(--foreground))" }}
                />
                <Area
                  type="monotone"
                  dataKey="completed"
                  stackId="1"
                  stroke="var(--chart-2)"
                  fill="var(--chart-2)"
                  fillOpacity={0.3}
                />
                <Area
                  type="monotone"
                  dataKey="failed"
                  stackId="1"
                  stroke="var(--chart-5)"
                  fill="var(--chart-5)"
                  fillOpacity={0.3}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* ── Bottom panels ─────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Recent Failures */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent Failures</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            {metrics.recentFailures.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                No failures in the last 24 hours
              </div>
            ) : (
              <div className="space-y-3">
                {metrics.recentFailures.map((task) => (
                  <div
                    key={task.task_id}
                    className="flex items-center justify-between gap-2"
                  >
                    <div
                      className="flex min-w-0 flex-1 cursor-pointer items-center gap-2"
                      onClick={() => router.push(`/tasks/${task.task_id}`)}
                    >
                      <span className="truncate text-sm font-medium">
                        {task.replay_url
                          ? truncateUrl(task.replay_url)
                          : task.task_id.slice(0, 8)}
                      </span>
                      {task.error_category && (
                        <Badge
                          variant="secondary"
                          className={cn(
                            "shrink-0 text-[10px]",
                            getErrorCategoryColor(
                              task.error_category as ErrorCategory
                            )
                          )}
                        >
                          {getErrorCategoryLabel(
                            task.error_category as ErrorCategory
                          )}
                        </Badge>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className="text-xs text-muted-foreground">
                        {formatDistanceToNow(new Date(task.created_at), {
                          addSuffix: true,
                        })}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        disabled={retryingId === task.task_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRetry(task.task_id);
                        }}
                      >
                        {retryingId === task.task_id ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : (
                          "Retry"
                        )}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Retry Activity */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Retry Activity</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            {metrics.retryActivity.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                No retry activity
              </div>
            ) : (
              <div className="space-y-3">
                {metrics.retryActivity.map((task) => (
                  <div
                    key={task.task_id}
                    className="flex cursor-pointer items-center justify-between gap-2"
                    onClick={() => router.push(`/tasks/${task.task_id}`)}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium">
                        {task.replay_url
                          ? truncateUrl(task.replay_url)
                          : task.task_id.slice(0, 8)}
                      </span>
                      <Badge
                        variant="outline"
                        className="shrink-0 text-[10px]"
                      >
                        Attempt {task.retry_count + 1}
                      </Badge>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <StatusBadge status={task.status} />
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {formatCost(task.cost_cents)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
