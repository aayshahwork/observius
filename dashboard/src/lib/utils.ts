import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { ErrorCategory } from "./types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCost(cents: number | null): string {
  if (cents == null || cents === 0) return "—"
  return `$${(cents / 100).toFixed(cents < 1 ? 4 : 2)}`
}

export function formatTokens(count: number | null): string {
  if (count == null || count === 0) return "—"
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`
  return String(count)
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

const ERROR_CATEGORY_LABELS: Record<ErrorCategory, string> = {
  transient_llm: "Transient (LLM)",
  rate_limited: "Rate Limited",
  transient_network: "Transient (Network)",
  transient_browser: "Transient (Browser)",
  permanent_llm: "Permanent (LLM)",
  permanent_browser: "Permanent (Browser)",
  permanent_task: "Permanent (Task)",
  unknown: "Unknown",
}

export function getErrorCategoryLabel(cat: ErrorCategory): string {
  return ERROR_CATEGORY_LABELS[cat] ?? cat
}

const ERROR_CATEGORY_COLORS: Record<ErrorCategory, string> = {
  transient_llm: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  rate_limited: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  transient_network: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  transient_browser: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  permanent_llm: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  permanent_browser: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  permanent_task: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  unknown: "bg-gray-100 text-gray-800 dark:bg-gray-900/40 dark:text-gray-300",
}

export function getErrorCategoryColor(cat: ErrorCategory): string {
  return ERROR_CATEGORY_COLORS[cat] ?? ERROR_CATEGORY_COLORS.unknown
}

export function isRetryable(cat: ErrorCategory): boolean {
  return cat.startsWith("transient_") || cat === "rate_limited"
}
