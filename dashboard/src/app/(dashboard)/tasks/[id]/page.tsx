"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { format } from "date-fns";
import { toast } from "sonner";
import { ArrowLeft, RotateCw, XCircle, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/status-badge";
import { JsonViewer } from "@/components/json-viewer";
import { ReplayViewer } from "@/components/replay-viewer";
import { AnalysisPanel } from "@/components/analysis-panel";
import { StepTimeline } from "@/components/step-timeline";
import { WorkflowPanel } from "@/components/workflow-panel";
import { RetryChain } from "@/components/retry-chain";
import { RepairActivity } from "@/components/repair-activity";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import {
  formatCost,
  formatTokens,
  formatDuration,
  getErrorCategoryLabel,
  getErrorCategoryColor,
  isRetryable,
} from "@/lib/utils";
import type { TaskResponse, StepResponse, ErrorCategory } from "@/lib/types";

function isTerminal(status: string): boolean {
  return ["completed", "failed", "timeout", "cancelled"].includes(status);
}

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const client = useApiClient();

  const [task, setTask] = useState<TaskResponse | null>(null);
  const [replayUrl, setReplayUrl] = useState<string | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [steps, setSteps] = useState<StepResponse[]>([]);
  const [stepsLoading, setStepsLoading] = useState(false);

  const [cancelDialog, setCancelDialog] = useState(false);
  const [retryDialog, setRetryDialog] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const fetchTask = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.getTask(id);
      setTask(res);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to fetch task");
    } finally {
      setLoading(false);
    }
  }, [client, id, router]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  // Poll every 3s while active. Clear on unmount or terminal state.
  useEffect(() => {
    if (!task || isTerminal(task.status)) return;

    const interval = setInterval(fetchTask, 3000);
    return () => clearInterval(interval);
  }, [task, fetchTask]);

  // Fetch replay URL when task reaches terminal state
  useEffect(() => {
    if (!client || !task || !isTerminal(task.status) || replayUrl) return;

    setReplayLoading(true);
    client
      .getReplay(id)
      .then((res) => setReplayUrl(res.replay_url))
      .catch(() => setReplayUrl(null))
      .finally(() => setReplayLoading(false));
  }, [client, task, id, replayUrl]);

  // Fetch step data when task has steps
  useEffect(() => {
    if (!client || !task || task.steps === 0) return;
    // For running tasks, re-fetch steps on each task poll update
    if (steps.length > 0 && isTerminal(task.status)) return;

    setStepsLoading(true);
    client
      .getTaskSteps(id)
      .then((res) => setSteps(res))
      .catch(() => setSteps([]))
      .finally(() => setStepsLoading(false));
  }, [client, task, id, steps.length]);

  const handleCancel = async () => {
    if (!client) return;
    setActionLoading(true);
    try {
      await client.cancelTask(id);
      setCancelDialog(false);
      toast.success("Task cancelled");
      fetchTask();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to cancel task");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRetry = async () => {
    if (!client) return;
    setActionLoading(true);
    try {
      const newTask = await client.retryTask(id);
      setRetryDialog(false);
      toast.success("Retry task created");
      router.push(`/tasks/${newTask.task_id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to retry task");
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error && !task) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" onClick={() => router.push("/tasks")}>
          <ArrowLeft className="mr-2 size-4" />
          Back to Tasks
        </Button>
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  if (!task) return null;

  const showCancel = task.status === "queued" || task.status === "running";
  const showRetry = task.status === "failed";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={() => router.push("/tasks")}>
          <ArrowLeft className="mr-2 size-4" />
          Back to Tasks
        </Button>
        <div className="flex gap-2">
          {showCancel && (
            <Button
              variant="outline"
              onClick={() => setCancelDialog(true)}
              disabled={actionLoading}
            >
              <XCircle className="mr-2 size-4" />
              Cancel
            </Button>
          )}
          {showRetry && (
            <Button
              variant="outline"
              onClick={() => setRetryDialog(true)}
              disabled={actionLoading}
            >
              <RotateCw className="mr-2 size-4" />
              Retry
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Retry Chain */}
      {client && <RetryChain task={task} client={client} />}

      {/* Metadata Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <CardTitle className="text-base">Task Details</CardTitle>
            <StatusBadge status={task.status} />
            {task.status === "running" && (
              <span className="relative flex size-2.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-brand opacity-75" />
                <span className="relative inline-flex size-2.5 rounded-full bg-brand" />
              </span>
            )}
            <Badge
              variant="outline"
              className={`text-xs font-medium border ${
                task.executor_mode === "native"
                  ? "bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800"
                  : task.executor_mode === "skyvern"
                  ? "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800"
                  : task.executor_mode === "sdk"
                  ? "bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700"
                  : "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800"
              }`}
            >
              {task.executor_mode === "native" ? "Anthropic CUA" : task.executor_mode === "skyvern" ? "Skyvern" : task.executor_mode === "sdk" ? "SDK" : "Browser Use"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <dt className="text-xs text-muted-foreground">Task ID</dt>
              <dd className="mt-1 text-sm font-mono">{task.task_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Steps</dt>
              <dd className="mt-1 text-sm tabular-nums">{task.steps}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Duration</dt>
              <dd className="mt-1 text-sm tabular-nums">
                {task.duration_ms ? formatDuration(task.duration_ms) : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Cost</dt>
              <dd className="mt-1 text-sm tabular-nums">
                {formatCost(task.cost_cents)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Tokens</dt>
              <dd className="mt-1 text-sm tabular-nums">
                {task.total_tokens_in || task.total_tokens_out
                  ? `↑${formatTokens(task.total_tokens_in)} ↓${formatTokens(task.total_tokens_out)}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Created</dt>
              <dd className="mt-1 text-sm">
                {format(new Date(task.created_at), "PPp")}
              </dd>
            </div>
            {task.completed_at && (
              <div>
                <dt className="text-xs text-muted-foreground">Completed</dt>
                <dd className="mt-1 text-sm">
                  {format(new Date(task.completed_at), "PPp")}
                </dd>
              </div>
            )}
            {task.replay_url && (
              <div>
                <dt className="text-xs text-muted-foreground">URL</dt>
                <dd className="mt-1">
                  <a
                    href={task.replay_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                  >
                    Open <ExternalLink className="size-3" />
                  </a>
                </dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Repair Activity */}
      {steps.length > 0 && (
        <RepairActivity steps={steps} failureCounts={task.failure_counts} />
      )}

      {/* Error Display */}
      {task.error && (
        <Card className="border-destructive/50">
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-sm text-destructive">Error</CardTitle>
              {task.error_category && (
                <>
                  <Badge
                    variant="secondary"
                    className={getErrorCategoryColor(task.error_category as ErrorCategory)}
                  >
                    {getErrorCategoryLabel(task.error_category as ErrorCategory)}
                  </Badge>
                  {isRetryable(task.error_category as ErrorCategory) && (
                    <span className="text-xs text-muted-foreground">(auto-retryable)</span>
                  )}
                </>
              )}
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <p className="text-sm">{task.error}</p>
          </CardContent>
        </Card>
      )}

      {/* Analysis */}
      {task.analysis && task.analysis.findings.length > 0 && (
        <AnalysisPanel analysis={task.analysis} status={task.status} />
      )}

      {/* Result */}
      {task.result && <JsonViewer data={task.result} title="Result" />}

      {/* Steps + Replay + Workflow tabs */}
      {(task.steps > 0 || isTerminal(task.status)) && (
        <Tabs defaultValue={task.steps > 0 ? "steps" : "replay"}>
          <TabsList>
            {task.steps > 0 && (
              <TabsTrigger value="steps">
                Steps ({task.steps})
              </TabsTrigger>
            )}
            <TabsTrigger value="replay">Replay</TabsTrigger>
            {task.compiled_workflow && (
              <TabsTrigger value="workflow">Workflow</TabsTrigger>
            )}
          </TabsList>
          {task.steps > 0 && (
            <TabsContent value="steps">
              {stepsLoading && steps.length === 0 ? (
                <div className="space-y-2">
                  <div className="h-64 animate-pulse rounded-md bg-muted" />
                </div>
              ) : (
                <StepTimeline steps={steps} executorMode={task.executor_mode} />
              )}
            </TabsContent>
          )}
          <TabsContent value="replay">
            <ReplayViewer replayUrl={replayUrl} loading={replayLoading} />
          </TabsContent>
          {task.compiled_workflow && (
            <TabsContent value="workflow">
              <WorkflowPanel workflow={task.compiled_workflow} taskId={task.task_id} client={client} />
            </TabsContent>
          )}
        </Tabs>
      )}

      {/* Dialogs */}
      <ConfirmDialog
        open={cancelDialog}
        onOpenChange={setCancelDialog}
        title="Cancel Task"
        description="Are you sure you want to cancel this task? This action cannot be undone."
        confirmLabel="Cancel Task"
        variant="destructive"
        loading={actionLoading}
        onConfirm={handleCancel}
      />
      <ConfirmDialog
        open={retryDialog}
        onOpenChange={setRetryDialog}
        title="Retry Task"
        description="This will create a new task with the same parameters. Continue?"
        confirmLabel="Retry"
        loading={actionLoading}
        onConfirm={handleRetry}
      />
    </div>
  );
}
