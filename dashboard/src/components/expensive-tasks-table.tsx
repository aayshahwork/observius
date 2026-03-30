"use client";

import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { TaskResponse } from "@/lib/types";
import { formatCost, formatDuration } from "@/lib/utils";

interface ExpensiveTasksTableProps {
  tasks: TaskResponse[];
}

export function ExpensiveTasksTable({ tasks }: ExpensiveTasksTableProps) {
  const router = useRouter();

  const top10 = [...tasks]
    .sort((a, b) => (b.cost_cents || 0) - (a.cost_cents || 0))
    .slice(0, 10);

  if (top10.length === 0 || top10[0].cost_cents === 0) return null;

  const statusVariant = (s: string) => {
    switch (s) {
      case "completed":
        return "secondary" as const;
      case "failed":
        return "destructive" as const;
      default:
        return "outline" as const;
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Most Expensive Tasks</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">URL</th>
                <th className="pb-2 pr-4 font-medium">Cost</th>
                <th className="pb-2 pr-4 font-medium">Steps</th>
                <th className="pb-2 pr-4 font-medium">Duration</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 font-medium">Executor</th>
              </tr>
            </thead>
            <tbody>
              {top10.map((task) => (
                <tr
                  key={task.task_id}
                  onClick={() => router.push(`/tasks/${task.task_id}`)}
                  className="cursor-pointer border-b transition-colors hover:bg-muted/50"
                >
                  <td className="max-w-[200px] truncate py-2 pr-4">
                    {task.url ?? "\u2014"}
                  </td>
                  <td className="py-2 pr-4 font-mono">
                    {formatCost(task.cost_cents)}
                  </td>
                  <td className="py-2 pr-4">{task.steps}</td>
                  <td className="py-2 pr-4">
                    {formatDuration(task.duration_ms)}
                  </td>
                  <td className="py-2 pr-4">
                    <Badge variant={statusVariant(task.status)}>
                      {task.status}
                    </Badge>
                  </td>
                  <td className="py-2">{task.executor_mode}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
