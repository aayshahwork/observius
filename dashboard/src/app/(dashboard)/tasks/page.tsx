"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Plus, ListTodo, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { TaskTable } from "@/components/task-table";
import type { SortField, SortOrder } from "@/components/task-table";
import { EmptyState } from "@/components/empty-state";
import { useApiClient } from "@/hooks/use-api-client";

import type { TaskResponse } from "@/lib/types";

const PAGE_SIZE = 20;

type DateRange = "today" | "7d" | "30d" | "all";

function getDateRangeSince(range: DateRange): string | undefined {
  if (range === "all") return undefined;
  const now = new Date();
  if (range === "today") {
    now.setHours(0, 0, 0, 0);
  } else if (range === "7d") {
    now.setDate(now.getDate() - 7);
  } else if (range === "30d") {
    now.setDate(now.getDate() - 30);
  }
  return now.toISOString();
}

export default function TasksPage() {
  const client = useApiClient();
  const router = useRouter();

  // All filter/sort state is local React state (no URL params)
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [dateRange, setDateRange] = useState<DateRange>("all");
  const [errorCategoryFilter, setErrorCategoryFilter] = useState("all");
  const [dominantFailureFilter, setDominantFailureFilter] = useState("all");
  const [offset, setOffset] = useState(0);

  const [tasks, setTasks] = useState<TaskResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Debounce search input for client-side filtering
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Fetch tasks from API (depends on server-side filters only)
  const fetchTasks = useCallback(async () => {
    if (!client) return;
    try {
      const since = getDateRangeSince(dateRange);
      // "repaired" is a client-side computed filter — don't pass a status to API
      const apiStatus = statusFilter === "all" || statusFilter === "repaired" ? undefined : statusFilter;
      const res = await client.listTasks({
        limit: PAGE_SIZE,
        offset,
        status: apiStatus,
        since,
      });
      setTasks(res.tasks);
      setTotal(res.total);
      setHasMore(res.has_more);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch tasks");
    } finally {
      setLoading(false);
    }
  }, [client, offset, statusFilter, dateRange]);  // dominantFailureFilter is client-side only

  useEffect(() => {
    setLoading(true);
    fetchTasks();
  }, [fetchTasks]);

  // Auto-refresh while active tasks exist
  useEffect(() => {
    const hasActiveTasks = tasks.some(
      (t) => t.status === "queued" || t.status === "running"
    );
    if (!hasActiveTasks) return;
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, [tasks, fetchTasks]);

  // Client-side: search → error category → sort
  const filteredAndSorted = useMemo(() => {
    let result = [...tasks];

    // Text search
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase();
      result = result.filter((t) => {
        const desc = t.task_description || "";
        const url = t.url || "";
        return desc.toLowerCase().includes(q) || url.toLowerCase().includes(q);
      });
    }

    // Repaired tasks filter (client-side computed field)
    if (statusFilter === "repaired") {
      result = result.filter((t) => t.was_repaired === true);
    }

    // Error category (only when status=failed)
    if (statusFilter === "failed" && errorCategoryFilter !== "all") {
      result = result.filter((t) => {
        if (errorCategoryFilter === "transient")
          return t.error_category?.startsWith("transient");
        if (errorCategoryFilter === "permanent")
          return t.error_category?.startsWith("permanent");
        if (errorCategoryFilter === "rate_limited")
          return t.error_category === "rate_limited";
        return true;
      });
    }

    // Dominant failure class filter (only when status=failed)
    if (statusFilter === "failed" && dominantFailureFilter !== "all") {
      const UI_FAILURES = ["element_not_found", "element_obscured", "captcha_challenge"];
      const NETWORK_FAILURES = ["network_timeout", "auth_required"];
      const GOAL_FAILURES = ["goal_not_met", "stuck_state", "navigation_loop"];
      const POLICY_FAILURES = ["policy_violation", "page_crash"];
      result = result.filter((t) => {
        const df = t.dominant_failure ?? "";
        if (dominantFailureFilter === "ui") return UI_FAILURES.includes(df);
        if (dominantFailureFilter === "network") return NETWORK_FAILURES.includes(df);
        if (dominantFailureFilter === "goal") return GOAL_FAILURES.includes(df);
        if (dominantFailureFilter === "policy") return POLICY_FAILURES.includes(df);
        return true;
      });
    }

    // Sort
    result.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "created_at":
          cmp =
            new Date(a.created_at).getTime() -
            new Date(b.created_at).getTime();
          break;
        case "duration_ms":
          cmp = (a.duration_ms || 0) - (b.duration_ms || 0);
          break;
        case "steps":
          cmp = a.steps - b.steps;
          break;
        case "cost_cents":
          cmp = a.cost_cents - b.cost_cents;
          break;
      }
      return sortOrder === "asc" ? cmp : -cmp;
    });

    return result;
  }, [tasks, debouncedSearch, statusFilter, errorCategoryFilter, dominantFailureFilter, sortField, sortOrder]);

  const handleFilterChange = (value: string) => {
    setStatusFilter(value);
    setOffset(0);
    setErrorCategoryFilter("all");
    setDominantFailureFilter("all");
  };

  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortOrder("desc");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          Tasks{!loading && total > 0 && (
            <span className="ml-2 text-base font-normal text-muted-foreground">
              ({total})
            </span>
          )}
        </h1>
        <Button onClick={() => router.push("/tasks/new")}>
          <Plus className="mr-2 size-4" />
          New Task
        </Button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            placeholder="Filter visible tasks..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex gap-1">
          {(["today", "7d", "30d", "all"] as DateRange[]).map((r) => (
            <Button
              key={r}
              variant={dateRange === r ? "secondary" : "ghost"}
              size="sm"
              onClick={() => { setDateRange(r); setOffset(0); }}
            >
              {r === "all" ? "All time" : r === "today" ? "Today" : r}
            </Button>
          ))}
        </div>
        {statusFilter === "failed" && (
          <>
            <Select
              value={errorCategoryFilter}
              onValueChange={(v) => setErrorCategoryFilter(v ?? "all")}
            >
              <SelectTrigger className="w-[160px]">
                <SelectValue placeholder="Error type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All errors</SelectItem>
                <SelectItem value="transient">Transient</SelectItem>
                <SelectItem value="permanent">Permanent</SelectItem>
                <SelectItem value="rate_limited">Rate limited</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={dominantFailureFilter}
              onValueChange={(v) => setDominantFailureFilter(v ?? "all")}
            >
              <SelectTrigger className="w-[160px]">
                <SelectValue placeholder="Failure class" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All failures</SelectItem>
                <SelectItem value="ui">UI Issues</SelectItem>
                <SelectItem value="network">Network / Auth</SelectItem>
                <SelectItem value="goal">Goal Not Met</SelectItem>
                <SelectItem value="policy">Policy / Crash</SelectItem>
              </SelectContent>
            </Select>
          </>
        )}
      </div>

      {/* Status filter */}
      <div className="flex gap-1 flex-wrap">
        {(["all", "queued", "running", "completed", "failed", "repaired"] as const).map((s) => (
          <Button
            key={s}
            variant={statusFilter === s ? "secondary" : "ghost"}
            size="sm"
            onClick={() => handleFilterChange(s)}
          >
            {s === "repaired" ? "Repaired" : s.charAt(0).toUpperCase() + s.slice(1)}
          </Button>
        ))}
      </div>

      {/* Task content */}
      <div className="mt-4">
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </div>
        ) : filteredAndSorted.length === 0 ? (
          <EmptyState
            icon={ListTodo}
            title="No tasks yet"
            description="Create your first browser automation task to get started."
            actionLabel="Create Task"
            onAction={() => router.push("/tasks/new")}
          />
        ) : (
          <>
            <TaskTable
              tasks={filteredAndSorted}
              sortField={sortField}
              sortOrder={sortOrder}
              onSort={handleSort}
            />
            <div className="flex items-center justify-between pt-4">
              <p className="text-sm text-muted-foreground">
                {debouncedSearch
                  ? `${filteredAndSorted.length} of ${tasks.length} visible`
                  : `Showing ${offset + 1}\u2013${Math.min(offset + PAGE_SIZE, total)} of ${total}`}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasMore}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
