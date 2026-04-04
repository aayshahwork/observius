"use client";

import { useState } from "react";
import {
  ArrowRight,
  Check,
  ClipboardCopy,
  Download,
  Globe,
  MousePointer2,
  Keyboard,
  Eye,
  Timer,
  Play,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { generatePlaywrightScript } from "@/lib/workflow-utils";
import type { ApiClient } from "@/lib/api-client";
import type { CompiledWorkflow, CompiledStep } from "@/lib/types";

const ACTION_ICONS: Record<string, typeof Globe> = {
  goto: Globe,
  click: MousePointer2,
  fill: Keyboard,
  select_option: MousePointer2,
  press: Keyboard,
  scroll: ArrowRight,
  wait: Timer,
  extract: Eye,
  dblclick: MousePointer2,
  hover: MousePointer2,
  right_click: MousePointer2,
};

const ACTION_COLORS: Record<string, string> = {
  goto: "bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300",
  click: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300",
  fill: "bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300",
  extract: "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300",
  wait: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

function StepRow({ step, index }: { step: CompiledStep; index: number }) {
  const Icon = ACTION_ICONS[step.action_type] ?? Play;
  const colorClass =
    ACTION_COLORS[step.action_type] ??
    "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
  const topSelector = step.selectors?.[0];

  return (
    <div className="flex items-start gap-3 rounded-md border bg-background/60 p-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium tabular-nums">
        {index + 1}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
              colorClass,
            )}
          >
            <Icon className="size-3" />
            {step.action_type}
          </span>
          {step.intent && (
            <span className="truncate text-sm text-muted-foreground">
              {step.intent}
            </span>
          )}
        </div>
        {topSelector && (
          <p className="truncate font-mono text-xs text-muted-foreground">
            {topSelector.type}: {topSelector.value}
            {topSelector.confidence > 0 && (
              <span className="ml-1 text-[10px] tabular-nums opacity-60">
                ({Math.round(topSelector.confidence * 100)}%)
              </span>
            )}
          </p>
        )}
        {step.fill_value_template && (
          <p className="truncate font-mono text-xs text-muted-foreground">
            value: {step.fill_value_template}
          </p>
        )}
      </div>
    </div>
  );
}

interface WorkflowPanelProps {
  workflow: CompiledWorkflow;
  taskId: string;
  client: ApiClient | null;
}

export function WorkflowPanel({ workflow, taskId, client }: WorkflowPanelProps) {
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);

  const paramNames = Object.keys(workflow.parameters);

  const handleDownloadJson = () => {
    const json = JSON.stringify(workflow, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${workflow.name || "workflow"}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Workflow JSON downloaded");
  };

  const handleCopyScript = async () => {
    const script = generatePlaywrightScript(workflow);
    await navigator.clipboard.writeText(script);
    setCopied(true);
    toast.success("Playwright script copied to clipboard");
    setTimeout(() => setCopied(false), 2000);

    // Also persist to database
    if (client) {
      setSaving(true);
      try {
        await client.savePlaywrightScript(taskId, script);
        toast.success("Playwright script saved");
      } catch {
        toast.error("Failed to save script to database");
      } finally {
        setSaving(false);
      }
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm">Compiled Workflow</CardTitle>
            <Badge variant="outline" className="text-[10px] font-normal">
              {workflow.steps.length} step{workflow.steps.length !== 1 && "s"}
            </Badge>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleDownloadJson}>
              <Download className="mr-1.5 size-3.5" />
              JSON
            </Button>
            <Button variant="outline" size="sm" onClick={handleCopyScript}>
              {copied ? (
                <Check className="mr-1.5 size-3.5" />
              ) : (
                <ClipboardCopy className="mr-1.5 size-3.5" />
              )}
              {copied ? "Copied" : "Playwright Script"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        {/* Metadata */}
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {workflow.start_url && (
            <div>
              <dt className="text-xs text-muted-foreground">Start URL</dt>
              <dd className="mt-0.5 truncate font-mono text-xs">
                {workflow.start_url}
              </dd>
            </div>
          )}
          {workflow.compiled_at && (
            <div>
              <dt className="text-xs text-muted-foreground">Compiled</dt>
              <dd className="mt-0.5 text-xs">
                {new Date(workflow.compiled_at).toLocaleString()}
              </dd>
            </div>
          )}
          {workflow.source_task_id && (
            <div>
              <dt className="text-xs text-muted-foreground">Source Task</dt>
              <dd className="mt-0.5 truncate font-mono text-xs">
                {workflow.source_task_id}
              </dd>
            </div>
          )}
        </dl>

        {/* Parameters */}
        {paramNames.length > 0 && (
          <div>
            <h4 className="mb-1.5 text-xs font-medium text-muted-foreground">
              Parameters
            </h4>
            <div className="flex flex-wrap gap-1.5">
              {paramNames.map((p) => (
                <Badge key={p} variant="secondary" className="font-mono text-xs">
                  {`{{${p}}}`}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Steps */}
        <div className="space-y-2">
          {workflow.steps.map((step, i) => (
            <StepRow key={i} step={step} index={i} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
