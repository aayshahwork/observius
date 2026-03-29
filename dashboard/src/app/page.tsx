"use client";

import { useEffect, useRef, useState } from "react";
import { Inter } from "next/font/google";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const inter = Inter({ subsets: ["latin"] });

// ── Types ─────────────────────────────────────────────────────────────────────

interface Task {
  id: number;
  ok: boolean;
  desc: string;
  domain: string;
  dur: string;
  steps: number;
}

interface Stat {
  label: string;
  value: string;
  sub: string;
  accent: boolean;
}

interface ChartPoint {
  day: string;
  rate: number;
}

// ── Default / seed data ───────────────────────────────────────────────────────

const DEFAULT_TASKS: Task[] = [
  { id: 1, ok: true,  desc: "Extract pricing plan names & features", domain: "vapi.ai",    dur: "34.2s", steps: 4 },
  { id: 2, ok: true,  desc: "List open engineering job titles",      domain: "vapi.ai",    dur: "28.7s", steps: 3 },
  { id: 3, ok: true,  desc: "Extract pricing plan names & features", domain: "retell.ai",  dur: "41.3s", steps: 5 },
  { id: 4, ok: true,  desc: "List open engineering job titles",      domain: "retell.ai",  dur: "22.1s", steps: 3 },
  { id: 5, ok: true,  desc: "Extract pricing plan names & features", domain: "bland.ai",   dur: "38.6s", steps: 4 },
  { id: 6, ok: false, desc: "List open engineering job titles",      domain: "bland.ai",   dur: "12.0s", steps: 2 },
];

const DEFAULT_STATS: Stat[] = [
  { label: "Tasks Run",    value: "–",    sub: "awaiting report", accent: false },
  { label: "Success Rate", value: "–",    sub: "awaiting report", accent: true  },
  { label: "Avg Duration", value: "–",    sub: "per task",        accent: false },
  { label: "Est. Cost",    value: "–",    sub: "this session",    accent: false },
];

const BASE_CHART: ChartPoint[] = [
  { day: "Mon", rate: 82 },
  { day: "Tue", rate: 85 },
  { day: "Wed", rate: 88 },
  { day: "Thu", rate: 86 },
  { day: "Fri", rate: 90 },
  { day: "Sat", rate: 93 },
];

const STEPS = [
  { num: 1, action: "Navigate", desc: "Loaded target URL"             },
  { num: 2, action: "Scroll",   desc: "Scanned page content"          },
  { num: 3, action: "Read",     desc: "Identified data elements"      },
  { num: 4, action: "Extract",  desc: "Pulled structured fields"      },
  { num: 5, action: "Validate", desc: "Checked schema conformance"    },
  { num: 6, action: "Done",     desc: "Returned validated JSON"       },
];

