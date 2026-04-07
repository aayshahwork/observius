"use client";

import { useState, useEffect } from "react";
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
import { KeyRound } from "lucide-react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { apiKey, isLoading, login } = useAuth();
  const router = useRouter();

  // Redirect already-authenticated users
  useEffect(() => {
    if (!isLoading && apiKey) router.replace("/tasks");
  }, [apiKey, isLoading, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      const data = await res.json();

      if (!res.ok) {
        const msg = data?.detail?.message || data?.detail || "Login failed";
        setError(typeof msg === "string" ? msg : "Invalid email or password.");
        return;
      }

      login(data.api_key);
    } catch {
      setError("Could not connect to the API. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="text-center">
        <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/10">
          <KeyRound className="size-5 text-primary" />
        </div>
        <CardTitle>Welcome back</CardTitle>
        <CardDescription>
          Sign in to your Pokant account
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
              placeholder="Your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button
            type="submit"
            className="w-full"
            disabled={!email.trim() || !password || loading}
          >
            {loading ? "Signing in..." : "Sign In"}
          </Button>
        </form>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          Need an account?{" "}
          <Link
            href="/signup"
            className="text-primary underline underline-offset-4 hover:text-primary/80"
          >
            Sign up free
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
