"use client";

import { useState } from "react";
import Link from "next/link";
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
import { Check, Mail } from "lucide-react";

// Replace with your Formspree form ID after signing up at https://formspree.io
const FORMSPREE_ENDPOINT = "https://formspree.io/f/xbdpwokl";

interface FormState {
  name: string;
  email: string;
  company: string;
  message: string;
}

export default function ContactPage() {
  const [form, setForm] = useState<FormState>({
    name: "",
    email: "",
    company: "",
    message: "",
  });
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  function handleChange(
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.email.trim() || !form.message.trim()) return;

    setLoading(true);
    setError("");

    try {
      const res = await fetch(FORMSPREE_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(form),
      });

      if (!res.ok) {
        setError("Something went wrong. Please try again or email us directly.");
        return;
      }

      setSubmitted(true);
    } catch {
      setError("Could not send message. Please email avidesai0110@gmail.com directly.");
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-background via-background to-brand/5">
        <div className="mb-6 text-center">
          <h1 className="text-lg font-semibold tracking-tight">
            <Link href="/" className="hover:opacity-80 transition-opacity">
              Pokant
            </Link>
          </h1>
        </div>
        <Card className="w-full max-w-sm">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-success/10">
              <Check className="size-5 text-success" />
            </div>
            <CardTitle>Message sent</CardTitle>
            <CardDescription>
              We&apos;ll be in touch shortly.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/">
              <Button variant="outline" className="w-full">
                Back to home
              </Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-background via-background to-brand/5">
      <div className="mb-6 text-center">
        <h1 className="text-lg font-semibold tracking-tight">
          <Link href="/" className="hover:opacity-80 transition-opacity">
            Pokant
          </Link>
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Managed browser automation, powered by AI
        </p>
      </div>

      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-brand/10">
            <Mail className="size-5 text-brand" />
          </div>
          <CardTitle>Contact Enterprise Sales</CardTitle>
          <CardDescription>
            Tell us about your use case and we&apos;ll get back to you within one business day.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  name="name"
                  placeholder="Jane Smith"
                  value={form.name}
                  onChange={handleChange}
                  autoFocus
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="company">Company</Label>
                <Input
                  id="company"
                  name="company"
                  placeholder="Acme Inc."
                  value={form.company}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="email">Work Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                placeholder="jane@acme.com"
                value={form.email}
                onChange={handleChange}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="message">How can we help?</Label>
              <textarea
                id="message"
                name="message"
                rows={4}
                placeholder="Describe your use case, expected task volume, and any specific requirements..."
                value={form.message}
                onChange={handleChange}
                required
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-none"
              />
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <Button
              type="submit"
              className="w-full"
              disabled={
                !form.name.trim() ||
                !form.email.trim() ||
                !form.message.trim() ||
                loading
              }
            >
              {loading ? "Sending..." : "Send Message"}
            </Button>
          </form>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            Need a regular account?{" "}
            <Link
              href="/signup"
              className="text-primary underline underline-offset-4 hover:text-primary/80"
            >
              Sign up free
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
