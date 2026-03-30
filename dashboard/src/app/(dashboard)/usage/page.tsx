"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { BarChart3, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { UsageChart } from "@/components/usage-chart";
import { CostChart } from "@/components/cost-chart";
import { ErrorChart } from "@/components/error-chart";
import { ExecutorComparisonCards } from "@/components/executor-comparison";
import { ExpensiveTasksTable } from "@/components/expensive-tasks-table";
import { EmptyState } from "@/components/empty-state";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import { formatCost, formatTokens } from "@/lib/utils";
import type { UsageResponse, TaskResponse } from "@/lib/types";
import {
  computeSummaryStats,
  computeDailyCosts,
  computeErrorDistribution,
  computeRetryStats,
  computeExecutorComparison,
} from "@/lib/usage-analytics";

export default function UsagePage() {
  const client = useApiClient();
  const router = useRouter();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [tasks, setTasks] = useState<TaskResponse[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!client) return;
    try {
      const thirtyDaysAgo = new Date();
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

      // TODO: Need server-side analytics endpoint for accurate aggregation over large datasets
      const [usageRes, tasksRes] = await Promise.all([
        client.getUsage(),
        client.listTasks({
          limit: 100,
          since: thirtyDaysAgo.toISOString(),
        }),
      ]);

      setUsage(usageRes);
      setTasks(tasksRes.tasks);
      setHasMore(tasksRes.has_more);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to fetch usage");
    } finally {
      setLoading(false);
    }
  }, [client, router]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-40 w-full" />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Usage</h1>
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  if (!usage) {
    return (
      <EmptyState
        icon={BarChart3}
        title="Usage data unavailable"
        description="Usage tracking is not available yet."
      />
    );
  }

  const usagePercent =
    usage.monthly_step_limit > 0
      ? Math.round(
          (usage.monthly_steps_used / usage.monthly_step_limit) * 100,
        )
      : 0;

  const summary = computeSummaryStats(tasks);
  const dailyCosts = computeDailyCosts(tasks);
  const errorDist = computeErrorDistribution(tasks);
  const retryStats = computeRetryStats(tasks);
  const executorComp = computeExecutorComparison(tasks);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Usage</h1>

      {hasMore && (
        <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 px-4 py-2 text-sm text-muted-foreground">
          <Info className="h-4 w-4 shrink-0" />
          Showing analytics for the most recent 100 tasks.
        </div>
      )}

      {/* Section 1: Existing Monthly Steps (preserved) */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Monthly Steps</CardTitle>
            <Badge variant="secondary" className="capitalize">
              {usage.tier}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <Progress value={usagePercent}>
            <ProgressLabel>
              {usage.monthly_steps_used.toLocaleString()} of{" "}
              {usage.monthly_step_limit.toLocaleString()} steps
            </ProgressLabel>
            <ProgressValue />
          </Progress>
        </CardContent>
      </Card>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <CardContent className="pt-6">
            <div className="text-sm text-muted-foreground">Monthly Cost</div>
            <div className="mt-1 text-2xl font-semibold">
              {formatCost(summary.totalCostCents)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="text-sm text-muted-foreground">
              Avg Cost / Task
            </div>
            <div className="mt-1 text-2xl font-semibold">
              {formatCost(summary.avgCostCents)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="text-sm text-muted-foreground">Token Usage</div>
            <div className="mt-1 text-2xl font-semibold">
              {formatTokens(summary.totalTokens)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="text-sm text-muted-foreground">Retry Rate</div>
            <div className="mt-1 text-2xl font-semibold">
              {summary.totalTasks > 0
                ? `${summary.retryRate.toFixed(1)}%`
                : "\u2014"}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Section 2: Daily Step Usage (existing chart, preserved) */}
      <UsageChart data={usage.daily_usage ?? []} />

      {/* Section 3: Cost Over Time */}
      <CostChart data={dailyCosts} />

      {/* Section 4: Error Distribution (only when failed tasks exist) */}
      <ErrorChart distribution={errorDist} retryStats={retryStats} />

      {/* Section 5: Executor Comparison (only when native tasks exist) */}
      {executorComp && <ExecutorComparisonCards comparison={executorComp} />}

      {/* Section 6: Most Expensive Tasks */}
      <ExpensiveTasksTable tasks={tasks} />
    </div>
  );
}
