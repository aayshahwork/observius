"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { format } from "date-fns";
import { toast } from "sonner";
import { FileCode2, RefreshCw, ClipboardCopy, Check, ExternalLink } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useApiClient } from "@/hooks/use-api-client";
import type { ScriptEntry } from "@/lib/types";

function ScriptDialog({
  script,
  open,
  onOpenChange,
}: {
  script: ScriptEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!script) return;
    await navigator.clipboard.writeText(script.playwright_script);
    setCopied(true);
    toast.success("Script copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  if (!script) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-center justify-between pr-8">
            <DialogTitle className="text-sm font-medium">
              {script.task_description || "Playwright Script"}
            </DialogTitle>
            <Button variant="outline" size="sm" onClick={handleCopy}>
              {copied ? (
                <Check className="mr-1.5 size-3.5" />
              ) : (
                <ClipboardCopy className="mr-1.5 size-3.5" />
              )}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </DialogHeader>
        <div className="flex-1 overflow-auto rounded-md border bg-muted/50 p-4">
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed">
            {script.playwright_script}
          </pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function ScriptsPage() {
  const client = useApiClient();
  const router = useRouter();
  const [scripts, setScripts] = useState<ScriptEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedScript, setSelectedScript] = useState<ScriptEntry | null>(null);

  const fetchScripts = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.listScripts({ limit: 50 });
      setScripts(res.scripts);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch scripts");
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    fetchScripts();
  }, [fetchScripts]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Scripts</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setLoading(true);
            fetchScripts();
          }}
          disabled={loading}
        >
          <RefreshCw
            className={`mr-2 size-4 ${loading ? "animate-spin" : ""}`}
          />
          Refresh
        </Button>
      </div>

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
      ) : scripts.length === 0 ? (
        <EmptyState
          icon={FileCode2}
          title="No saved scripts"
          description="Playwright scripts are saved when you click the 'Playwright Script' button on a task's workflow tab."
          actionLabel="View Tasks"
          onAction={() => router.push("/tasks")}
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Task</TableHead>
              <TableHead className="hidden sm:table-cell">URL</TableHead>
              <TableHead className="hidden md:table-cell">Created</TableHead>
              <TableHead className="w-[100px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {scripts.map((script) => (
              <TableRow key={script.task_id}>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium truncate max-w-[300px]">
                      {script.task_description || "Untitled"}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {script.task_id.slice(0, 8)}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="hidden sm:table-cell">
                  <span className="truncate max-w-[200px] text-xs text-muted-foreground block">
                    {script.url}
                  </span>
                </TableCell>
                <TableCell className="hidden md:table-cell text-sm text-muted-foreground">
                  {script.created_at
                    ? format(new Date(script.created_at), "MMM d, yyyy")
                    : "-"}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSelectedScript(script)}
                    >
                      <FileCode2 className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => router.push(`/tasks/${script.task_id}`)}
                    >
                      <ExternalLink className="size-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <ScriptDialog
        script={selectedScript}
        open={!!selectedScript}
        onOpenChange={(open) => !open && setSelectedScript(null)}
      />
    </div>
  );
}
