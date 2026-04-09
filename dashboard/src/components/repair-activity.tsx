"use client";

import { useState } from "react";
import { Wrench, ChevronDown, ChevronUp, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StepResponse } from "@/lib/types";

const FAILURE_CLASS_LABELS: Record<string, string> = {
  element_not_found: "Element Not Found",
  auth_required: "Auth Required",
  captcha_challenge: "Captcha Challenge",
  navigation_loop: "Navigation Loop",
  element_obscured: "Element Obscured",
  network_timeout: "Network Timeout",
  page_crash: "Page Crash",
  goal_not_met: "Goal Not Met",
  policy_violation: "Policy Violation",
  stuck_state: "Stuck State",
};

function getFailureLabel(cls: string): string {
  return FAILURE_CLASS_LABELS[cls] ?? cls.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function getActionLabel(action: string): string {
  return action.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

interface RepairActivityProps {
  steps: StepResponse[];
  failureCounts?: Record<string, number> | null;
}

export function RepairActivity({ steps, failureCounts }: RepairActivityProps) {
  const [expanded, setExpanded] = useState(false);

  // Steps where a repair was attempted
  const repairSteps = steps
    .filter((s) => s.patch_applied != null)
    .sort((a, b) => a.step_number - b.step_number);

  // Circuit breaker: any failure class that tripped >= 3 times
  const cbTrips = failureCounts
    ? Object.entries(failureCounts).filter(([, count]) => count >= 3)
    : [];

  if (repairSteps.length === 0 && cbTrips.length === 0) return null;

  const successCount = repairSteps.filter((s) => s.patch_applied?.success).length;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm">Repair Activity</CardTitle>
            {repairSteps.length > 0 && (
              <Badge variant="secondary" className="text-xs">
                {repairSteps.length} repair{repairSteps.length !== 1 ? "s" : ""} attempted
              </Badge>
            )}
            {successCount > 0 && (
              <Badge variant="outline" className="text-xs border-green-400 text-green-700 dark:text-green-400">
                {successCount} succeeded
              </Badge>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
          </Button>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="pt-0 space-y-3">
          {/* Circuit breaker warnings */}
          {cbTrips.length > 0 && (
            <div className="space-y-1">
              {cbTrips.map(([cls, count]) => (
                <div
                  key={cls}
                  className="flex items-center gap-2 rounded-md border border-amber-400/50 bg-amber-50 dark:bg-amber-900/20 px-3 py-2"
                >
                  <AlertTriangle className="size-4 text-amber-600 dark:text-amber-400 shrink-0" />
                  <p className="text-xs text-amber-700 dark:text-amber-300">
                    Circuit breaker tripped: <span className="font-medium">{getFailureLabel(cls)}</span> repeated{" "}
                    {count}× times
                  </p>
                </div>
              ))}
            </div>
          )}

          {/* Repair timeline */}
          {repairSteps.length > 0 && (
            <div className="space-y-1">
              {repairSteps.map((s) => {
                const patch = s.patch_applied!;
                const succeeded = patch.success;
                return (
                  <div
                    key={s.step_number}
                    className={cn(
                      "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm",
                      succeeded
                        ? "bg-green-50 dark:bg-green-900/20"
                        : "bg-red-50 dark:bg-red-900/20",
                    )}
                  >
                    {succeeded ? (
                      <CheckCircle2 className="size-3.5 text-green-600 dark:text-green-400 shrink-0" />
                    ) : (
                      <XCircle className="size-3.5 text-red-600 dark:text-red-400 shrink-0" />
                    )}
                    <span className="text-xs text-muted-foreground shrink-0">Step {s.step_number}:</span>
                    {s.failure_class && (
                      <span className="text-xs font-medium">{getFailureLabel(s.failure_class)}</span>
                    )}
                    <span className="text-xs text-muted-foreground">→</span>
                    <span className="text-xs">
                      <Wrench className="inline size-3 mr-0.5" />
                      {getActionLabel(patch.action)}
                    </span>
                    <span className="text-xs ml-auto shrink-0">
                      {succeeded ? (
                        <span className="text-green-600 dark:text-green-400">✓ Success</span>
                      ) : (
                        <span className="text-red-600 dark:text-red-400">✗ Failed</span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
