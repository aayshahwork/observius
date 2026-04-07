"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/contexts/auth-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Rocket, Copy, Check, ArrowRight } from "lucide-react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    account_id: string;
    api_key: string;
    tier: string;
    monthly_step_limit: number;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      const data = await res.json();

      if (!res.ok) {
        const msg =
          data?.detail?.message || data?.detail || "Registration failed";
        setError(typeof msg === "string" ? msg : JSON.stringify(msg));
        return;
      }

      setResult(data);
    } catch {
      setError("Could not connect to the API. Is it running?");
    } finally {
      setLoading(false);
    }
  }

  function handleCopy() {
    if (!result) return;
    navigator.clipboard.writeText(result.api_key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleContinue() {
    if (!result) return;
    login(result.api_key);
    router.replace("/tasks");
  }

  // --- Success state: show the API key ---
  if (result) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-success/10">
            <Check className="size-5 text-success" />
          </div>
          <CardTitle>You&apos;re in</CardTitle>
          <CardDescription>
            Free tier &middot; {result.monthly_step_limit.toLocaleString()}{" "}
            steps/month
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* API key display */}
          <div className="space-y-2">
            <Label>Your API Key</Label>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded-lg border border-input bg-muted/50 px-3 py-2 font-mono text-xs break-all">
                {result.api_key}
              </code>
              <Button
                variant="outline"
                size="icon"
                onClick={handleCopy}
                aria-label="Copy API key"
              >
                {copied ? (
                  <Check className="size-4 text-success" />
                ) : (
                  <Copy className="size-4" />
                )}
              </Button>
            </div>
            <p className="text-xs text-warning font-medium">
              Save this key — you won&apos;t see it again. Use it with the SDK or retrieve a new one by logging in.
            </p>
          </div>

          {/* Quick start */}
          <div className="space-y-2">
            <Label>Quick Start</Label>
            <div className="rounded-lg border border-input bg-muted/30 p-3 font-mono text-xs leading-relaxed">
              <div className="text-muted-foreground">$ pip install computeruse</div>
              <div className="mt-3 text-muted-foreground"># run your first task</div>
              <div>
                <span className="text-brand">from</span> computeruse{" "}
                <span className="text-brand">import</span> ComputerUse
              </div>
              <div>
                cu = ComputerUse(api_key=
                <span className="text-success">
                  &quot;{result.api_key}&quot;
                </span>
                )
              </div>
              <div>
                result = cu.run_task(
              </div>
              <div className="pl-4">
                url=<span className="text-success">&quot;https://news.ycombinator.com&quot;</span>,
              </div>
              <div className="pl-4">
                task=<span className="text-success">&quot;Get the top 5 posts&quot;</span>,
              </div>
              <div>)</div>
            </div>
          </div>

          {/* Continue button */}
          <Button className="w-full" onClick={handleContinue}>
            Continue to Dashboard
            <ArrowRight className="ml-1 size-4" data-icon="inline-end" />
          </Button>

          <p className="text-center text-xs text-muted-foreground">
            Already have an account?{" "}
            <Link
              href="/login"
              className="text-primary underline underline-offset-4 hover:text-primary/80"
            >
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    );
  }

  // --- Default state: registration form ---
  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="text-center">
        <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-brand/10">
          <Rocket className="size-5 text-brand" />
        </div>
        <CardTitle>Get Started Free</CardTitle>
        <CardDescription>
          500 steps/month. No credit card required.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="dev@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confirm-password">Confirm Password</Label>
            <Input
              id="confirm-password"
              type="password"
              placeholder="Repeat your password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={!email.trim() || !password || !confirmPassword || loading}
          >
            {loading ? "Creating account..." : "Get Started Free"}
          </Button>
        </form>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          Already have an account?{" "}
          <Link
            href="/login"
            className="text-primary underline underline-offset-4 hover:text-primary/80"
          >
            Log in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
