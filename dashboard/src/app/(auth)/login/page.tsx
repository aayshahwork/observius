"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
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

export default function LoginPage() {
  const [key, setKey] = useState("");
  const { apiKey, login } = useAuth();
  const router = useRouter();

  // Navigate after apiKey state is committed
  useEffect(() => {
    if (apiKey) router.replace("/tasks");
  }, [apiKey, router]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    login(key.trim());
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="text-center">
        <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/10">
          <KeyRound className="size-5 text-primary" />
        </div>
        <CardTitle>Pokant</CardTitle>
        <CardDescription>
          Enter your API key to access the dashboard
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="api-key">API Key</Label>
            <Input
              id="api-key"
              type="password"
              placeholder="cu_test_..."
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoFocus
            />
          </div>
          <Button type="submit" className="w-full" disabled={!key.trim()}>
            Continue
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
