"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DailyCost } from "@/lib/usage-analytics";

interface CostChartProps {
  data: DailyCost[];
}

export function CostChart({ data }: CostChartProps) {
  const hasCostData = data.some((d) => d.cost > 0);

  if (data.length === 0 || !hasCostData) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Cost Over Time</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            Cost tracking was added recently. Data will appear as new tasks
            complete.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Cost Over Time (Last 30 Days)</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="5%"
                  stopColor="var(--chart-2)"
                  stopOpacity={0.3}
                />
                <stop
                  offset="95%"
                  stopColor="var(--chart-2)"
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v.slice(5)}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `$${v}`}
            />
            <Tooltip
              contentStyle={{
                borderRadius: "0.5rem",
                fontSize: "0.75rem",
              }}
              formatter={(value, name) => {
                const v = typeof value === "number" ? value : 0;
                if (name === "cost") return [`$${v.toFixed(2)}`, "Cost"];
                return [v, "Tasks"];
              }}
              labelFormatter={(label) => `Date: ${label}`}
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke="var(--chart-2)"
              fill="url(#costGradient)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
