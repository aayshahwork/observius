"use client";

import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/status-badge";
import type { TaskResponse } from "@/lib/types";

function truncateUrl(url: string, maxLen = 40): string {
  try {
    const u = new URL(url);
    const display = u.hostname + u.pathname;
    return display.length > maxLen ? display.slice(0, maxLen) + "..." : display;
  } catch {
    return url.length > maxLen ? url.slice(0, maxLen) + "..." : url;
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

interface TaskTableProps {
  tasks: TaskResponse[];
}

export function TaskTable({ tasks }: TaskTableProps) {
  const router = useRouter();

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Description</TableHead>
          <TableHead className="hidden md:table-cell">URL</TableHead>
          <TableHead className="text-right">Steps</TableHead>
          <TableHead className="text-right hidden sm:table-cell">Duration</TableHead>
          <TableHead className="text-right hidden lg:table-cell">Created</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tasks.map((task) => (
          <TableRow
            key={task.task_id}
            className="cursor-pointer"
            onClick={() => router.push(`/tasks/${task.task_id}`)}
          >
            <TableCell>
              <StatusBadge status={task.status} />
            </TableCell>
            <TableCell className="max-w-[200px] truncate font-medium">
              {task.result?.task_description as string ?? task.task_id.slice(0, 8)}
            </TableCell>
            <TableCell className="hidden md:table-cell text-muted-foreground">
              {truncateUrl(task.replay_url ?? "")}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {task.steps}
            </TableCell>
            <TableCell className="text-right hidden sm:table-cell tabular-nums text-muted-foreground">
              {task.duration_ms ? formatDuration(task.duration_ms) : "—"}
            </TableCell>
            <TableCell className="text-right hidden lg:table-cell text-muted-foreground">
              {formatDistanceToNow(new Date(task.created_at), {
                addSuffix: true,
              })}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
