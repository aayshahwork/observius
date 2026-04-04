"use client";

import { RotateCcw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { RetryStatsResponse } from "@/lib/types";

interface RetryStatsCardProps {
  data: RetryStatsResponse;
}

export function RetryStatsCard({ data }: RetryStatsCardProps) {
  if (data.total_retried === 0) return null;

  const successColor =
    data.retry_success_rate >= 70
      ? "text-green-600 dark:text-green-400"
      : data.retry_success_rate >= 40
        ? "text-amber-600 dark:text-amber-400"
        : "text-red-600 dark:text-red-400";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <RotateCcw className="size-4 text-muted-foreground" />
          <CardTitle className="text-sm">Retry Intelligence</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-2xl font-bold tabular-nums">
              {data.total_retried}
            </div>
            <div className="text-xs text-muted-foreground">Tasks retried</div>
          </div>
          <div>
            <div className={cn("text-2xl font-bold tabular-nums", successColor)}>
              {Math.round(data.retry_success_rate)}%
            </div>
            <div className="text-xs text-muted-foreground">
              Recovery rate
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold tabular-nums">
              {data.avg_attempts.toFixed(1)}
            </div>
            <div className="text-xs text-muted-foreground">Avg attempts</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
