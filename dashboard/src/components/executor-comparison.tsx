"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ExecutorComparison } from "@/lib/usage-analytics";
import { formatCost, formatDuration } from "@/lib/utils";

interface ExecutorComparisonProps {
  comparison: ExecutorComparison;
}

export function ExecutorComparisonCards({
  comparison,
}: ExecutorComparisonProps) {
  const modes = [
    { key: "browser_use", label: "Browser Use", stats: comparison.browser_use },
    { key: "native", label: "Native", stats: comparison.native },
  ] as const;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Browser Use vs Native Executor
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="grid grid-cols-2 gap-4">
          {modes.map(({ key, label, stats }) => (
            <div key={key} className="space-y-2 rounded-lg border p-4">
              <div className="text-sm font-medium">{label}</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Avg Cost</span>
                  <span className="font-medium">
                    {formatCost(stats.avgCostCents)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Avg Duration</span>
                  <span className="font-medium">
                    {formatDuration(Math.round(stats.avgDurationMs))}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total Tasks</span>
                  <span className="font-medium">{stats.count}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
