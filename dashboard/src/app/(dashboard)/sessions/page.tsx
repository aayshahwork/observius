"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { Key, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import type { SessionResponse } from "@/lib/types";

export default function SessionsPage() {
  const client = useApiClient();
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

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

  const handleDelete = async () => {
    if (!client || !deleteTarget) return;
    setDeleting(true);
    try {
      await client.deleteSession(deleteTarget);
      setDeleteTarget(null);
      fetchSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete session");
    } finally {
      setDeleting(false);
    }
  };

  const authStateBadge = (state: string | null) => {
    if (!state) return <Badge variant="secondary">Unknown</Badge>;
    switch (state) {
      case "active":
        return <Badge className="bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300">Active</Badge>;
      case "stale":
        return <Badge className="bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300">Stale</Badge>;
      case "expired":
        return <Badge variant="secondary">Expired</Badge>;
      default:
        return <Badge variant="secondary">{state}</Badge>;
    }
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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Domain</TableHead>
              <TableHead>Auth State</TableHead>
              <TableHead className="hidden sm:table-cell">Last Used</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessions.map((session) => (
              <TableRow key={session.session_id}>
                <TableCell className="font-medium">
                  {session.origin_domain}
                </TableCell>
                <TableCell>{authStateBadge(session.auth_state)}</TableCell>
                <TableCell className="hidden sm:table-cell text-muted-foreground">
                  {session.last_used_at
                    ? formatDistanceToNow(new Date(session.last_used_at), {
                        addSuffix: true,
                      })
                    : "Never"}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setDeleteTarget(session.session_id)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete Session"
        description="This will delete the session and its stored cookies. Any tasks using this session will need to re-authenticate."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleting}
        onConfirm={handleDelete}
      />
    </div>
  );
}
