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
        <div className="grid grid-cols-4 gap-4">
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
          <div>
            <div className="text-2xl font-bold tabular-nums">
              ${((data.total_diagnosis_cost_cents ?? 0) / 100).toFixed(2)}
            </div>
            <div className="text-xs text-muted-foreground">Diagnosis cost</div>
          </div>
        </div>

        {data.category_counts && Object.keys(data.category_counts).length > 0 && (
          <div className="mt-4 space-y-2">
            <h4 className="text-sm font-medium">Failure Categories</h4>
            {Object.entries(data.category_counts)
              .sort(([, a], [, b]) => b - a)
              .map(([category, count]) => {
                const total = Object.values(data.category_counts!).reduce((s, v) => s + v, 0);
                const pct = (count / total) * 100;
                const colors: Record<string, string> = {
                  element_interaction: "bg-yellow-500",
                  navigation: "bg-blue-500",
                  anti_bot: "bg-red-500",
                  authentication: "bg-purple-500",
                  content_mismatch: "bg-orange-500",
                  agent_loop: "bg-pink-500",
                  agent_reasoning: "bg-indigo-500",
                  infrastructure: "bg-gray-500",
                  transient_llm: "bg-sky-500",
                  rate_limited: "bg-amber-500",
                  transient_network: "bg-teal-500",
                  transient_browser: "bg-cyan-500",
                  permanent_llm: "bg-rose-500",
                  permanent_browser: "bg-red-700",
                  permanent_task: "bg-red-900",
                };
                return (
                  <div key={category} className="flex items-center gap-2 text-xs">
                    <span className="w-32 truncate text-muted-foreground">
                      {category.replace(/_/g, " ")}
                    </span>
                    <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className={cn("h-full rounded-full", colors[category] ?? "bg-gray-500")}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-8 text-right text-muted-foreground">{count}</span>
                  </div>
                );
              })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
