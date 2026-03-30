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
import type { StepResponse } from "@/lib/types";

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
};

function getActionConfig(actionType: string) {
  return ACTION_CONFIG[actionType.toLowerCase()] ?? { icon: HelpCircle, label: actionType };
}

function estimateCostCents(tokensIn: number, tokensOut: number): number {
  // claude-sonnet-4-6: $3/1M input, $15/1M output
  return (tokensIn * 3 + tokensOut * 15) / 10_000;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StepTimelineProps {
  steps: StepResponse[];
}

const SPEEDS = [1, 2, 4] as const;

export function StepTimeline({ steps }: StepTimelineProps) {
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
                    <div className="text-center">
                      <ImageOff className="mx-auto size-8 text-muted-foreground" />
                      <p className="mt-2 text-sm text-muted-foreground">
                        No screenshot captured
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
