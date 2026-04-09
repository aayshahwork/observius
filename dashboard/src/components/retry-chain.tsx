"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  RotateCcw,
  ArrowRight,
  Zap,
  Clock,
  ShieldAlert,
  RefreshCw,
  Shield,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import {
  formatCost,
  formatDuration,
  getErrorCategoryLabel,
  getErrorCategoryColor,
  isRetryable,
  cn,
} from "@/lib/utils";
import type { ApiClient } from "@/lib/api-client";
import type {
  TaskResponse,
  ErrorCategory,
  RetryAttempt,
} from "@/lib/types";

interface RetryChainProps {
  task: TaskResponse;
  client: ApiClient;
}

// ---------- Inline attempt cards (from wrap() adaptive retry) ----------

function AttemptCard({
  attempt,
  isLast,
}: {
  attempt: RetryAttempt;
  isLast: boolean;
}) {
  const d = attempt.diagnosis;
  const p = attempt.recovery_plan;
  const ok = attempt.status === "completed";
  const failed = attempt.status === "failed";

  return (
    <div className="flex items-stretch">
      <div
        className={cn(
          "flex w-56 shrink-0 flex-col gap-1.5 rounded-lg border p-3 text-left text-xs",
          ok &&
            "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30",
          failed &&
            "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20",
          !ok && !failed && "border-border bg-muted/30",
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <span className="font-medium">Attempt {attempt.attempt}</span>
          <Badge
            variant="secondary"
            className={cn(
              ok &&
                "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
              failed &&
                "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
            )}
          >
            {ok ? "Success" : "Failed"}
          </Badge>
        </div>

        {/* Diagnosis */}
        {d && (
          <>
            <Badge variant="outline" className="w-fit text-[10px]">
              {d.category}
              {d.subcategory ? ` / ${d.subcategory}` : ""}
            </Badge>
            <p className="line-clamp-2 text-muted-foreground">
              {d.root_cause}
            </p>
            <div className="text-[10px] text-muted-foreground">
              {d.analysis_method === "llm_haiku"
                ? "AI diagnosed"
                : "Rule matched"}
              {d.analysis_cost_cents > 0 &&
                ` | ${formatCost(d.analysis_cost_cents)}`}
            </div>
          </>
        )}

        {/* Recovery strategy */}
        {d && d.retry_hint && (
          <div className="flex items-start gap-1 rounded bg-blue-100/50 px-1.5 py-1 dark:bg-blue-900/20">
            <Zap className="mt-0.5 size-3 shrink-0 text-blue-600 dark:text-blue-400" />
            <span className="line-clamp-2 text-blue-800 dark:text-blue-300">
              {d.retry_hint}
            </span>
          </div>
        )}

        {/* Environment changes */}
        {p &&
          (p.fresh_browser ||
            p.stealth_mode ||
            p.clear_cookies ||
            p.increase_timeout ||
            p.reduce_max_actions ||
            p.extend_system_message) && (
            <div className="flex flex-wrap gap-1">
              {p.fresh_browser && (
                <Badge variant="outline" className="text-[9px]">
                  <RefreshCw className="mr-0.5 size-2.5" />
                  fresh browser
                </Badge>
              )}
              {p.stealth_mode && (
                <Badge variant="outline" className="text-[9px]">
                  <Shield className="mr-0.5 size-2.5" />
                  stealth
                </Badge>
              )}
              {p.clear_cookies && (
                <Badge variant="outline" className="text-[9px]">
                  clear cookies
                </Badge>
              )}
              {p.increase_timeout && (
                <Badge variant="outline" className="text-[9px]">
                  <Clock className="mr-0.5 size-2.5" />
                  longer timeout
                </Badge>
              )}
              {p.reduce_max_actions && (
                <Badge variant="outline" className="text-[9px]">
                  deliberate mode
                </Badge>
              )}
              {p.extend_system_message && (
                <Badge variant="outline" className="text-[9px] bg-blue-500/10 text-blue-400 border-blue-500/20">
                  system prompt modified
                </Badge>
              )}
            </div>
          )}

        {/* Success */}
        {ok && !d && (
          <p className="text-green-700 dark:text-green-400">
            Completed successfully
          </p>
        )}
      </div>
      {!isLast && (
        <div className="flex items-center px-1">
          <ArrowRight className="size-3.5 text-muted-foreground/50" />
        </div>
      )}
    </div>
  );
}

// ---------- Linked-task card (from cloud retry via retry_of_task_id) ----------

function TaskAttemptCard({
  node,
  index,
  isCurrent,
  chainLength,
  onClick,
}: {
  node: TaskResponse;
  index: number;
  isCurrent: boolean;
  chainLength: number;
  onClick: () => void;
}) {
  const nodeFailed = node.status === "failed" || node.status === "timeout";
  const isSuccess = node.status === "completed";

  return (
    <div className="flex items-stretch">
      {index > 0 && (
        <div className="flex items-center px-1">
          <ArrowRight className="size-3.5 text-muted-foreground/50" />
        </div>
      )}
      <button
        onClick={onClick}
        disabled={isCurrent}
        className={cn(
          "flex w-56 shrink-0 flex-col gap-1.5 rounded-lg border p-3 text-left text-xs transition-colors",
          isCurrent && "ring-2 ring-primary/50",
          isSuccess &&
            "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30",
          nodeFailed &&
            !isCurrent &&
            "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20",
          !isSuccess && !nodeFailed && "border-border bg-muted/30",
          !isCurrent && "cursor-pointer hover:bg-muted/60",
        )}
      >
        <div className="flex items-center justify-between">
          <span className="font-medium">
            {chainLength > 1 ? `Attempt ${index + 1}` : "This Run"}
          </span>
          <StatusBadge status={node.status} />
        </div>

        {nodeFailed && node.error_category && (
          <Badge
            variant="secondary"
            className={cn(
              "w-fit text-[10px]",
              getErrorCategoryColor(node.error_category as ErrorCategory),
            )}
          >
            {getErrorCategoryLabel(node.error_category as ErrorCategory)}
          </Badge>
        )}

        {nodeFailed && node.error && (
          <p className="line-clamp-2 text-muted-foreground">{node.error}</p>
        )}

        {node.analysis?.summary && (
          <div className="flex items-start gap-1 rounded bg-amber-100/50 px-1.5 py-1 dark:bg-amber-900/20">
            <ShieldAlert className="mt-0.5 size-3 shrink-0 text-amber-600 dark:text-amber-400" />
            <span className="line-clamp-2 text-amber-800 dark:text-amber-300">
              {node.analysis.summary}
            </span>
          </div>
        )}

        {node.analysis?.primary_suggestion && (
          <div className="flex items-start gap-1 rounded bg-blue-100/50 px-1.5 py-1 dark:bg-blue-900/20">
            <Zap className="mt-0.5 size-3 shrink-0 text-blue-600 dark:text-blue-400" />
            <span className="line-clamp-2 text-blue-800 dark:text-blue-300">
              {node.analysis.primary_suggestion}
            </span>
          </div>
        )}

        {isSuccess && !node.analysis && (
          <p className="text-green-700 dark:text-green-400">
            Completed successfully
          </p>
        )}

        <div className="mt-auto flex items-center gap-2 border-t border-border/50 pt-1.5 text-muted-foreground">
          {node.duration_ms > 0 && (
            <span className="flex items-center gap-0.5 tabular-nums">
              <Clock className="size-3" />
              {formatDuration(node.duration_ms)}
            </span>
          )}
          <span className="tabular-nums">{formatCost(node.cost_cents)}</span>
          {node.steps > 0 && (
            <span className="tabular-nums">{node.steps} steps</span>
          )}
        </div>
      </button>
    </div>
  );
}

// ---------- Main component ----------

export function RetryChain({ task, client }: RetryChainProps) {
  const router = useRouter();
  const [chain, setChain] = useState<TaskResponse[]>([]);
  const [loading, setLoading] = useState(true);

  // Check for inline adaptive retry attempts (from wrap())
  const inlineAttempts = task.analysis?.attempts;
  const hasInlineAttempts =
    inlineAttempts && inlineAttempts.length > 0;

  useEffect(() => {
    // If we have inline attempts, no need to fetch the chain
    if (hasInlineAttempts) {
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function buildChain() {
      try {
        const isRetry = !!task.retry_of_task_id;
        const rootId = task.retry_of_task_id ?? task.task_id;

        const retriesRes = await client.listTasks({
          retry_of_task_id: rootId,
          limit: 10,
        });

        if (cancelled) return;

        if (retriesRes.tasks.length === 0) {
          setChain([task]);
          setLoading(false);
          return;
        }

        const rootTask = isRetry ? await client.getTask(rootId) : task;
        if (cancelled) return;

        const retries = retriesRes.tasks.sort(
          (a, b) => a.retry_count - b.retry_count,
        );
        setChain([rootTask, ...retries]);
      } catch {
        setChain([task]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    buildChain();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.task_id, task.retry_of_task_id, client, hasInlineAttempts]);

  if (loading) return null;

  // --- Inline attempts from wrap() adaptive retry ---
  if (hasInlineAttempts) {
    const totalAttempts = task.analysis?.total_attempts ?? inlineAttempts.length;
    const recovered = inlineAttempts.some((a) => a.status === "completed");
    const diagCost = inlineAttempts.reduce(
      (s, a) => s + (a.diagnosis?.analysis_cost_cents ?? 0),
      0,
    );

    return (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <RotateCcw className="size-4 text-muted-foreground" />
              <CardTitle className="text-sm">
                Retry Intelligence ({totalAttempts} attempt
                {totalAttempts !== 1 ? "s" : ""})
              </CardTitle>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              {diagCost > 0 && (
                <span>Diagnosis: {formatCost(diagCost)}</span>
              )}
              {(() => {
                const savedRetries = Math.max(0, inlineAttempts.filter(a => a.status === "failed").length - 1);
                return savedRetries > 0 ? (
                  <span>
                    Est. savings: ~${(savedRetries * 0.19).toFixed(2)} ({savedRetries} prevented blind {savedRetries === 1 ? "retry" : "retries"})
                  </span>
                ) : null;
              })()}
              {recovered && (
                <Badge
                  variant="secondary"
                  className="bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                >
                  Recovered
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="flex items-stretch gap-0 overflow-x-auto pb-1">
            {inlineAttempts.map((a, i) => (
              <AttemptCard
                key={a.attempt}
                attempt={a}
                isLast={i === inlineAttempts.length - 1}
              />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  // --- Linked-task chain (cloud retries via retry_of_task_id) ---
  const isSingleRun = chain.length <= 1;
  const hasAnalysis = task.analysis && task.analysis.findings.length > 0;
  const isFailed = task.status === "failed" || task.status === "timeout";

  if (isSingleRun && !isFailed && !hasAnalysis) return null;
  if (isSingleRun && (task.status === "running" || task.status === "queued"))
    return null;

  const totalCost = chain.reduce((s, t) => s + (t.cost_cents ?? 0), 0);
  const recovered =
    chain.length > 1 && chain.some((t) => t.status === "completed");

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <RotateCcw className="size-4 text-muted-foreground" />
            <CardTitle className="text-sm">
              {chain.length > 1
                ? `Retry Intelligence (${chain.length} attempts)`
                : "Retry Intelligence"}
            </CardTitle>
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {chain.length > 1 && <span>Total: {formatCost(totalCost)}</span>}
            {recovered && (
              <Badge
                variant="secondary"
                className="bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
              >
                Recovered
              </Badge>
            )}
            {isSingleRun &&
              isFailed &&
              task.error_category &&
              isRetryable(task.error_category as ErrorCategory) && (
                <Badge
                  variant="secondary"
                  className="bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                >
                  Auto-retryable
                </Badge>
              )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-stretch gap-0 overflow-x-auto pb-1">
          {chain.map((node, i) => (
            <TaskAttemptCard
              key={node.task_id}
              node={node}
              index={i}
              isCurrent={node.task_id === task.task_id}
              chainLength={chain.length}
              onClick={() => router.push(`/tasks/${node.task_id}`)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
