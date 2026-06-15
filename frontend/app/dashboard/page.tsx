"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type ThreatScore, type MarketEvent } from "@/lib/api";

const TIER_COLOR: Record<string, string> = {
  HIGH: "var(--threat-high)",
  MEDIUM: "var(--threat-med)",
  LOW: "var(--threat-low)",
};

const TREND_ICON: Record<string, string> = {
  increasing: "↑",
  stable: "→",
  decreasing: "↓",
};

function ThreatCard({ threat }: { threat: ThreatScore }) {
  const color = TIER_COLOR[threat.tier] ?? "var(--text-muted)";
  const pct = Math.round(threat.score);

  return (
    <div className="rounded-xl p-6 flex flex-col gap-4"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>

      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-medium tracking-widest uppercase mb-1"
            style={{ color: "var(--text-muted)" }}>Competitor</div>
          <div className="text-lg font-semibold">{threat.company}</div>
        </div>
        <span className="px-2 py-1 rounded text-xs font-bold tracking-wide"
          style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}>
          {threat.tier}
        </span>
      </div>

      {/* Score bar */}
      <div>
        <div className="flex items-end justify-between mb-2">
          <span className="text-4xl font-bold" style={{ color }}>{pct}</span>
          <span className="text-sm pb-1" style={{ color: "var(--text-muted)" }}>/ 100</span>
        </div>
        <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
          <div className="h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: color }} />
        </div>
      </div>

      {/* Components */}
      <div className="grid grid-cols-3 gap-2">
        {Object.entries(threat.score_components).map(([k, v]) => (
          <div key={k} className="text-center">
            <div className="text-sm font-semibold">{Math.round(v)}</div>
            <div className="text-xs capitalize" style={{ color: "var(--text-muted)" }}>
              {k.replace(/_/g, " ")}
            </div>
          </div>
        ))}
      </div>

      {/* Trend + narrative */}
      <div className="pt-2 border-t" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-1.5 text-sm mb-2">
          <span style={{ color }}>{TREND_ICON[threat.trend]}</span>
          <span className="capitalize" style={{ color: "var(--text-muted)" }}>{threat.trend}</span>
        </div>
        {threat.narrative && (
          <p className="text-sm leading-relaxed line-clamp-3" style={{ color: "var(--text-muted)" }}>
            {threat.narrative}
          </p>
        )}
      </div>

      <Link href={`/events?company=${encodeURIComponent(threat.company)}`}
        className="text-xs mt-auto hover:underline" style={{ color: "var(--accent)" }}>
        View events →
      </Link>
    </div>
  );
}

function EventRow({ event }: { event: MarketEvent }) {
  const badge: Record<string, string> = {
    feature_launch: "#6366f1",
    pricing_change: "#f59e0b",
    acquisition: "#ef4444",
    funding: "#22c55e",
    partnership: "#06b6d4",
    product_update: "#8b5cf6",
    hiring_trend: "#ec4899",
    market_trend: "#64748b",
  };
  const color = badge[event.event_type] ?? "#64748b";
  const date = new Date(event.timestamp).toLocaleDateString("en-US", {
    month: "short", day: "numeric",
  });

  return (
    <div className="flex gap-4 py-3 border-b last:border-0"
      style={{ borderColor: "var(--border)" }}>
      <div className="flex-shrink-0 w-16 text-xs pt-0.5" style={{ color: "var(--text-muted)" }}>
        {date}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}>
            {event.event_type.replace(/_/g, " ")}
          </span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>{event.company}</span>
        </div>
        <p className="text-sm truncate">{event.summary}</p>
      </div>
      <div className="flex-shrink-0 text-xs pt-0.5" style={{ color: "var(--text-muted)" }}>
        {Math.round(event.confidence_score * 100)}%
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [threats, setThreats] = useState<ThreatScore[]>([]);
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.threats().catch(() => [] as ThreatScore[]),
      // Load events for all companies combined — use the first company if any
    ]).then(([t]) => {
      setThreats(t);
      if (t.length > 0) {
        // Load events for the highest-threat company
        const top = t.sort((a, b) => b.score - a.score)[0];
        return api.events(top.company, 14);
      }
      return null;
    }).then((evResp) => {
      if (evResp) setEvents(evResp.events.slice(0, 10));
    }).catch((e) => {
      setError(String(e));
    }).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="p-8">
        <div className="h-7 w-48 rounded mb-8 animate-pulse" style={{ background: "var(--surface-2)" }} />
        <div className="grid grid-cols-3 gap-4">
          {[0, 1, 2].map(i => (
            <div key={i} className="h-72 rounded-xl animate-pulse" style={{ background: "var(--surface)" }} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold">Threat Dashboard</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            Pre-computed Sunday 7 AM · {threats.length} competitors tracked
          </p>
        </div>
        <Link href="/chat"
          className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity hover:opacity-80"
          style={{ background: "var(--accent)", color: "#fff" }}>
          Ask the agent →
        </Link>
      </div>

      {error && (
        <div className="mb-6 px-4 py-3 rounded-lg text-sm"
          style={{ background: "#f8514922", border: "1px solid #f8514944", color: "var(--threat-high)" }}>
          Backend not reachable: {error}. Start the API server with <code>python main.py</code>.
        </div>
      )}

      {threats.length === 0 && !error && (
        <div className="mb-6 px-4 py-3 rounded-lg text-sm"
          style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-muted)" }}>
          No threat scores yet. They are computed Sunday 7 AM after the synthesis pipeline runs.
          Trigger it manually via <code>POST /api/v1/pipeline/trigger</code> or wait for Sunday.
        </div>
      )}

      {threats.length > 0 && (
        <div className="grid gap-4 mb-10"
          style={{ gridTemplateColumns: `repeat(${Math.min(threats.length, 3)}, 1fr)` }}>
          {threats
            .sort((a, b) => b.score - a.score)
            .map(t => <ThreatCard key={t.company} threat={t} />)}
        </div>
      )}

      {events.length > 0 && (
        <div className="rounded-xl p-6"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold">Recent Events</h2>
            <Link href="/events" className="text-xs hover:underline"
              style={{ color: "var(--accent)" }}>View all →</Link>
          </div>
          <div>
            {events.map(e => <EventRow key={e.event_id} event={e} />)}
          </div>
        </div>
      )}
    </div>
  );
}