// ── Report → state mappers ────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function reportToTasks(report: any): Task[] {
  // demo.py format: { tasks: [{ id, url, description, success, steps, duration_ms }] }
  const raw = report.tasks ?? report.competitors;
  if (!raw?.length) return DEFAULT_TASKS;

  if (report.tasks) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return report.tasks.map((t: any, i: number) => {
      let domain = t.url ?? "";
      try { domain = new URL(t.url).hostname.replace(/^www\./, ""); } catch { /* keep raw */ }
      return {
        id:    i + 1,
        ok:    t.success ?? false,
        desc:  t.description ?? "Task",
        domain,
        dur:   `${((t.duration_ms ?? 0) / 1000).toFixed(1)}s`,
        steps: t.steps ?? 0,
      };
    });
  }

  // Legacy competitors format
  const rows: Task[] = [];
  let id = 1;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  for (const c of report.competitors) {
    let domain = c.competitor ?? "";
    try { domain = new URL(c.pricing_url).hostname.replace(/^www\./, ""); } catch { /* keep */ }
    rows.push({ id: id++, ok: (c.plans?.length ?? 0) > 0, desc: "Extract pricing plan names & features", domain, dur: `${(c.pricing_duration_ms / 1000).toFixed(1)}s`, steps: c.pricing_steps ?? 0 });
    rows.push({ id: id++, ok: (c.roles?.length ?? 0)  > 0, desc: "List open engineering job titles",     domain, dur: `${(c.jobs_duration_ms  / 1000).toFixed(1)}s`, steps: c.jobs_steps  ?? 0 });
  }
  return rows;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function reportToStats(report: any): Stat[] {
  const tasks = report.tasks ?? [];
  const total = tasks.length;
  if (!total) return DEFAULT_STATS;

  const succeeded  = tasks.filter((t: { success?: boolean }) => t.success).length;
  const rate       = Math.round((succeeded / total) * 100);
  const avgMs      = tasks.reduce((s: number, t: { duration_ms?: number }) => s + (t.duration_ms ?? 0), 0) / total;
  const totalSteps = tasks.reduce((s: number, t: { steps?: number }) => s + (t.steps ?? 0), 0);
  const estCost    = (totalSteps * 0.05).toFixed(2);

  return [
    { label: "Tasks Run",    value: String(total),              sub: "this session",  accent: false },
    { label: "Success Rate", value: `${rate}%`,                 sub: "this session",  accent: true  },
    { label: "Avg Duration", value: `${(avgMs / 1000).toFixed(1)}s`, sub: "per task", accent: false },
    { label: "Est. Cost",    value: `$${estCost}`,              sub: `${totalSteps} steps × $0.05`, accent: false },
  ];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function reportToChart(report: any): ChartPoint[] {
  const tasks   = report.tasks ?? [];
  const total   = tasks.length;
  const todayRate = total
    ? Math.round((tasks.filter((t: { success?: boolean }) => t.success).length / total) * 100)
    : 95;
  return [...BASE_CHART, { day: "Today", rate: todayRate }];
}

// ── Shared style tokens ───────────────────────────────────────────────────────

const glass = {
  background:           "rgba(255,255,255,0.03)",
  border:               "1px solid rgba(255,255,255,0.08)",
  backdropFilter:       "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
  borderRadius:         12,
} as const;

// ── ChartTooltip ──────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "rgba(8,8,18,0.96)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, padding: "8px 14px" }}>
      <div style={{ color: "#64748b", fontSize: 11, marginBottom: 4 }}>{label}</div>
      <div style={{ color: "#10b981", fontSize: 15, fontWeight: 700 }}>{payload[0].value}%</div>
    </div>
  );
}

// ── TaskRow ───────────────────────────────────────────────────────────────────

function TaskRow({ task }: { task: Task }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "7px 10px", borderRadius: 8,
        background: "rgba(255,255,255,0.02)",
        border: `1px solid ${hovered ? "rgba(255,255,255,0.15)" : "rgba(255,255,255,0.05)"}`,
        transform: hovered ? "translateY(-1px)" : "translateY(0)",
        transition: "transform 0.15s ease, border-color 0.15s ease",
        cursor: "default",
      }}
    >
      {/* Status indicator */}
      <div style={{
        fontSize: 13, flexShrink: 0, lineHeight: 1,
        color: task.ok ? "#10b981" : "#ef4444",
        filter: `drop-shadow(0 0 4px ${task.ok ? "rgba(16,185,129,0.7)" : "rgba(239,68,68,0.7)"})`,
      }}>
        {task.ok ? "✓" : "✗"}
      </div>

      {/* Description + domain */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "#e2e8f0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {task.desc}
        </div>
        <div style={{ fontSize: 11, color: "#475569", marginTop: 1 }}>{task.domain}</div>
      </div>

      {/* Steps + duration */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2, flexShrink: 0 }}>
        <div style={{ fontSize: 11, color: task.ok ? "#475569" : "#ef4444", fontFamily: "monospace" }}>
          {task.dur}
        </div>
        <div style={{ fontSize: 10, color: "#334155", fontFamily: "monospace" }}>
          {task.steps} step{task.steps !== 1 ? "s" : ""}
        </div>
      </div>
    </div>
  );
}

