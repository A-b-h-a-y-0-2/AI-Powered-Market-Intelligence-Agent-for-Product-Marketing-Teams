"use client";

import { useEffect, useState } from "react";
import { api, type PipelineStatus } from "@/lib/api";

type AgentRow = {
  name: string;
  schedule: string;
  lastRunKey: keyof PipelineStatus | null;
};

const AGENTS: AgentRow[] = [
  { name: "Research Agent", schedule: "Daily 02:00", lastRunKey: "last_research_run" },
  { name: "Extraction Agent", schedule: "Daily 04:00", lastRunKey: "last_extraction_run" },
  { name: "Sentiment Agent", schedule: "Daily 05:00", lastRunKey: "last_sentiment_run" },
  { name: "Matrix Agent", schedule: "Event-triggered (≤15 min)", lastRunKey: null },
  { name: "Hiring Signal Agent", schedule: "Sunday 03:00", lastRunKey: null },
  { name: "Narrative Agent", schedule: "Sunday 05:00", lastRunKey: "last_narrative_run" },
  { name: "Convergence Agent", schedule: "Sunday 06:00", lastRunKey: null },
  { name: "Threat Scoring Agent", schedule: "Sunday 07:00", lastRunKey: "last_threat_run" },
  { name: "Digest Agent", schedule: "Sunday 08:00", lastRunKey: null },
];

const ARCH_LAYERS = [
  { label: "Layer 1", detail: "Source ingestion (RSS, Firecrawl, Tavily, Apify)" },
  { label: "Layer 2", detail: "Research Agent — raw document store (MongoDB)" },
  { label: "Layer 3", detail: "Extraction Agent — 3-pass DSPy pipeline" },
  { label: "Layer 4", detail: "Event store + pgvector embeddings" },
  { label: "Layer 5", detail: "Synthesis agents (Narrative, Convergence, Threat)" },
  { label: "Layer 6", detail: "Knowledge graph + vector index" },
  { label: "Layer 7", detail: "Conversational Agent (RAG on demand)" },
];

const HEALTH_COLORS: Record<string, string> = {
  healthy: "var(--threat-low)",
  degraded: "var(--threat-med)",
  down: "var(--threat-high)",
};

const HEALTH_BG: Record<string, string> = {
  healthy: "#3fb95018",
  degraded: "#d2992218",
  down: "#f8514918",
};

function fmtDate(val: string | null): string {
  if (!val) return "--";
  try {
    return new Date(val).toLocaleString("en-US", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return "--";
  }
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className="inline-block w-2 h-2 rounded-full mr-2 flex-shrink-0"
      style={{ background: ok ? "var(--threat-low)" : "var(--border)" }} />
  );
}

export default function PipelinePage() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.pipelineStatus()
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setLoading(false));
  }, []);

  const health = status?.pipeline_health ?? null;
  const healthLabel = health ? health.charAt(0).toUpperCase() + health.slice(1) : "Unknown";
  const healthColor = health ? HEALTH_COLORS[health] : "var(--text-muted)";
  const healthBg = health ? HEALTH_BG[health] : "var(--surface-2)";

  return (
    <div className="p-8 max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Pipeline Status</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Agent schedule and last-run times for all background workers
        </p>
      </div>

      <div className="flex flex-wrap gap-4 mb-8">
        <div className="flex items-center gap-3 px-5 py-3 rounded-xl"
          style={{ background: healthBg, border: `1px solid ${healthColor}44` }}>
          <span className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ background: healthColor }} />
          <div>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>System health</div>
            <div className="text-sm font-semibold" style={{ color: healthColor }}>
              {loading ? "..." : healthLabel}
            </div>
          </div>
        </div>

        <div className="px-5 py-3 rounded-xl"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>Events today</div>
          <div className="text-sm font-semibold" style={{ color: "var(--text)" }}>
            {loading ? "..." : (status?.events_ingested_today ?? "--")}
          </div>
        </div>

        <div className="px-5 py-3 rounded-xl"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>Total events</div>
          <div className="text-sm font-semibold" style={{ color: "var(--text)" }}>
            {loading ? "..." : (status?.events_ingested_total ?? "--")}
          </div>
        </div>

        {status?.next_scheduled_run && (
          <div className="px-5 py-3 rounded-xl"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>Next scheduled run</div>
            <div className="text-sm font-semibold" style={{ color: "var(--text)" }}>
              {fmtDate(status.next_scheduled_run)}
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl overflow-hidden mb-10"
        style={{ border: "1px solid var(--border)" }}>
        <div className="px-5 py-3" style={{ background: "var(--surface-2)" }}>
          <span className="text-xs font-semibold uppercase tracking-wider"
            style={{ color: "var(--text-muted)" }}>Agent Schedule</span>
        </div>
        <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th className="text-left px-5 py-3 text-xs font-medium"
                style={{ color: "var(--text-muted)", background: "var(--surface)" }}>Agent</th>
              <th className="text-left px-5 py-3 text-xs font-medium"
                style={{ color: "var(--text-muted)", background: "var(--surface)" }}>Schedule</th>
              <th className="text-left px-5 py-3 text-xs font-medium"
                style={{ color: "var(--text-muted)", background: "var(--surface)" }}>Last Run</th>
              <th className="text-left px-5 py-3 text-xs font-medium"
                style={{ color: "var(--text-muted)", background: "var(--surface)" }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {AGENTS.map((agent, i) => {
              const lastRunVal = agent.lastRunKey ? (status?.[agent.lastRunKey] as string | null | undefined) ?? null : null;
              const hasRun = !!lastRunVal;
              return (
                <tr key={agent.name}
                  style={{
                    background: i % 2 === 0 ? "var(--surface)" : "var(--surface-2)",
                    borderBottom: "1px solid var(--border)",
                  }}>
                  <td className="px-5 py-3 font-medium" style={{ color: "var(--text)" }}>
                    {agent.name}
                  </td>
                  <td className="px-5 py-3" style={{ color: "var(--text-muted)" }}>
                    {agent.schedule}
                  </td>
                  <td className="px-5 py-3" style={{ color: "var(--text-muted)" }}>
                    {loading ? "..." : (agent.lastRunKey ? fmtDate(lastRunVal) : "--")}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center">
                      <StatusDot ok={hasRun} />
                      <span className="text-xs" style={{ color: hasRun ? "var(--threat-low)" : "var(--text-muted)" }}>
                        {loading ? "..." : hasRun ? "Completed" : "Pending"}
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h2 className="text-xs font-semibold tracking-widest uppercase mb-4"
        style={{ color: "var(--text-muted)" }}>7-Layer Architecture</h2>

      <div className="space-y-1.5">
        {ARCH_LAYERS.map((layer, i) => (
          <div key={i} className="flex items-center gap-4 rounded-lg px-4 py-3"
            style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <div className="w-16 flex-shrink-0 text-xs font-semibold" style={{ color: "var(--accent)" }}>
              {layer.label}
            </div>
            <div className="text-sm" style={{ color: "var(--text-muted)" }}>
              {layer.detail}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
