"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Mouse,
  Keyboard,
  ArrowDownUp,
  Globe,
  Clock,
  Download,
  HelpCircle,
  CheckCircle2,
  XCircle,
  ChevronLeft,
  ChevronRight,
  Play,
  Pause,
  ImageOff,
  AppWindow,
  Monitor,
  Layers,
  Menu,
  FileInput,
  Save,
  type LucideIcon,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { formatTokens, formatDuration } from "@/lib/utils";
import type { StepResponse, ExecutorMode } from "@/lib/types";

// ---------------------------------------------------------------------------
// Action type config
// ---------------------------------------------------------------------------

const ACTION_CONFIG: Record<string, { icon: LucideIcon; label: string }> = {
  click: { icon: Mouse, label: "Click" },
  double_click: { icon: Mouse, label: "Double Click" },
  right_click: { icon: Mouse, label: "Right Click" },
  middle_click: { icon: Mouse, label: "Middle Click" },
  triple_click: { icon: Mouse, label: "Triple Click" },
  type: { icon: Keyboard, label: "Type" },
  key_press: { icon: Keyboard, label: "Key Press" },
  scroll: { icon: ArrowDownUp, label: "Scroll" },
  navigate: { icon: Globe, label: "Navigate" },
  wait: { icon: Clock, label: "Wait" },
  extract: { icon: Download, label: "Extract" },
  mouse_move: { icon: Mouse, label: "Mouse Move" },
  screenshot: { icon: ImageOff, label: "Screenshot" },
  inject_credentials: { icon: Keyboard, label: "Inject Credentials" },
  solve_captcha: { icon: HelpCircle, label: "Solve Captcha" },
  drag: { icon: Mouse, label: "Drag" },
  zoom: { icon: ArrowDownUp, label: "Zoom" },
  // Desktop automation
  desktop_click: { icon: Mouse, label: "Desktop Click" },
  desktop_type: { icon: Keyboard, label: "Desktop Type" },
  desktop_hotkey: { icon: Keyboard, label: "Desktop Hotkey" },
  desktop_scroll: { icon: ArrowDownUp, label: "Desktop Scroll" },
  desktop_drag: { icon: Mouse, label: "Desktop Drag" },
  desktop_launch: { icon: AppWindow, label: "Launch App" },
  desktop_focus: { icon: Monitor, label: "Focus Window" },
  window_switch: { icon: Layers, label: "Switch Window" },
  menu_select: { icon: Menu, label: "Menu Select" },
  file_open: { icon: FileInput, label: "File Open" },
  file_save: { icon: Save, label: "File Save" },
};

function getActionConfig(actionType: string) {
  return ACTION_CONFIG[actionType.toLowerCase()] ?? { icon: HelpCircle, label: actionType };
}

function estimateCostCents(tokensIn: number, tokensOut: number): number {
  // claude-sonnet-4-6: $3/1M input, $15/1M output
  return (tokensIn * 3 + tokensOut * 15) / 10_000;
}

const NON_VISUAL_ACTIONS = new Set(["llm_call", "api_call", "state_snapshot"]);
const DESKTOP_ACTIONS = new Set([
  "desktop_click", "desktop_type", "desktop_hotkey", "desktop_scroll",
  "desktop_drag", "desktop_launch", "desktop_focus", "window_switch",
  "menu_select", "file_open", "file_save",
]);

function getNoScreenshotMessage(
  executorMode: ExecutorMode | undefined,
  actionType: string,
): string {
  if (executorMode === "sdk") {
    if (NON_VISUAL_ACTIONS.has(actionType.toLowerCase())) {
      return "No visual \u2014 this is a non-browser step. Check Debug Context below for details.";
    }
    if (DESKTOP_ACTIONS.has(actionType.toLowerCase())) {
      return "No screenshot \u2014 pass screenshot_fn= to PokantTracker for auto-screenshots.";
    }
    return "No screenshot \u2014 agent ran without a browser page. Pass page= to PokantTracker for auto-screenshots.";
  }

  if (executorMode === "browser_use" || executorMode === "native") {
    return "Screenshot capture failed for this step.";
  }

  return "No screenshot captured";
}

// ---------------------------------------------------------------------------
// Step context renderer
// ---------------------------------------------------------------------------

