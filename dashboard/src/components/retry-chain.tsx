"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { StatusBadge } from "@/components/status-badge";
import { formatCost } from "@/lib/utils";
import type { ApiClient } from "@/lib/api-client";
import type { TaskResponse } from "@/lib/types";

interface RetryChainProps {
  task: TaskResponse;
  client: ApiClient;
}

export function RetryChain({ task, client }: RetryChainProps) {
  const router = useRouter();
  const [chain, setChain] = useState<TaskResponse[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function buildChain() {
      try {
        const isRetry = !!task.retry_of_task_id;
        const rootId = task.retry_of_task_id ?? task.task_id;

        // Fetch all retries that point to the root
        const retriesRes = await client.listTasks({
          retry_of_task_id: rootId,
          limit: 10,
        });

        if (cancelled) return;

        // No retries exist and this is the original → no chain
        if (retriesRes.tasks.length === 0) {
          setChain([]);
          setLoading(false);
          return;
        }

        // Fetch root task if current task is a retry
        const rootTask = isRetry ? await client.getTask(rootId) : task;

        if (cancelled) return;

        const retries = retriesRes.tasks.sort(
          (a, b) => a.retry_count - b.retry_count
        );
        setChain([rootTask, ...retries]);
      } catch {
        setChain([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    buildChain();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.task_id, task.retry_of_task_id, client]);

  if (loading || chain.length <= 1) return null;

  return (
    <div className="flex items-center gap-1 rounded-md border bg-muted/50 px-4 py-2">
      <RotateCcw className="mr-2 size-4 text-muted-foreground shrink-0" />
      <span className="text-sm text-muted-foreground mr-3 shrink-0">
        Attempt {task.retry_count + 1} of {chain.length}
      </span>
      <div className="flex items-center overflow-x-auto">
        {chain.map((node, i) => {
          const isCurrent = node.task_id === task.task_id;
          return (
            <div key={node.task_id} className="flex items-center">
              {i > 0 && <div className="w-6 h-px bg-border shrink-0" />}
              <button
                onClick={() => {
                  if (!isCurrent) router.push(`/tasks/${node.task_id}`);
                }}
                className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-md transition-colors ${
                  isCurrent
                    ? "bg-primary/10"
                    : "hover:bg-muted cursor-pointer"
                }`}
                disabled={isCurrent}
              >
                <div
                  className={`flex items-center justify-center size-6 rounded-full text-xs font-medium border-2 ${
                    isCurrent
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background border-muted-foreground/30"
                  }`}
                >
                  {i}
                </div>
                <StatusBadge status={node.status} />
                <span className="text-[10px] text-muted-foreground tabular-nums">
                  {formatCost(node.cost_cents)}
                </span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
