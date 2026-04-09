"use client";

import { useState, useEffect } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { ShieldCheck, AlertTriangle, Wrench, Globe } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiClient } from "@/hooks/use-api-client";
import { cn } from "@/lib/utils";
import type { ReliabilityAnalytics } from "@/lib/types";

// ---------------------------------------------------------------------------
// Color mapping for failure classes
// ---------------------------------------------------------------------------

function getFailureColor(cls: string): string {
  if (cls.includes("element") || cls.includes("obscured") || cls.includes("captcha")) {
    return "#f97316"; // orange — UI failures
  }
  if (cls.includes("network") || cls.includes("auth") || cls.includes("timeout")) {
    return "#ef4444"; // red — network/auth
  }
  if (cls.includes("goal") || cls.includes("stuck") || cls.includes("loop")) {
    return "#eab308"; // yellow — goal failures
  }
  if (cls.includes("policy") || cls.includes("crash")) {
    return "#a855f7"; // purple — policy/crash
  }
  return "#6b7280"; // gray fallback
}

function formatFailureLabel(cls: string): string {
  return cls.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ReliabilityScoreCard({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100);
  const color =
    pct >= 90
      ? "text-green-600 dark:text-green-400"
      : pct >= 70
        ? "text-amber-600 dark:text-amber-400"
        : "text-red-600 dark:text-red-400";
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Reliability Score
          </CardTitle>
          <ShieldCheck className="size-4 text-muted-foreground" />
        </div>
      </CardHeader>
      <CardContent>
        <span className={cn("text-3xl font-bold", color)}>{pct}%</span>
        <p className="text-xs text-muted-foreground mt-1">overall task success rate</p>
      </CardContent>
    </Card>
  );
}

function CircuitBreakerCard({ trips, avgRepairs }: { trips: number; avgRepairs: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Circuit Breaker
          </CardTitle>
          <AlertTriangle className="size-4 text-muted-foreground" />
        </div>
      </CardHeader>
      <CardContent>
        <span className={cn("text-3xl font-bold", trips > 0 ? "text-amber-600 dark:text-amber-400" : "text-green-600 dark:text-green-400")}>
          {trips}
        </span>
        <p className="text-xs text-muted-foreground mt-1">trips this period</p>
        <p className="text-xs text-muted-foreground mt-1">avg {avgRepairs} repairs/task</p>
      </CardContent>
    </Card>
  );
}

function RepairCard({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100);
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Repair Success Rate
          </CardTitle>
          <Wrench className="size-4 text-muted-foreground" />
        </div>
      </CardHeader>
      <CardContent>
        <span className={cn("text-3xl font-bold", pct >= 60 ? "text-green-600 dark:text-green-400" : "text-amber-600 dark:text-amber-400")}>
          {pct}%
        </span>
        <p className="text-xs text-muted-foreground mt-1">of repaired tasks succeeded</p>
      </CardContent>
    </Card>
  );
}

function FailureBreakdownChart({ distribution }: { distribution: Record<string, number> }) {
  const data = Object.entries(distribution)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 8)
    .map(([cls, count]) => ({ name: formatFailureLabel(cls), count, cls }));

  if (data.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        No failure data for this period
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 8 }}>
        <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={130} />
        <Tooltip
          contentStyle={{
            borderRadius: "0.5rem",
            fontSize: "0.75rem",
            backgroundColor: "hsl(var(--card))",
            borderColor: "hsl(var(--border))",
          }}
        />
        <Bar dataKey="count" radius={[0, 3, 3, 0]}>
          {data.map((entry) => (
            <Cell key={entry.cls} fill={getFailureColor(entry.cls)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function RepairEffectivenessChart({
  distribution,
}: {
  distribution: Record<string, { attempts: number; successes: number }>;
}) {
  const data = Object.entries(distribution)
    .filter(([, v]) => v.attempts > 0)
    .sort(([, a], [, b]) => b.attempts - a.attempts)
    .slice(0, 8)
    .map(([action, v]) => ({
      name: formatFailureLabel(action),
      rate: Math.round((v.successes / v.attempts) * 100),
      attempts: v.attempts,
    }));

  if (data.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        No repair data for this period
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 8 }}>
        <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={130} />
        <Tooltip
          formatter={(v) => [`${v}%`, "Success rate"]}
          contentStyle={{
            borderRadius: "0.5rem",
            fontSize: "0.75rem",
            backgroundColor: "hsl(var(--card))",
            borderColor: "hsl(var(--border))",
          }}
        />
        <Bar dataKey="rate" fill="var(--chart-2)" radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function TopDomainsTable({
  domains,
}: {
  domains: Array<{ domain: string; failure_count: number; top_failure: string }>;
}) {
  if (domains.length === 0) {
    return (
      <div className="flex h-20 items-center justify-center text-sm text-muted-foreground">
        No domain failures this period
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {domains.map((d) => (
        <div key={d.domain} className="flex items-center justify-between gap-2 text-sm">
          <div className="flex items-center gap-2 min-w-0">
            <Globe className="size-3.5 text-muted-foreground shrink-0" />
            <span className="truncate font-mono text-xs">{d.domain}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              {formatFailureLabel(d.top_failure)}
            </Badge>
            <span className="text-xs tabular-nums text-muted-foreground">{d.failure_count}×</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ReliabilitySection() {
  const client = useApiClient();
  const [data, setData] = useState<ReliabilityAnalytics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!client) return;
    client
      .getReliabilityAnalytics("7d")
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [client]);

  if (loading) {
    return (
      <div className="space-y-4">
        <h2 className="text-sm font-semibold text-muted-foreground">Reliability (7d)</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-56" />
          <Skeleton className="h-56" />
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-4">
      <h2 className="text-sm font-semibold text-muted-foreground">Reliability (7d)</h2>

      {/* Score cards */}
      <div className="grid gap-4 sm:grid-cols-3">
        <ReliabilityScoreCard rate={data.success_rate} />
        <RepairCard rate={data.repair_success_rate} />
        <CircuitBreakerCard trips={data.circuit_breaker_trips} avgRepairs={data.avg_repairs_per_task} />
      </div>

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Failure Breakdown</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <FailureBreakdownChart distribution={data.failure_distribution} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Repair Effectiveness</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <RepairEffectivenessChart distribution={data.repair_distribution} />
          </CardContent>
        </Card>
      </div>

      {/* Top failing domains */}
      {data.top_failing_domains.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Top Failing Domains</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <TopDomainsTable domains={data.top_failing_domains} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
