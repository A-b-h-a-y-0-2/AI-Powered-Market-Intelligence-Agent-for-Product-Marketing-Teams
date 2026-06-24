"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { api, type MarketEvent } from "@/lib/api";

const EVENT_TYPES = [
  "all", "feature_launch", "pricing_change", "acquisition",
  "funding", "partnership", "product_update", "hiring_trend", "market_trend",
];

const EVENT_COLORS: Record<string, string> = {
  feature_launch: "#6366f1",
  pricing_change: "#f59e0b",
  acquisition: "#ef4444",
  funding: "#22c55e",
  partnership: "#06b6d4",
  product_update: "#8b5cf6",
  hiring_trend: "#ec4899",
  market_trend: "#64748b",
  customer_sentiment: "#84cc16",
};

const COMPANIES = [
  { label: "McKinsey", full: "McKinsey & Company" },
  { label: "BCG", full: "Boston Consulting Group" },
  { label: "Bain", full: "Bain & Company" },
  { label: "Deloitte", full: "Deloitte" },
];

function ConfBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 70 ? "var(--threat-low)" : pct >= 40 ? "var(--threat-med)" : "var(--threat-high)";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>{pct}%</span>
    </div>
  );
}

function EventCard({ event }: { event: MarketEvent }) {
  const color = EVENT_COLORS[event.event_type] ?? "#64748b";
  const date = new Date(event.timestamp).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  });

  return (
    <div className="rounded-xl p-4 flex gap-4"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      {/* Timeline dot */}
      <div className="flex flex-col items-center pt-1 flex-shrink-0">
        <div className="w-3 h-3 rounded-full border-2"
          style={{ background: "var(--bg)", borderColor: color }} />
        <div className="w-px flex-1 mt-1" style={{ background: "var(--border)" }} />
      </div>

      <div className="flex-1 min-w-0 pb-4">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}>
            {event.event_type.replace(/_/g, " ")}
          </span>
          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
            {event.company}
          </span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>·</span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>{date}</span>
          <div className="ml-auto">
            <ConfBar score={event.confidence_score} />
          </div>
        </div>

        <p className="text-sm leading-relaxed mb-3">{event.summary}</p>

        {event.source_urls.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {event.source_urls.slice(0, 3).map((url, i) => {
              let host = url;
              try { host = new URL(url).hostname; } catch {}
              return (
                <a key={i} href={url} target="_blank" rel="noopener noreferrer"
                  className="text-xs px-2 py-0.5 rounded hover:opacity-80 transition-opacity"
                  style={{ background: "var(--surface-2)", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                  ↗ {host}
                </a>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function EventsContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const defaultCompany = searchParams.get("company") ?? COMPANIES[0].full;
  const [company, setCompany] = useState(defaultCompany);
  const [eventType, setEventType] = useState("all");
  const [days, setDays] = useState(30);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.events(company, days, eventType === "all" ? undefined : eventType, 0.0)
      .then(r => setEvents(r.events))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, [company, days, eventType]);

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold mb-6">Events Timeline</h1>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-6">
        {/* Company selector */}
        <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border)" }}>
          {COMPANIES.map(c => (
            <button key={c.full} onClick={() => setCompany(c.full)}
              className="px-4 py-2 text-sm font-medium transition-colors"
              style={{
                background: company === c.full ? "var(--accent)" : "var(--surface)",
                color: company === c.full ? "#fff" : "var(--text-muted)",
              }}>
              {c.label}
            </button>
          ))}
        </div>

        {/* Days */}
        <select value={days} onChange={e => setDays(Number(e.target.value))}
          className="px-3 py-2 rounded-lg text-sm outline-none"
          style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)" }}>
          {[7, 14, 30, 60, 90].map(d => (
            <option key={d} value={d}>Last {d} days</option>
          ))}
        </select>
      </div>

      {/* Event type chips */}
      <div className="flex flex-wrap gap-2 mb-6">
        {EVENT_TYPES.map(t => {
          const color = t === "all" ? "var(--accent)" : (EVENT_COLORS[t] ?? "#64748b");
          const active = eventType === t;
          return (
            <button key={t} onClick={() => setEventType(t)}
              className="px-3 py-1 rounded-full text-xs font-medium transition-all"
              style={{
                background: active ? `${color}33` : "var(--surface)",
                color: active ? color : "var(--text-muted)",
                border: `1px solid ${active ? color : "var(--border)"}`,
              }}>
              {t === "all" ? "All types" : t.replace(/_/g, " ")}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="px-4 py-3 rounded-lg text-sm mb-6"
          style={{ background: "#f8514922", border: "1px solid #f8514944", color: "var(--threat-high)" }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-24 rounded-xl animate-pulse"
              style={{ background: "var(--surface)" }} />
          ))}
        </div>
      ) : events.length === 0 ? (
        <div className="text-sm py-12 text-center" style={{ color: "var(--text-muted)" }}>
          No events found for {company} in the last {days} days.
          Run the pipeline to populate the knowledge base.
        </div>
      ) : (
        <div className="space-y-3">
          {events.map(e => <EventCard key={e.event_id} event={e} />)}
          <div className="text-xs text-center pt-2" style={{ color: "var(--text-muted)" }}>
            {events.length} events · sorted by date desc
          </div>
        </div>
      )}
    </div>
  );
}

export default function EventsPage() {
  return (
    <Suspense>
      <EventsContent />
    </Suspense>
  );
}
