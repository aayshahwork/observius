"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Copy, Plus, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiClient } from "@/hooks/use-api-client";
import { ApiError } from "@/lib/api-client";
import type { BillingUsageResponse, ApiKeyResponse } from "@/lib/types";

const TIERS = ["free", "startup", "growth", "enterprise"] as const;

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  startup: "Startup",
  growth: "Growth",
  enterprise: "Enterprise",
};

export default function SettingsPage() {
  const client = useApiClient();
  const router = useRouter();

  // Billing state
  const [billing, setBilling] = useState<BillingUsageResponse | null>(null);
  const [billingLoading, setBillingLoading] = useState(true);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);

  // API keys state
  const [apiKeys, setApiKeys] = useState<ApiKeyResponse[]>([]);
  const [keysLoading, setKeysLoading] = useState(true);
  const [keysError, setKeysError] = useState<string | null>(null);
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const fetchBilling = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.getBillingUsage();
      setBilling(res);
      setBillingError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setBillingError(
        err instanceof Error ? err.message : "Failed to fetch billing"
      );
    } finally {
      setBillingLoading(false);
    }
  }, [client, router]);

  const fetchApiKeys = useCallback(async () => {
    if (!client) return;
    try {
      const res = await client.listApiKeys();
      setApiKeys(res);
      setKeysError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setKeysError(
        err instanceof Error ? err.message : "Failed to fetch API keys"
      );
    } finally {
      setKeysLoading(false);
    }
  }, [client, router]);

  useEffect(() => {
    fetchBilling();
    fetchApiKeys();
  }, [fetchBilling, fetchApiKeys]);

  const handleUpgrade = async (tier: string) => {
    if (!client) return;
    setCheckoutLoading(tier);
    try {
      const res = await client.createCheckout(tier);
      window.location.href = res.checkout_url;
    } catch (err) {
      setBillingError(
        err instanceof Error ? err.message : "Failed to create checkout"
      );
    } finally {
      setCheckoutLoading(null);
    }
  };

  const handlePortal = async () => {
    if (!client) return;
    try {
      const res = await client.createPortal();
      window.location.href = res.portal_url;
    } catch (err) {
      setBillingError(
        err instanceof Error ? err.message : "Failed to open billing portal"
      );
    }
  };

  const handleCreateKey = async () => {
    if (!client) return;
    setCreating(true);
    setCreatedKey(null);
    try {
      const res = await client.createApiKey(newKeyLabel || undefined);
      setCreatedKey(res.key);
      setNewKeyLabel("");
      await fetchApiKeys();
    } catch (err) {
      setKeysError(
        err instanceof Error ? err.message : "Failed to create API key"
      );
    } finally {
      setCreating(false);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!client) return;
    try {
      await client.revokeApiKey(keyId);
      await fetchApiKeys();
    } catch (err) {
      setKeysError(
        err instanceof Error ? err.message : "Failed to revoke API key"
      );
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const usagePercent =
    billing && billing.monthly_step_limit > 0
      ? Math.round(
          (billing.monthly_steps_used / billing.monthly_step_limit) * 100
        )
      : 0;

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Settings</h1>

      {/* ── Billing Section ── */}
      <section className="space-y-4">
        <h2 className="text-lg font-medium">Billing</h2>

        {billingLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : billingError ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            {billingError}
          </div>
        ) : billing ? (
          <>
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">Current Plan</CardTitle>
                  <Badge variant="secondary" className="capitalize">
                    {TIER_LABELS[billing.tier] ?? billing.tier}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4 pt-0">
                <Progress value={usagePercent}>
                  <ProgressLabel>
                    {billing.monthly_steps_used.toLocaleString()} of{" "}
                    {billing.monthly_step_limit.toLocaleString()} steps
                  </ProgressLabel>
                  <ProgressValue />
                </Progress>

                {billing.billing_period_end && (
                  <p className="text-xs text-muted-foreground">
                    Billing period ends{" "}
                    {new Date(billing.billing_period_end).toLocaleDateString()}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Change Plan</CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="flex flex-wrap gap-2">
                  {TIERS.filter((t) => t !== "free" && t !== billing.tier).map(
                    (tier) => (
                      <Button
                        key={tier}
                        variant="outline"
                        size="sm"
                        disabled={checkoutLoading !== null}
                        onClick={() => handleUpgrade(tier)}
                      >
                        {checkoutLoading === tier
                          ? "Redirecting..."
                          : `${TIER_LABELS[tier]}`}
                      </Button>
                    )
                  )}
                  {billing.tier !== "free" && (
                    <Button variant="ghost" size="sm" onClick={handlePortal}>
                      Manage Subscription
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          </>
        ) : null}
      </section>

      <Separator />

      {/* ── API Keys Section ── */}
      <section className="space-y-4">
        <h2 className="text-lg font-medium">API Keys</h2>

        {createdKey && (
          <div className="rounded-md border border-green-500/50 bg-green-500/10 p-4">
            <p className="mb-2 text-sm font-medium text-green-700 dark:text-green-400">
              New API key created. Copy it now — it won&apos;t be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-muted px-3 py-1.5 text-sm font-mono break-all">
                {createdKey}
              </code>
              <Button
                variant="outline"
                size="icon"
                onClick={() => copyToClipboard(createdKey)}
              >
                <Copy className="size-4" />
              </Button>
            </div>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Create New Key</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="flex gap-2">
              <Input
                placeholder="Label (optional)"
                value={newKeyLabel}
                onChange={(e) => setNewKeyLabel(e.target.value)}
                className="max-w-xs"
              />
              <Button
                size="sm"
                disabled={creating}
                onClick={handleCreateKey}
              >
                <Plus className="size-4" />
                {creating ? "Creating..." : "Create"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {keysLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : keysError ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
            {keysError}
          </div>
        ) : apiKeys.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No API keys yet. Create one above to get started.
          </p>
        ) : (
          <Card>
            <CardContent className="p-0">
              <div className="divide-y">
                {apiKeys.map((key) => {
                  const isRevoked = key.revoked_at !== null;
                  return (
                    <div
                      key={key.id}
                      className="flex items-center justify-between px-4 py-3"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <code className="text-sm font-mono">
                            {key.key_prefix}...{key.key_suffix}
                          </code>
                          {key.label && (
                            <span className="text-xs text-muted-foreground">
                              {key.label}
                            </span>
                          )}
                          {isRevoked && (
                            <Badge variant="destructive" className="text-xs">
                              Revoked
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          Created{" "}
                          {key.created_at
                            ? new Date(key.created_at).toLocaleDateString()
                            : "unknown"}
                        </p>
                      </div>
                      {!isRevoked && (
                        <Button
                          variant="destructive"
                          size="icon-sm"
                          onClick={() => handleRevokeKey(key.id)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}
