"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { BarChart3 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { UsageChart } from "@/components/usage-chart";
import { EmptyState } from "@/components/empty-state";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import type { UsageResponse } from "@/lib/types";

export default function UsagePage() {
  const client = useApiClient();
  const router = useRouter();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchUsage = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.getUsage();
      setUsage(res);
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
    fetchUsage();
  }, [fetchUsage]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-40 w-full" />
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
          (usage.monthly_steps_used / usage.monthly_step_limit) * 100
        )
      : 0;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Usage</h1>

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

      <UsageChart data={usage.daily_usage ?? []} />
    </div>
  );
}
