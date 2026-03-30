"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { differenceInDays, formatDistanceToNow } from "date-fns";
import { Key } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { SessionDrawer, AUTH_STATE_CONFIG } from "@/components/session-drawer";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import type { SessionResponse } from "@/lib/types";

function AuthStateBadge({ state }: { state: string | null }) {
  const config = AUTH_STATE_CONFIG[state ?? ""] ?? {
    dot: "bg-muted-foreground",
    badge: "",
    label: state ?? "Unknown",
  };
  return (
    <Badge variant="secondary" className={config.badge}>
      <span className={`mr-1.5 inline-block size-2 rounded-full ${config.dot}`} />
      {config.label}
    </Badge>
  );
}

function StatsRow({ sessions }: { sessions: SessionResponse[] }) {
  const total = sessions.length;
  const active = sessions.filter(
    (s) => s.auth_state === "active" || s.auth_state === "authenticated"
  ).length;
  const stale = sessions.filter((s) => s.auth_state === "stale").length;

  return (
    <div className="space-y-2">
      <div className="flex gap-4">
        <div className="rounded-md border px-3 py-2">
          <p className="text-xs text-muted-foreground">Total</p>
          <p className="text-lg font-semibold">{total}</p>
        </div>
        <div className="rounded-md border px-3 py-2">
          <p className="text-xs text-muted-foreground">Active</p>
          <p className="text-lg font-semibold text-green-600 dark:text-green-400">{active}</p>
        </div>
        <div className="rounded-md border px-3 py-2">
          <p className="text-xs text-muted-foreground">Stale</p>
          <p className={`text-lg font-semibold ${stale > 0 ? "text-amber-600 dark:text-amber-400" : ""}`}>
            {stale}
          </p>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Sessions are created automatically when tasks require login.
      </p>
    </div>
  );
}

export default function SessionsPage() {
  const client = useApiClient();
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSession, setSelectedSession] = useState<SessionResponse | null>(null);

  const fetchSessions = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.listSessions();
      setSessions(res);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to fetch sessions");
    } finally {
      setLoading(false);
    }
  }, [client, router]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const isLastUsedStale = (lastUsedAt: string | null): boolean => {
    if (!lastUsedAt) return false;
    return differenceInDays(new Date(), new Date(lastUsedAt)) > 7;
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Sessions</h1>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={Key}
          title="No sessions"
          description="Session management coming soon. Sessions are created automatically when tasks use authenticated browsing."
        />
      ) : (
        <>
          <StatsRow sessions={sessions} />

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Domain</TableHead>
                <TableHead>Auth State</TableHead>
                <TableHead className="hidden sm:table-cell">Last Used</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((session) => (
                <TableRow
                  key={session.session_id}
                  className="cursor-pointer"
                  onClick={() => setSelectedSession(session)}
                >
                  <TableCell className="font-medium">
                    {session.origin_domain}
                  </TableCell>
                  <TableCell>
                    <AuthStateBadge state={session.auth_state} />
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">
                    {session.last_used_at ? (
                      <span
                        className={
                          isLastUsedStale(session.last_used_at)
                            ? "text-amber-600 dark:text-amber-400"
                            : "text-muted-foreground"
                        }
                      >
                        {formatDistanceToNow(new Date(session.last_used_at), {
                          addSuffix: true,
                        })}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">Never</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}

      <SessionDrawer
        session={selectedSession}
        open={!!selectedSession}
        onOpenChange={(open) => !open && setSelectedSession(null)}
        onDeleted={fetchSessions}
      />
    </div>
  );
}
