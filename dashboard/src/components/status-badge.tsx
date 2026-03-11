import { Badge } from "@/components/ui/badge";
import type { TaskStatus } from "@/lib/types";

const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; className: string }
> = {
  queued: {
    label: "Queued",
    className: "bg-muted text-muted-foreground",
  },
  running: {
    label: "Running",
    className:
      "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  },
  completed: {
    label: "Completed",
    className:
      "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  },
  failed: {
    label: "Failed",
    className:
      "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  },
  timeout: {
    label: "Timeout",
    className:
      "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-muted text-muted-foreground",
  },
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  const config = STATUS_CONFIG[status];
  return (
    <Badge variant="secondary" className={config.className}>
      {config.label}
    </Badge>
  );
}