// ── StepCard ──────────────────────────────────────────────────────────────────

function StepCard({ step }: { step: typeof STEPS[0] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
      <div style={{
        width: 28, height: 28, borderRadius: "50%",
        background: "rgba(99,102,241,0.14)", border: "1px solid rgba(99,102,241,0.45)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 11, fontWeight: 700, color: "#818cf8", flexShrink: 0, position: "relative", zIndex: 1,
      }}>
        {step.num}
      </div>
      <div style={{
        width: "100%", height: 58, background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.07)", borderRadius: 6,
        display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
      }}>
        <div style={{ width: "55%", height: 3, background: "rgba(255,255,255,0.07)", borderRadius: 2 }} />
        <div style={{ width: "15%", height: 3, background: "rgba(255,255,255,0.04)", borderRadius: 2 }} />
      </div>
      <div style={{ textAlign: "center", width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 5, marginBottom: 4 }}>
          <svg width="13" height="13" viewBox="0 0 13 13" style={{ flexShrink: 0 }}>
            <circle cx="6.5" cy="6.5" r="6.5" fill="rgba(16,185,129,0.15)" />
            <polyline points="3.5,6.5 5.5,8.5 9.5,4" stroke="#10b981" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span style={{ fontSize: 11, fontWeight: 600, color: "#e2e8f0" }}>{step.action}</span>
        </div>
        <div style={{ fontSize: 10, color: "#475569", lineHeight: 1.5 }}>{step.desc}</div>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [tasks,         setTasks]     = useState<Task[]>(DEFAULT_TASKS);
  const [stats,         setStats]     = useState<Stat[]>(DEFAULT_STATS);
  const [chartData,     setChartData] = useState<ChartPoint[]>([...BASE_CHART, { day: "Today", rate: 95 }]);
  const [reportLoading, setLoading]   = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const res  = await fetch("/api/report");
        const json = await res.json();
        if (json.status === "complete") {
          setTasks(reportToTasks(json.data));
          setStats(reportToStats(json.data));
          setChartData(reportToChart(json.data));
          setLoading(false);
          if (intervalRef.current) clearInterval(intervalRef.current);
        }
      } catch { /* network hiccup — keep polling */ }
    };
    poll();
    intervalRef.current = setInterval(poll, 3000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, []);

  return (
    <div className={inter.className} style={{ minHeight: "100vh", background: "linear-gradient(160deg, #0a0a0f 0%, #0d0d18 60%, #0f0f1a 100%)", color: "#f8fafc", paddingBottom: 52 }}>

      {/* ── Top Nav ──────────────────────────────────────────────────────────── */}
      <nav style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 32px", height: 56,
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(10,10,15,0.75)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
        position: "sticky", top: 0, zIndex: 50,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%", background: "#6366f1", boxShadow: "0 0 10px rgba(99,102,241,0.9), 0 0 4px rgba(99,102,241,0.6)" }} />
          <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: "0.1em", color: "#fff" }}>OBSERVIUS</span>
        </div>
        <div style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 20, padding: "5px 14px", fontSize: 12, fontFamily: "monospace", color: "#64748b" }}>
          API Key:&nbsp;<span style={{ color: "#cbd5e1" }}>cu_sk_***4f8a</span>
        </div>
      </nav>

      <div style={{ padding: "28px 32px 0" }}>

        {/* ── Stats Row ────────────────────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 20 }}>
          {stats.map((s) => (
            <div key={s.label} style={{ ...glass, padding: "20px 24px" }}>
              <div style={{ fontSize: 11, color: "#475569", marginBottom: 10, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                {s.label}
              </div>
              <div style={{ fontSize: 30, fontWeight: 700, color: s.accent ? "#10b981" : "#f1f5f9", lineHeight: 1, marginBottom: 6 }}>
                {s.value}
              </div>
              <div style={{ fontSize: 11, color: "#334155" }}>{s.sub}</div>
            </div>
          ))}
        </div>

        {/* ── Main Grid ────────────────────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "60fr 40fr", gap: 16, marginBottom: 20 }}>

          {/* Chart */}
          <div style={{ ...glass, padding: "24px 24px 16px" }}>
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>Success Rate — Last 7 Days</div>
              <div style={{ fontSize: 11, color: "#475569", marginTop: 3 }}>Daily task completion rate</div>
            </div>
            <ResponsiveContainer width="100%" height={208}>
              <LineChart data={chartData} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
                <XAxis dataKey="day" axisLine={false} tickLine={false} tick={{ fill: "#475569", fontSize: 11 }} />
                <YAxis domain={[75, 100]} axisLine={false} tickLine={false} tick={{ fill: "#475569", fontSize: 11 }} tickFormatter={(v: number) => `${v}%`} />
                <Tooltip content={(props) => <ChartTooltip {...props} />} cursor={{ stroke: "rgba(255,255,255,0.04)", strokeWidth: 1 }} />
                <Line type="monotone" dataKey="rate" stroke="#10b981" strokeWidth={2.5} dot={false} activeDot={{ r: 5, fill: "#10b981", strokeWidth: 0 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Recent Tasks */}
          <div style={{ ...glass, padding: "24px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>Recent Tasks</div>
              {reportLoading && (
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 12, height: 12, borderRadius: "50%", border: "2px solid rgba(99,102,241,0.3)", borderTopColor: "#6366f1", animation: "spin 0.8s linear infinite" }} />
                  <span style={{ fontSize: 10, color: "#475569" }}>polling…</span>
                </div>
              )}
            </div>
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {tasks.map((t) => <TaskRow key={t.id} task={t} />)}
            </div>
          </div>
        </div>

        {/* ── Replay Viewer ────────────────────────────────────────────────────── */}
        <div style={{ ...glass, padding: "24px" }}>
          <div style={{ marginBottom: 24 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>Task Replay</div>
            <div style={{ fontSize: 11, color: "#475569", marginTop: 4 }}>
              {tasks[0]?.desc ?? "No task"} — {tasks[0]?.domain ?? ""}
            </div>
          </div>

          {/* Step timeline */}
          <div style={{ position: "relative", marginBottom: 24 }}>
            <div style={{ position: "absolute", top: 13, left: "8.33%", right: "8.33%", height: 2, background: "linear-gradient(90deg, #6366f1 0%, #8b5cf6 50%, #6366f1 100%)", opacity: 0.7, zIndex: 0 }} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, position: "relative", zIndex: 1 }}>
              {STEPS.map((s) => <StepCard key={s.num} step={s} />)}
            </div>
          </div>

          {/* JSON result */}
          <div style={{ background: "rgba(0,0,0,0.35)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, padding: "16px 20px" }}>
            <div style={{ fontSize: 10, color: "#6366f1", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 12 }}>Output</div>
            <pre style={{ margin: 0, fontSize: 12.5, lineHeight: 1.75, fontFamily: "monospace", overflow: "auto" }}>
              <span style={{ color: "#94a3b8" }}>{"{"}</span>{"\n"}
              {"  "}<span style={{ color: "#93c5fd" }}>&quot;items&quot;</span><span style={{ color: "#64748b" }}>: </span>
              <span style={{ color: "#86efac" }}>{JSON.stringify((tasks[0] as unknown as { result?: { items?: string[] } })?.result?.items?.slice(0, 3) ?? ["–", "–", "–"])}</span>
              <span style={{ color: "#64748b" }}>,</span>{"\n"}
              {"  "}<span style={{ color: "#93c5fd" }}>&quot;details&quot;</span><span style={{ color: "#64748b" }}>: </span>
              <span style={{ color: "#86efac" }}>{JSON.stringify((tasks[0] as unknown as { result?: { details?: string[] } })?.result?.details?.slice(0, 3) ?? ["–", "–", "–"])}</span>{"\n"}
              <span style={{ color: "#94a3b8" }}>{"}"}</span>
            </pre>
          </div>
        </div>

      </div>
    </div>
  );
}
