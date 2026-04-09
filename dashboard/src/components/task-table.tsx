"use client";

import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { ArrowUp, ArrowDown, ArrowUpDown, Search } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { formatCost, formatDuration } from "@/lib/utils";
import type { TaskResponse } from "@/lib/types";

export type SortField = "created_at" | "duration_ms" | "steps" | "cost_cents";
export type SortOrder = "asc" | "desc";

const ENGINE_BADGE_CONFIG: Record<string, { label: string; className: string }> = {
  browser_use: { label: "BU", className: "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800" },
  native: { label: "CUA", className: "bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800" },
  skyvern: { label: "SKY", className: "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800" },
  sdk: { label: "SDK", className: "bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700" },
};

function EngineBadge({ mode }: { mode: string }) {
  const config = ENGINE_BADGE_CONFIG[mode] ?? ENGINE_BADGE_CONFIG.browser_use;
  return (
    <Badge
      variant="outline"
      className={`px-1.5 py-0 text-[10px] leading-4 font-medium border ${config.className}`}
    >
      {config.label}
    </Badge>
  );
}

function truncateUrl(url: string, maxLen = 40): string {
  try {
    const u = new URL(url);
    const display = u.hostname + u.pathname;
    return display.length > maxLen ? display.slice(0, maxLen) + "..." : display;
  } catch {
    return url.length > maxLen ? url.slice(0, maxLen) + "..." : url;
  }
}

interface TaskTableProps {
  tasks: TaskResponse[];
  sortField?: SortField;
  sortOrder?: SortOrder;
  onSort?: (field: SortField) => void;
}

function SortIndicator({
  field,
  activeField,
  order,
}: {
  field: SortField;
  activeField?: SortField;
  order?: SortOrder;
}) {
  if (field !== activeField) {
    return (
      <ArrowUpDown className="ml-1 inline size-3 text-muted-foreground/50" />
    );
  }
  return order === "asc" ? (
    <ArrowUp className="ml-1 inline size-3" />
  ) : (
    <ArrowDown className="ml-1 inline size-3" />
  );
}

function SortableHead({
  field,
  activeField,
  order,
  onSort,
  className,
  children,
}: {
  field: SortField;
  activeField?: SortField;
  order?: SortOrder;
  onSort?: (field: SortField) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <TableHead
      className={`${className || ""} ${onSort ? "cursor-pointer select-none hover:text-foreground" : ""}`}
      onClick={() => onSort?.(field)}
    >
      {children}
      {onSort && (
        <SortIndicator field={field} activeField={activeField} order={order} />
      )}
    </TableHead>
  );
}

export function TaskTable({
  tasks,
  sortField,
  sortOrder,
  onSort,
}: TaskTableProps) {
  const router = useRouter();

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Description</TableHead>
          <TableHead className="hidden md:table-cell">URL</TableHead>
          <TableHead className="hidden sm:table-cell">Engine</TableHead>
          <SortableHead
            field="steps"
            activeField={sortField}
            order={sortOrder}
            onSort={onSort}
            className="text-right"
          >
            Steps
          </SortableHead>
          <SortableHead
            field="duration_ms"
            activeField={sortField}
            order={sortOrder}
            onSort={onSort}
            className="text-right hidden sm:table-cell"
          >
            Duration
          </SortableHead>
          <SortableHead
            field="cost_cents"
            activeField={sortField}
            order={sortOrder}
            onSort={onSort}
            className="text-right hidden sm:table-cell"
          >
            Cost
          </SortableHead>
          <SortableHead
            field="created_at"
            activeField={sortField}
            order={sortOrder}
            onSort={onSort}
            className="text-right hidden lg:table-cell"
          >
            Created
          </SortableHead>
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
              <div className="flex items-center gap-1.5">
                <StatusBadge status={task.status} />
                {task.status === "failed" && task.analysis && task.analysis.findings.length > 0 && (
                  <Badge
                    variant="outline"
                    className="px-1 py-0 text-[10px] leading-4 font-normal text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-700"
                    title={task.analysis.primary_suggestion}
                  >
                    <Search className="size-2.5" />
                  </Badge>
                )}
              </div>
            </TableCell>
            <TableCell className="max-w-[200px] truncate font-medium">
              {task.task_description || task.task_id.slice(0, 8)}
            </TableCell>
            <TableCell className="hidden md:table-cell text-muted-foreground">
              {truncateUrl(task.url ?? "")}
            </TableCell>
            <TableCell className="hidden sm:table-cell">
              <EngineBadge mode={task.executor_mode} />
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {task.steps}
            </TableCell>
            <TableCell className="text-right hidden sm:table-cell tabular-nums text-muted-foreground">
              {task.duration_ms ? formatDuration(task.duration_ms) : "\u2014"}
            </TableCell>
            <TableCell
              className={`text-right hidden sm:table-cell tabular-nums text-muted-foreground${
                task.cost_cents > 50
                  ? " bg-amber-50 dark:bg-amber-900/20"
                  : ""
              }`}
            >
              {formatCost(task.cost_cents)}
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