function StepContext({ context }: { context: Record<string, unknown> }) {
  const type = context.type as string | undefined;

  if (type === "subtask") {
    const name = context.subtask_name as string | undefined;
    const num = context.subtask_number as number | undefined;
    const total = context.subtask_total as number | undefined;
    const url = context.subtask_url as string | undefined;
    if (!name) return null;
    return (
      <div>
        <div className="flex items-center gap-2 rounded-md border bg-primary/5 px-3 py-2">
          <Layers className="size-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              {num != null && total != null && (
                <Badge variant="outline" className="text-[10px] shrink-0">
                  {num}/{total}
                </Badge>
              )}
              <span className="text-sm font-medium truncate">{name}</span>
            </div>
            {url && (
              <p className="text-xs text-muted-foreground truncate mt-0.5">{url}</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (type === "desktop_action") {
    const windowTitle = context.window_title as string | undefined;
    const coords = context.coordinates as { x: number; y: number } | undefined;
    if (!windowTitle && !coords) return null;
    return (
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">Desktop Action</p>
        <div className="rounded-md border bg-muted/50 px-3 py-2 space-y-1">
          {windowTitle && (
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-muted-foreground">Window</span>
              <span className="text-sm font-medium">{windowTitle}</span>
            </div>
          )}
          {coords && (
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-muted-foreground">Coordinates</span>
              <span className="text-sm font-mono">({coords.x}, {coords.y})</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (type === "llm_call") {
    const prompt = context.prompt as string | undefined;
    const response = context.response as string | undefined;
    const model = context.model as string | undefined;
    if (!prompt && !response) return null;
    return (
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">LLM Trace</p>
        <div className="rounded-md border bg-muted/50 px-3 py-2 space-y-2 text-sm">
          {prompt && (
            <div>
              <p className="text-xs text-muted-foreground mb-0.5">Prompt</p>
              <pre className="whitespace-pre-wrap text-xs">{prompt.slice(0, 2000)}</pre>
            </div>
          )}
          {response && (
            <div>
              <p className="text-xs text-muted-foreground mb-0.5">Response</p>
              <pre className="whitespace-pre-wrap text-xs">{response.slice(0, 2000)}</pre>
            </div>
          )}
          {model && <p className="text-xs text-muted-foreground">Model: {model}</p>}
        </div>
      </div>
    );
  }

  if (type === "enrichment") {
    const intent = context.intent as string | undefined;
    const intentDetail = context.intent_detail as string | undefined;
    if (!intent) return null;
    return (
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">Intent</p>
        <div className="rounded-md border bg-muted/50 px-3 py-2 space-y-1">
          <div className="flex items-baseline gap-2">
            <span className="text-xs text-muted-foreground">Action</span>
            <span className="text-sm font-medium capitalize">{intent.replace(/_/g, " ")}</span>
          </div>
          {intentDetail && (
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-muted-foreground">Detail</span>
              <span className="text-sm text-muted-foreground">{intentDetail}</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (type === "api_call") {
    const method = context.method as string | undefined;
    const url = context.url as string | undefined;
    const statusCode = context.status_code as number | undefined;
    if (!method && !url) return null;
    return (
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">API Call</p>
        <div className="rounded-md border bg-muted/50 px-3 py-2 flex items-baseline gap-2">
          {method && <span className="text-sm font-mono font-semibold">{method}</span>}
          {url && <span className="text-sm text-primary truncate">{url}</span>}
          {statusCode != null && (
            <Badge variant={statusCode < 400 ? "secondary" : "destructive"} className="text-xs ml-auto shrink-0">
              {statusCode}
            </Badge>
          )}
        </div>
      </div>
    );
  }

  // Generic fallback — collapsible JSON
  return (
    <details>
      <summary className="text-xs font-medium text-muted-foreground cursor-pointer">
        Debug Context
      </summary>
      <pre className="mt-1 rounded-md border bg-muted/50 px-3 py-2 text-xs whitespace-pre-wrap overflow-x-auto">
        {JSON.stringify(context, null, 2)}
      </pre>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StepTimelineProps {
  steps: StepResponse[];
  executorMode?: ExecutorMode;
}

const SPEEDS = [1, 2, 4] as const;

export function StepTimeline({ steps, executorMode }: StepTimelineProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [descExpanded, setDescExpanded] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const step = steps[currentIndex];
  const total = steps.length;

  // Auto-play
  useEffect(() => {
    if (!playing || total === 0) return;

    const ms = 2000 / speed;
    const interval = setInterval(() => {
      setCurrentIndex((prev) => {
        if (prev >= total - 1) {
          setPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, ms);

    return () => clearInterval(interval);
  }, [playing, speed, total]);

  // Collapse description when step changes
  useEffect(() => {
    setDescExpanded(false);
  }, [currentIndex]);

  // Keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      switch (e.key) {
        case "ArrowLeft":
          e.preventDefault();
          setCurrentIndex((prev) => Math.max(0, prev - 1));
          break;
        case "ArrowRight":
          e.preventDefault();
          setCurrentIndex((prev) => Math.min(total - 1, prev + 1));
          break;
        case " ":
          e.preventDefault();
          setPlaying((prev) => !prev);
          break;
      }
    },
    [total],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener("keydown", handleKeyDown);
    return () => el.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  if (total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Steps</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-sm text-muted-foreground">No step data available.</p>
        </CardContent>
      </Card>
    );
  }

  const actionCfg = getActionConfig(step.action_type);
  const ActionIcon = actionCfg.icon;
  const costCents = estimateCostCents(step.tokens_in, step.tokens_out);
  const desc = step.description ?? "";
  const descTruncated = desc.length > 200;
  const descDisplay = descExpanded ? desc : desc.slice(0, 200);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Steps</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div
          ref={containerRef}
          tabIndex={0}
          className="space-y-4 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-md"
        >
          {/* Split pane: screenshot + detail */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[55fr_45fr]">
            {/* Left — Screenshot */}
            <div className="space-y-3">
              <div className="relative overflow-hidden rounded-md border bg-muted">
                {step.screenshot_url ? (
                  <img
                    src={step.screenshot_url}
                    alt={`Step ${step.step_number} screenshot`}
                    className="block w-full h-auto"
                    draggable={false}
                  />
                ) : (
                  <div className="flex aspect-video items-center justify-center">
                    <div className="text-center max-w-xs">
                      <ImageOff className="mx-auto size-8 text-muted-foreground" />
                      <p className="mt-2 text-sm text-muted-foreground">
                        {getNoScreenshotMessage(executorMode, step.action_type)}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {/* Playback controls */}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="icon"
                    className="size-8"
                    disabled={currentIndex === 0}
                    onClick={() => setCurrentIndex((prev) => Math.max(0, prev - 1))}
                  >
                    <ChevronLeft className="size-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="size-8"
                    onClick={() => setPlaying((prev) => !prev)}
                  >
                    {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="size-8"
                    disabled={currentIndex === total - 1}
                    onClick={() => setCurrentIndex((prev) => Math.min(total - 1, prev + 1))}
                  >
                    <ChevronRight className="size-4" />
                  </Button>
                </div>

                <span className="text-xs tabular-nums text-muted-foreground">
                  Step {currentIndex + 1} / {total}
                </span>

                <div className="flex items-center gap-1">
                  {SPEEDS.map((s) => (
                    <Button
                      key={s}
                      variant={speed === s ? "default" : "outline"}
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => setSpeed(s)}
                    >
                      {s}x
                    </Button>
                  ))}
                </div>
              </div>
            </div>

            {/* Right — Step detail */}
            <div className="space-y-4">
              {/* Action type */}
              <div className="flex items-center gap-2">
                <Badge variant="secondary" className="gap-1.5">
                  <ActionIcon className="size-3.5" />
                  {actionCfg.label}
                </Badge>
                {step.success ? (
                  <CheckCircle2 className="size-4 text-green-600 dark:text-green-400" />
                ) : (
                  <XCircle className="size-4 text-red-600 dark:text-red-400" />
                )}
              </div>

              {/* Metadata grid */}
              <dl className="grid grid-cols-2 gap-3">
                <div>
                  <dt className="text-xs text-muted-foreground">Duration</dt>
                  <dd className="mt-0.5 text-sm tabular-nums">
                    {formatDuration(step.duration_ms)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Tokens</dt>
                  <dd className="mt-0.5 text-sm tabular-nums">
                    {step.tokens_in || step.tokens_out
                      ? `\u2191${formatTokens(step.tokens_in)} \u2193${formatTokens(step.tokens_out)}`
                      : "\u2014"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Est. Cost</dt>
                  <dd className="mt-0.5 text-sm tabular-nums">
                    {costCents > 0 ? `$${(costCents / 100).toFixed(4)}` : "\u2014"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Step #</dt>
                  <dd className="mt-0.5 text-sm tabular-nums">{step.step_number}</dd>
                </div>
              </dl>

              {/* Error */}
              {step.error && (
                <div className="rounded-md border border-destructive/50 bg-destructive/10 p-2">
                  <p className="text-xs text-destructive">{step.error}</p>
                </div>
              )}

              {/* Description / reasoning */}
              {desc && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Description</p>
                  <p className="text-sm whitespace-pre-wrap">{descDisplay}</p>
                  {descTruncated && (
                    <button
                      type="button"
                      className="mt-1 text-xs text-primary hover:underline"
                      onClick={() => setDescExpanded((prev) => !prev)}
                    >
                      {descExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}

              {/* Step context */}
              {step.context && <StepContext context={step.context} />}
            </div>
          </div>

          {/* Bottom — Timeline bar */}
          <TooltipProvider>
            <div className="flex gap-px rounded-md overflow-hidden" role="group" aria-label="Step timeline">
              {steps.map((s, i) => (
                <Tooltip key={s.step_number}>
                  <TooltipTrigger
                    className={cn(
                      "h-3 flex-1 cursor-pointer transition-all",
                      s.success
                        ? "bg-green-500/60 hover:bg-green-500/80 dark:bg-green-600/50 dark:hover:bg-green-600/70"
                        : "bg-red-500/60 hover:bg-red-500/80 dark:bg-red-600/50 dark:hover:bg-red-600/70",
                      i === currentIndex && "ring-2 ring-primary ring-offset-1 ring-offset-background",
                    )}
                    onClick={() => setCurrentIndex(i)}
                  />
                  <TooltipContent>
                    Step {s.step_number}: {getActionConfig(s.action_type).label} ({formatDuration(s.duration_ms)})
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          </TooltipProvider>

          <p className="text-[11px] text-muted-foreground">
            Tip: Use arrow keys to navigate steps, space to play/pause
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
