"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  LabelList,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { ErrorDistributionEntry, RetryStats } from "@/lib/usage-analytics";
import { getErrorCategoryLabel } from "@/lib/utils";

interface ErrorChartProps {
  distribution: ErrorDistributionEntry[];
  retryStats: RetryStats;
}

export function ErrorChart({ distribution, retryStats }: ErrorChartProps) {
  if (distribution.length === 0) return null;

  const chartData = distribution.map((d) => ({
    ...d,
    displayLabel: `${d.count} (${d.percentage}%)`,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Failure Categories (Last 30 Days)
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6 pt-0">
        <ResponsiveContainer
          width="100%"
          height={Math.max(160, distribution.length * 44)}
        >
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ left: 0, right: 60 }}
          >
            <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="label"
              tick={{ fontSize: 11 }}
              width={140}
            />
            <Tooltip
              contentStyle={{
                borderRadius: "0.5rem",
                fontSize: "0.75rem",
              }}
              formatter={(value) => [typeof value === "number" ? value : 0, "Failures"]}
            />
            <Bar dataKey="count" radius={[0, 4, 4, 0]}>
              <LabelList
                dataKey="displayLabel"
                position="right"
                style={{ fontSize: 11, fill: "currentColor" }}
              />
              {chartData.map((entry) => (
                <Cell key={entry.category} fill={entry.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>

        <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          {retryStats.totalRetried > 0 && (
            <div>
              <span className="text-muted-foreground">
                Retry Success Rate:{" "}
              </span>
              <span className="font-medium">
                {retryStats.retrySuccessRate}% of auto-retried tasks eventually
                succeeded
              </span>
            </div>
          )}
          {retryStats.mostCommonFailure && (
            <div>
              <span className="text-muted-foreground">
                Most Common Failure:{" "}
              </span>
              <Badge variant="secondary" className="ml-1 font-normal">
                {getErrorCategoryLabel(retryStats.mostCommonFailure.category)}
              </Badge>
              <span className="ml-1 text-muted-foreground">
                ({retryStats.mostCommonFailure.count} occurrences)
              </span>
              {retryStats.mostCommonFailure.url && (
                <span className="ml-1 max-w-[200px] truncate text-xs text-muted-foreground">
                  {" "}
                  &mdash; {retryStats.mostCommonFailure.url}
                </span>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
