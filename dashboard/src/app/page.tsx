import Link from "next/link";

const tiers = [
  {
    name: "Free",
    price: "$0",
    period: "forever",
    steps: "500",
    features: ["1 concurrent task", "50 steps per task", "Community support"],
    cta: "Get Started Free",
    ctaHref: "/signup",
    highlight: false,
  },
  {
    name: "Startup",
    price: "$29",
    period: "/month",
    steps: "5,000",
    features: [
      "5 concurrent tasks",
      "200 steps per task",
      "5 min timeout",
      "Email support",
    ],
    cta: "Get Started",
    ctaHref: "/signup",
    highlight: true,
  },
  {
    name: "Growth",
    price: "$99",
    period: "/month",
    steps: "25,000",
    features: [
      "10 concurrent tasks",
      "350 steps per task",
      "7.5 min timeout",
      "Priority support",
    ],
    cta: "Get Started",
    ctaHref: "/signup",
    highlight: false,
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "",
    steps: "100,000+",
    features: [
      "20+ concurrent tasks",
      "500 steps per task",
      "10 min timeout",
      "Dedicated support",
      "SLA guarantee",
    ],
    cta: "Contact Us",
    ctaHref: "/contact",
    highlight: false,
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <nav className="border-b border-border/50">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
          <Link
            href="/"
            className="text-sm font-semibold tracking-tight hover:opacity-80 transition-opacity"
          >
            Pokant
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Log in
            </Link>
            <Link
              href="/signup"
              className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Sign up
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-6 pt-24 pb-20">
        <div className="max-w-2xl">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            One API to automate any browser workflow
          </h1>
          <p className="mt-4 text-lg text-muted-foreground leading-relaxed">
            Give it a URL, a task in plain English, and a schema. Get back
            structured JSON. No Playwright scripts. No selectors. No
            maintenance.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/signup"
              className="rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Get Started Free
            </Link>
            <a
              href="https://github.com/aayshahwork/pokant"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-border bg-card px-5 py-2.5 text-sm font-medium hover:bg-muted transition-colors"
            >
              View on GitHub
            </a>
          </div>
        </div>

        {/* Code example */}
        <div className="mt-14 rounded-xl border border-border bg-card overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2">
            <span className="size-3 rounded-full bg-destructive/40" />
            <span className="size-3 rounded-full bg-warning/40" />
            <span className="size-3 rounded-full bg-success/40" />
            <span className="ml-2 text-xs text-muted-foreground">
              example.py
            </span>
          </div>
          <pre className="overflow-x-auto p-5 text-sm leading-relaxed font-mono">
            <code>
              <span className="text-muted-foreground">
                {
                  "from computeruse import ComputerUse\n\ncu = ComputerUse()\n\n"
                }
              </span>
              <span>{"result = cu.run_task(\n"}</span>
              <span>
                {'    url="https://news.ycombinator.com",\n'}
              </span>
              <span>
                {'    task="Get the top 5 posts with title, points, and link",\n'}
              </span>
              <span>
                {
                  '    output_schema={"posts": [{"title": "str", "points": "int", "link": "str"}]}\n'
                }
              </span>
              <span>{")\n\n"}</span>
              <span className="text-muted-foreground">{"# => "}</span>
              <span className="text-success">
                {'{"posts": [{"title": "Show HN: ...", "points": 342, "link": "..."}]}'}
              </span>
            </code>
          </pre>
        </div>
      </section>

      {/* How it works */}
      <section className="border-t border-border/50 bg-muted/30">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="text-center text-2xl font-bold tracking-tight">
            How it works
          </h2>
          <div className="mt-12 grid gap-8 sm:grid-cols-3">
            {[
              {
                step: "1",
                title: "Describe your task",
                desc: "Pass a URL, a plain-English instruction, and an optional output schema. No selectors or scripts needed.",
              },
              {
                step: "2",
                title: "Agent executes",
                desc: "An AI agent launches a real browser, navigates the page, fills forms, clicks buttons, and extracts data.",
              },
              {
                step: "3",
                title: "Get structured data",
                desc: "Receive clean JSON matching your schema. Every run is recorded with step-by-step screenshots.",
              },
            ].map((item) => (
              <div key={item.step} className="space-y-3">
                <div className="flex size-9 items-center justify-center rounded-lg bg-brand/10 text-sm font-bold text-brand">
                  {item.step}
                </div>
                <h3 className="font-semibold">{item.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {item.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section className="border-t border-border/50">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 className="text-center text-2xl font-bold tracking-tight">
            Simple, step-based pricing
          </h2>
          <p className="mt-2 text-center text-sm text-muted-foreground">
            One step = one browser action. Pay only for what you use.
          </p>
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {tiers.map((tier) => (
              <div
                key={tier.name}
                className={`flex flex-col rounded-xl border p-5 ${
                  tier.highlight
                    ? "border-brand ring-1 ring-brand/20"
                    : "border-border"
                }`}
              >
                {tier.highlight && (
                  <span className="mb-3 w-fit rounded-full bg-brand/10 px-2.5 py-0.5 text-xs font-medium text-brand">
                    Popular
                  </span>
                )}
                <h3 className="font-semibold">{tier.name}</h3>
                <div className="mt-2">
                  <span className="text-3xl font-bold">{tier.price}</span>
                  <span className="text-sm text-muted-foreground">
                    {tier.period}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {tier.steps} steps/month
                </p>
                <ul className="mt-4 flex-1 space-y-2">
                  {tier.features.map((f) => (
                    <li
                      key={f}
                      className="flex items-start gap-2 text-sm text-muted-foreground"
                    >
                      <span className="mt-0.5 text-success">&#10003;</span>
                      {f}
                    </li>
                  ))}
                </ul>
                <Link
                  href={tier.ctaHref}
                  className={`mt-5 block rounded-lg py-2 text-center text-sm font-medium transition-colors ${
                    tier.highlight
                      ? "bg-brand text-brand-foreground hover:bg-brand/90"
                      : "border border-border bg-card hover:bg-muted"
                  }`}
                >
                  {tier.cta}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border/50">
        <div className="mx-auto max-w-5xl px-6 py-8">
          <p className="text-center text-sm text-muted-foreground">
            Built by Aayush &amp; Avi. Open source on{" "}
            <a
              href="https://github.com/aayshahwork/pokant"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-4 hover:text-foreground"
            >
              GitHub
            </a>
            .
          </p>
        </div>
      </footer>
    </div>
  );
}
