"use client";

import { useMemo } from "react";
import { useAuth } from "@/contexts/auth-context";
import { ApiClient } from "@/lib/api-client";

// Single source of truth: reads API key from AuthContext, returns memoized ApiClient.
export function useApiClient(): ApiClient | null {
  const { apiKey } = useAuth();
  return useMemo(() => (apiKey ? new ApiClient(apiKey) : null), [apiKey]);
}
