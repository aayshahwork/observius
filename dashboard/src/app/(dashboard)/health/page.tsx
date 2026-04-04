"use client";

import { useState, useEffect, useCallback } from "react";
import { HeartPulse, RefreshCw } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { useApiClient } from "@/hooks/use-api-client";
import { PeriodSelector } from "@/components/health/period-selector";
import { HealthScoreCard } from "@/components/health/health-score-card";
import { MetricsBar } from "@/components/health/metrics-bar";
import { HourlyActivityChart } from "@/components/health/hourly-activity-chart";
import { FailureHotspots } from "@/components/health/failure-hotspots";
import { ErrorBreakdownChart } from "@/components/health/error-breakdown-chart";
import { ExecutorCards } from "@/components/health/executor-cards";
import { RetryStatsCard } from "@/components/health/retry-stats-card";
import type { AnalyticsPeriod, HealthAnalyticsResponse } from "@/lib/types";

export default function HealthPage() {
  const client = useApiClient();
  const [data, setData] = useState<HealthAnalyticsResponse | null>(null);
  const [period, setPeriod] = useState<AnalyticsPeriod>("24h");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!client) return;
    try {
      const result = await client.getHealthAnalytics(period);
      setData(result);
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load health data",
      );
    } finally {
      setLoading(false);
    }
  }, [client, period]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-28" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-72" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
        <Skeleton className="h-40" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Fleet Health</h1>
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <EmptyState
        icon={HeartPulse}
        title="No health data"
        description="Health analytics will appear once tasks are running."
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Fleet Health</h1>
        <div className="flex items-center gap-3">
          <PeriodSelector period={period} onChange={setPeriod} />
          <Button
            variant="outline"
            size="sm"
            disabled={refreshing}
            onClick={async () => {
              setRefreshing(true);
              await fetchData();
              setRefreshing(false);
            }}
          >
            <RefreshCw
              className={`mr-2 size-4 ${refreshing ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </div>
      </div>

      {/* Health Score */}
      <HealthScoreCard
        successRate={data.success_rate}
        trend={data.success_rate_trend}
        totalRuns={data.total_runs}
      />

      {/* Metrics Bar */}
      <MetricsBar data={data} />

      {/* Hourly Activity */}
      <HourlyActivityChart data={data.hourly_breakdown} period={period} />

      {/* Two-column: Failure Hotspots + Error Breakdown */}
      <div className="grid gap-4 lg:grid-cols-2">
        <FailureHotspots data={data.top_failing_urls} />
        <ErrorBreakdownChart data={data.top_errors} />
      </div>

      {/* Executor Performance */}
      <ExecutorCards data={data.executor_breakdown} />

      {/* Retry Intelligence */}
      <RetryStatsCard data={data.retry_stats} />

      {/* Active Alerts */}
      {data.alerts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Active Alerts</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="space-y-3">
              {data.alerts.map((alert) => (
                <div
                  key={alert.id}
                  className="flex items-center justify-between gap-2"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="destructive" className="text-[10px]">
                      {alert.alert_type}
                    </Badge>
                    <span className="text-sm">{alert.message}</span>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatDistanceToNow(new Date(alert.created_at), {
                      addSuffix: true,
                    })}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
