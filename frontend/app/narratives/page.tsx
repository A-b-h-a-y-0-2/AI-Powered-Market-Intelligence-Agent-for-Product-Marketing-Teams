"use client";

import { useEffect, useState } from "react";
import { api, type NarrativeEvent } from "@/lib/api";

const COMPANIES = [
  { label: "McKinsey", full: "McKinsey & Company" },
  { label: "BCG", full: "Boston Consulting Group" },
  { label: "Bain", full: "Bain & Company" },
  { label: "Deloitte", full: "Deloitte" },
];

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "var(--threat-low)" : pct >= 40 ? "var(--threat-med)" : "var(--threat-high)";
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs font-medium w-10 text-right" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}

function NarrativeCard({ narrative }: { narrative: NarrativeEvent }) {
  const date = new Date(narrative.generated_date).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  });

  return (
    <div className="rounded-xl p-6" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <h3 className="text-base font-bold leading-snug" style={{ color: "var(--text)" }}>
          {narrative.narrative_title}
        </h3>
        <span className="text-xs flex-shrink-0 mt-0.5" style={{ color: "var(--text-muted)" }}>
          {date}
        </span>
      </div>

      <p className="text-sm leading-relaxed mb-4" style={{ color: "var(--text)" }}>
        {narrative.narrative_summary}
      </p>

      {narrative.strategic_intent && (
        <p className="text-sm italic pl-4 mb-4 leading-relaxed"
          style={{ color: "var(--text-muted)", borderLeft: "2px solid var(--accent)" }}>
          {narrative.strategic_intent}
        </p>
      )}

      <div className="mb-4">
        <div className="text-xs font-medium mb-1.5" style={{ color: "var(--text-muted)" }}>Confidence</div>
        <ConfidenceBar value={narrative.confidence} />
      </div>

      {narrative.key_signals.length > 0 && (
        <div className="mb-4">
          <div className="text-xs font-medium mb-2" style={{ color: "var(--text-muted)" }}>Key signals</div>
          <div className="flex flex-wrap gap-1.5">
            {narrative.key_signals.map((signal, i) => (
              <span key={i} className="text-xs px-2 py-0.5 rounded-full"
                style={{ background: "#6366f118", color: "var(--accent)", border: "1px solid #6366f133" }}>
                {signal}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-4 pt-3"
        style={{ borderTop: "1px solid var(--border)" }}>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {narrative.constituent_event_ids.length} constituent event{narrative.constituent_event_ids.length !== 1 ? "s" : ""}
        </span>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {narrative.time_window_days}d window
        </span>
      </div>
    </div>
  );
}

function EmptyState({ company }: { company: string }) {
  return (
    <div className="rounded-xl p-12 text-center"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="text-4xl mb-4">◑</div>
      <div className="text-sm font-medium mb-2" style={{ color: "var(--text)" }}>
        No narratives yet for {company}
      </div>
      <p className="text-sm max-w-sm mx-auto leading-relaxed" style={{ color: "var(--text-muted)" }}>
        The Narrative Agent clusters events into strategic stories.
        It runs every Sunday at 05:00 — check back then.
      </p>
    </div>
  );
}

export default function NarrativesPage() {
  const [company, setCompany] = useState(COMPANIES[0].full);
  const [narratives, setNarratives] = useState<NarrativeEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.narratives(company, 90)
      .then(setNarratives)
      .catch(e => {
        const msg = String(e);
        if (msg.includes("404") || msg.includes("422")) {
          setNarratives([]);
          setError(null);
        } else {
          setError(msg);
        }
      })
      .finally(() => setLoading(false));
  }, [company]);

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Strategic Narratives</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Clusters of competitor events synthesised into strategic stories · generated weekly
        </p>
      </div>

      <div className="flex rounded-lg overflow-hidden border mb-6"
        style={{ borderColor: "var(--border)", display: "inline-flex" }}>
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

      {error && (
        <div className="px-4 py-3 rounded-lg text-sm mb-6"
          style={{ background: "#f8514922", border: "1px solid #f8514944", color: "var(--threat-high)" }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          {[0, 1, 2].map(i => (
            <div key={i} className="h-48 rounded-xl animate-pulse"
              style={{ background: "var(--surface)" }} />
          ))}
        </div>
      ) : narratives.length === 0 ? (
        <EmptyState company={COMPANIES.find(c => c.full === company)?.label ?? company} />
      ) : (
        <div className="space-y-4">
          {narratives.map(n => (
            <NarrativeCard key={n.narrative_id} narrative={n} />
          ))}
          <div className="text-xs text-center pt-2" style={{ color: "var(--text-muted)" }}>
            {narratives.length} narrative{narratives.length !== 1 ? "s" : ""} · last 90 days
          </div>
        </div>
      )}
    </div>
  );
}
