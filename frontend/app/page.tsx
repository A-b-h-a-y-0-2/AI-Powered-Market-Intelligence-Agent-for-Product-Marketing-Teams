"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type HealthStatus, type PipelineStatus } from "@/lib/api";

const CARDS = [
  {
    href: "/dashboard",
    icon: "◈",
    title: "Threat Scoring",
    desc: "Competitor danger level scored 0–100, updated every Sunday",
    color: "#f85149",
  },
  {
    href: "/chat",
    icon: "◉",
    title: "Intelligence Chat",
    desc: "Ask anything. Answers grounded in the knowledge base with source citations",
    color: "#6366f1",
  },
  {
    href: "/events",
    icon: "◎",
    title: "Event Timeline",
    desc: "Every competitor move: launches, pricing, funding, hiring — timestamped and sourced",
    color: "#22c55e",
  },
  {
    href: "/matrix",
    icon: "⊞",
    title: "Feature Matrix",
    desc: "Living comparison of competitor capabilities, updated within 15 min of new events",
    color: "#06b6d4",
  },
  {
    href: "/narratives",
    icon: "◑",
    title: "Strategic Narratives",
    desc: "Clusters of events synthesised into strategic stories",
    color: "#d29922",
  },
  {
    href: "/admin",
    icon: "⚑",
    title: "Quarantine Review",
    desc: "Human review queue for low-confidence extractions that feed DSPy optimization",
    color: "#7d8590",
  },
];

const FLOW_STEPS = [
  { label: "Sources", detail: "RSS / Firecrawl / Tavily / Apify" },
  { label: "Research Agent", detail: "Daily 02:00" },
  { label: "Extraction Agent", detail: "3-pass: filter → extract → judge" },
  { label: "Storage", detail: "MongoDB + pgvector" },
  { label: "Synthesis", detail: "Weekly: Narrative, Convergence, Threat" },
  { label: "Conversational Agent", detail: "On-demand" },
];

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-center px-6 py-3 rounded-xl"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <span className="text-xl font-bold" style={{ color: "var(--text)" }}>{value}</span>
      <span className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{label}</span>
    </div>
  );
}

export default function HomePage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => null);
    api.pipelineStatus().then(setPipeline).catch(() => null);
  }, []);

  const lastRun = pipeline?.last_research_run
    ? new Date(pipeline.last_research_run).toLocaleString("en-US", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "--";

  const eventsToday = pipeline?.events_ingested_today ?? "--";
  const eventsTotal = pipeline?.events_ingested_total ?? "--";
  const apiStatus = health?.status === "ok" ? "Live" : health ? health.status : "--";

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="mb-10">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-3xl font-bold" style={{ color: "var(--text)" }}>
            Intel Agent
          </h1>
          <span className="text-xs px-2 py-0.5 rounded-full font-medium"
            style={{ background: "#3fb95022", color: "var(--threat-low)", border: "1px solid #3fb95044" }}>
            {apiStatus}
          </span>
        </div>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          AI-powered market intelligence for professional services — McKinsey, BCG, Bain, Deloitte, KPMG, Oliver Wyman, Accenture Strategy
        </p>
      </div>

      <div className="flex flex-wrap gap-4 mb-10">
        <StatPill label="Events today" value={String(eventsToday)} />
        <StatPill label="Total events" value={String(eventsTotal)} />
        <StatPill label="Companies tracked" value="7" />
        <StatPill label="Last research run" value={lastRun} />
      </div>

      <h2 className="text-xs font-semibold tracking-widest uppercase mb-4"
        style={{ color: "var(--text-muted)" }}>Capabilities</h2>

      <div className="grid grid-cols-1 gap-4 mb-12 sm:grid-cols-2 lg:grid-cols-3">
        {CARDS.map(({ href, icon, title, desc, color }) => (
          <Link key={href} href={href}
            className="group rounded-xl p-5 transition-all hover:scale-[1.02]"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-9 h-9 rounded-lg flex items-center justify-center text-lg flex-shrink-0"
                style={{ background: `${color}18`, color }}>
                {icon}
              </div>
              <span className="font-semibold text-sm" style={{ color: "var(--text)" }}>{title}</span>
            </div>
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>{desc}</p>
            <div className="mt-4 text-xs font-medium transition-colors"
              style={{ color }}>
              Open →
            </div>
          </Link>
        ))}
      </div>

      <h2 className="text-xs font-semibold tracking-widest uppercase mb-4"
        style={{ color: "var(--text-muted)" }}>How it works</h2>

      <div className="rounded-xl p-6" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="flex flex-wrap items-center gap-0">
          {FLOW_STEPS.map((step, i) => (
            <div key={i} className="flex items-center">
              <div className="flex flex-col items-center text-center px-3 py-2">
                <div className="text-xs font-semibold" style={{ color: "var(--text)" }}>{step.label}</div>
                <div className="text-xs mt-0.5 max-w-[120px]" style={{ color: "var(--text-muted)" }}>{step.detail}</div>
              </div>
              {i < FLOW_STEPS.length - 1 && (
                <div className="text-base flex-shrink-0" style={{ color: "var(--border)" }}>→</div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
