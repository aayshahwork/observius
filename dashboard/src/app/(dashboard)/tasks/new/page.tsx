"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowLeft, ChevronDown } from "lucide-react";
import { AUTH_STATE_CONFIG } from "@/components/session-drawer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonEditor } from "@/components/json-editor";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import type { ExecutorMode, SessionResponse } from "@/lib/types";

export default function NewTaskPage() {
  const client = useApiClient();
  const router = useRouter();

  const [url, setUrl] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  const [schemaRaw, setSchemaRaw] = useState("");
  const [schemaParsed, setSchemaParsed] = useState<Record<string, unknown> | null>(null);
  const [timeout, setTimeout] = useState("300");
  const [maxRetries, setMaxRetries] = useState("3");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [executorMode, setExecutorMode] = useState<ExecutorMode>("browser_use");
  const [maxCostCents, setMaxCostCents] = useState("");
  const [skyvernEngine, setSkyvernEngine] = useState("");
  const [proxyLocation, setProxyLocation] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!client) return;
    client.listSessions().then(setSessions).catch(() => {});
  }, [client]);

  const canSubmit = url.trim() && taskDesc.trim() && !loading;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!client || !canSubmit) return;

    setError(null);
    setLoading(true);

    try {
      const res = await client.createTask({
        url: url.trim(),
        task: taskDesc.trim(),
        output_schema: schemaParsed ?? undefined,
        timeout_seconds: parseInt(timeout, 10),
        max_retries: parseInt(maxRetries, 10),
        webhook_url: webhookUrl.trim() || undefined,
        executor_mode: executorMode,
        max_cost_cents: maxCostCents ? parseInt(maxCostCents, 10) : undefined,
        session_id: sessionId || undefined,
        skyvern_engine: executorMode === "skyvern" && skyvernEngine ? skyvernEngine : undefined,
        proxy_location: executorMode === "skyvern" && proxyLocation ? proxyLocation : undefined,
      });
      toast.success("Task created");
      router.push(`/tasks/${res.task_id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to create task");
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <Button variant="ghost" onClick={() => router.push("/tasks")}>
        <ArrowLeft className="mr-2 size-4" />
        Back to Tasks
      </Button>

      <Card>
        <CardHeader>
          <CardTitle>New Task</CardTitle>
          <p className="text-sm text-muted-foreground">
            Configure and launch a browser automation task.
          </p>
        </CardHeader>
        <CardContent className="pt-0">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="url">URL *</Label>
              <Input
                id="url"
                type="url"
                placeholder="https://example.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="task">Task Description *</Label>
              <Textarea
                id="task"
                placeholder="Describe what the browser should do..."
                value={taskDesc}
                onChange={(e) => setTaskDesc(e.target.value)}
                rows={4}
                required
              />
            </div>

            <JsonEditor
              label="Output Schema (optional)"
              value={schemaRaw}
              onChange={(raw, parsed) => {
                setSchemaRaw(raw);
                setSchemaParsed(parsed);
              }}
            />

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Timeout</Label>
                <Select value={timeout} onValueChange={(v) => v && setTimeout(v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30 seconds</SelectItem>
                    <SelectItem value="60">1 minute</SelectItem>
                    <SelectItem value="120">2 minutes</SelectItem>
                    <SelectItem value="300">5 minutes</SelectItem>
                    <SelectItem value="600">10 minutes</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>Max Retries</Label>
                <Select value={maxRetries} onValueChange={(v) => v && setMaxRetries(v)}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">0</SelectItem>
                    <SelectItem value="1">1</SelectItem>
                    <SelectItem value="2">2</SelectItem>
                    <SelectItem value="3">3</SelectItem>
                    <SelectItem value="4">4</SelectItem>
                    <SelectItem value="5">5</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="webhook">Webhook URL (optional)</Label>
              <Input
                id="webhook"
                type="url"
                placeholder="https://your-server.com/webhook"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
              />
            </div>

            {/* Advanced Options */}
            <div className="rounded-lg border">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium"
                onClick={() => setAdvancedOpen(!advancedOpen)}
              >
                Advanced Options
                <ChevronDown
                  className={`size-4 text-muted-foreground transition-transform ${advancedOpen ? "rotate-180" : ""}`}
                />
              </button>
              {advancedOpen && (
                <div className="space-y-4 border-t px-4 py-4">
                  <fieldset className="space-y-3">
                    <Label>Execution Engine</Label>
                    <div className="grid grid-cols-3 gap-2">
                      {([
                        { value: "browser_use" as const, label: "Browser Use", desc: "DOM-based automation via browser-use. Best for structured pages." },
                        { value: "native" as const, label: "Anthropic CUA", desc: "Claude\u2019s computer vision (screenshot pixel-based). Handles complex visual layouts." },
                        { value: "skyvern" as const, label: "Skyvern", desc: "Cloud-hosted browser via Skyvern API. Goal delegation with video recording." },
                      ]).map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => setExecutorMode(opt.value)}
                          className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                            executorMode === opt.value
                              ? "border-primary bg-primary/5 ring-1 ring-primary"
                              : "border-border hover:border-muted-foreground/40"
                          }`}
                          title={opt.desc}
                        >
                          <div className="font-medium">{opt.label}</div>
                          <div className="mt-1 text-xs text-muted-foreground leading-snug">{opt.desc}</div>
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  {executorMode === "skyvern" && (
                    <div className="grid grid-cols-2 gap-4 rounded-lg border border-dashed p-3">
                      <div className="space-y-2">
                        <Label htmlFor="skyvern-engine">Skyvern Engine</Label>
                        <Select value={skyvernEngine} onValueChange={(v) => setSkyvernEngine(v ?? "")}>
                          <SelectTrigger id="skyvern-engine" className="w-full">
                            <SelectValue placeholder="Default (skyvern-2.0)" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="">Default (skyvern-2.0)</SelectItem>
                            <SelectItem value="skyvern-2.0">skyvern-2.0</SelectItem>
                            <SelectItem value="skyvern-1.0">skyvern-1.0</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="proxy-location">Proxy Location</Label>
                        <Select value={proxyLocation} onValueChange={(v) => setProxyLocation(v ?? "")}>
                          <SelectTrigger id="proxy-location" className="w-full">
                            <SelectValue placeholder="None" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="">None</SelectItem>
                            <SelectItem value="RESIDENTIAL">Residential</SelectItem>
                            <SelectItem value="DATACENTER">Datacenter</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  )}

                  {sessions.length > 0 && (
                    <div className="space-y-2">
                      <Label>Session (optional)</Label>
                      <Select value={sessionId} onValueChange={(v) => setSessionId(v ?? "")}>
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder="Auto — no session" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="">Auto</SelectItem>
                          {sessions
                            .filter((s) => s.auth_state !== "expired")
                            .map((s) => {
                              const dotColor =
                                AUTH_STATE_CONFIG[s.auth_state ?? ""]?.dot ?? "bg-muted-foreground";
                              return (
                                <SelectItem key={s.session_id} value={s.session_id}>
                                  <span className="flex items-center gap-2">
                                    <span className={`inline-block size-2 rounded-full ${dotColor}`} />
                                    {s.origin_domain}
                                  </span>
                                </SelectItem>
                              );
                            })}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        Reuse an authenticated session to skip login.
                      </p>
                    </div>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="max-cost">Max cost (cents)</Label>
                    <Input
                      id="max-cost"
                      type="number"
                      min="1"
                      placeholder="e.g. 50"
                      value={maxCostCents}
                      onChange={(e) => setMaxCostCents(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Task will stop if LLM cost exceeds this limit. Leave blank for no limit.
                    </p>
                  </div>
                </div>
              )}
            </div>

            {error && (
              <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            <Button type="submit" className="w-full" disabled={!canSubmit}>
              {loading ? "Creating..." : "Create Task"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
