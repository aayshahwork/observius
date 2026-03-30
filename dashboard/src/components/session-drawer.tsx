"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { format, formatDistanceToNow } from "date-fns";
import { AlertTriangle, Trash2 } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/status-badge";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useApiClient } from "@/hooks/use-api-client";
import type { SessionResponse, TaskResponse } from "@/lib/types";

interface SessionDrawerProps {
  session: SessionResponse | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}

const AUTH_STATE_CONFIG: Record<string, { dot: string; badge: string; label: string }> = {
  authenticated: {
    dot: "bg-green-500",
    badge: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
    label: "Authenticated",
  },
  active: {
    dot: "bg-green-500",
    badge: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
    label: "Active",
  },
  stale: {
    dot: "bg-amber-500",
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    label: "Stale",
  },
  expired: {
    dot: "bg-red-500",
    badge: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
    label: "Expired",
  },
};

export { AUTH_STATE_CONFIG };

export function SessionDrawer({ session, open, onOpenChange, onDeleted }: SessionDrawerProps) {
  const client = useApiClient();
  const router = useRouter();
  const [recentTasks, setRecentTasks] = useState<TaskResponse[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const fetchRecentTasks = useCallback(async () => {
    if (!client || !session) return;
    setLoadingTasks(true);
    try {
      const res = await client.listTasks({
        session_id: session.session_id,
        limit: 5,
      });
      setRecentTasks(res.tasks);
      setTaskTotal(res.total);
    } catch {
      setRecentTasks([]);
      setTaskTotal(0);
    } finally {
      setLoadingTasks(false);
    }
  }, [client, session]);

  useEffect(() => {
    if (open && session) {
      fetchRecentTasks();
    } else {
      setRecentTasks([]);
      setTaskTotal(0);
    }
  }, [open, session, fetchRecentTasks]);

  const handleDelete = async () => {
    if (!client || !session) return;
    setDeleting(true);
    try {
      await client.deleteSession(session.session_id);
      setConfirmDelete(false);
      onOpenChange(false);
      onDeleted();
    } catch {
      // Error is non-critical here — user can retry
    } finally {
      setDeleting(false);
    }
  };

  if (!session) return null;

  const stateConfig = AUTH_STATE_CONFIG[session.auth_state ?? ""] ?? {
    dot: "bg-muted-foreground",
    badge: "",
    label: session.auth_state ?? "Unknown",
  };

  const isStale = session.auth_state === "stale";

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="flex flex-col overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{session.origin_domain}</SheetTitle>
            <SheetDescription>Session details and linked tasks</SheetDescription>
          </SheetHeader>

          <div className="flex-1 space-y-6 px-4">
            {/* Auth State */}
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Auth State</p>
              <Badge variant="secondary" className={stateConfig.badge}>
                <span className={`mr-1.5 inline-block size-2 rounded-full ${stateConfig.dot}`} />
                {stateConfig.label}
              </Badge>
            </div>

            {/* Dates */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Created</p>
                <p className="text-sm">
                  {session.created_at
                    ? format(new Date(session.created_at), "MMM d, yyyy")
                    : "—"}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">Last Used</p>
                <p className="text-sm">
                  {session.last_used_at
                    ? format(new Date(session.last_used_at), "MMM d, yyyy")
                    : "Never"}
                </p>
                {session.last_used_at && (
                  <p className="text-xs text-muted-foreground">
                    {formatDistanceToNow(new Date(session.last_used_at), { addSuffix: true })}
                  </p>
                )}
              </div>
            </div>

            {/* Staleness Warning */}
            {isStale && (
              <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/50 dark:bg-amber-950/30">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
                <p className="text-xs text-amber-800 dark:text-amber-300">
                  This session&apos;s credentials may have expired. The next task using this session
                  will attempt to re-authenticate.
                </p>
              </div>
            )}

            {/* Recent Tasks */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-muted-foreground">
                  Recent Tasks {taskTotal > 0 && `(${taskTotal} total)`}
                </p>
              </div>

              {loadingTasks ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              ) : recentTasks.length === 0 ? (
                <p className="text-xs text-muted-foreground">No tasks have used this session yet.</p>
              ) : (
                <div className="space-y-1">
                  {recentTasks.map((task) => (
                    <button
                      key={task.task_id}
                      type="button"
                      className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/50"
                      onClick={() => {
                        onOpenChange(false);
                        router.push(`/tasks/${task.task_id}`);
                      }}
                    >
                      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                        {task.created_at
                          ? formatDistanceToNow(new Date(task.created_at), { addSuffix: true })
                          : ""}
                      </span>
                      <StatusBadge status={task.status} />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <SheetFooter>
            <Button
              variant="destructive"
              className="w-full"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 className="mr-2 size-4" />
              Delete Session
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete Session"
        description="This will delete the session and its stored cookies. Any tasks using this session will need to re-authenticate."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleting}
        onConfirm={handleDelete}
      />
    </>
  );
}
