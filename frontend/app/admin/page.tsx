"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type QuarantineItem, type QuarantineStats } from "@/lib/api";

function StatPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="px-4 py-3 rounded-xl text-center"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs mt-1 capitalize" style={{ color: "var(--text-muted)" }}>{label}</div>
    </div>
  );
}

function QuarantineRow({
  item,
  onAction,
}: {
  item: QuarantineItem;
  onAction: (id: string, action: "approve" | "reject") => void;
}) {
  const [acting, setActing] = useState(false);

  const act = async (action: "approve" | "reject") => {
    setActing(true);
    await onAction(item.quarantine_id, action);
    setActing(false);
  };

  const ev = item.extracted_event as Record<string, unknown>;

  return (
    <tr style={{ borderBottom: "1px solid var(--border)" }}>
      <td className="px-4 py-3 align-top">
        <a href={item.source_url} target="_blank" rel="noopener noreferrer"
          className="text-xs hover:underline" style={{ color: "var(--accent)" }}>
          {(() => { try { return new URL(item.source_url).hostname; } catch { return item.source_url; } })()}
        </a>
        <div className="text-xs mt-1 line-clamp-2 max-w-xs" style={{ color: "var(--text-muted)" }}>
          {item.raw_content_excerpt}
        </div>
      </td>

      <td className="px-4 py-3 align-top">
        <div className="text-xs font-medium mb-1 capitalize"
          style={{ color: "var(--accent)" }}>
          {String(ev.event_type ?? "—").replace(/_/g, " ")}
        </div>
        <div className="text-xs line-clamp-3 max-w-xs" style={{ color: "var(--text-muted)" }}>
          {String(ev.summary ?? "—")}
        </div>
      </td>

      <td className="px-4 py-3 align-top text-center">
        <div className="text-sm font-semibold"
          style={{ color: item.confidence_score >= 0.7 ? "var(--threat-low)" : "var(--threat-high)" }}>
          {Math.round(item.confidence_score * 100)}%
        </div>
        <div className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
          {item.error_code}
        </div>
      </td>

      <td className="px-4 py-3 align-top text-xs" style={{ color: "var(--text-muted)" }}>
        {new Date(item.created_at).toLocaleDateString("en-US", {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        })}
      </td>

      <td className="px-4 py-3 align-top">
        {item.status !== "pending" ? (
          <span className="text-xs capitalize px-2 py-1 rounded"
            style={{ background: "var(--surface-2)", color: "var(--text-muted)" }}>
            {item.status}
          </span>
        ) : (
          <div className="flex gap-2">
            <button onClick={() => act("approve")} disabled={acting}
              className="text-xs px-3 py-1 rounded font-medium transition-opacity disabled:opacity-50"
              style={{ background: "#3fb95022", color: "var(--threat-low)", border: "1px solid #3fb95044" }}>
              Approve
            </button>
            <button onClick={() => act("reject")} disabled={acting}
              className="text-xs px-3 py-1 rounded font-medium transition-opacity disabled:opacity-50"
              style={{ background: "#f8514922", color: "var(--threat-high)", border: "1px solid #f8514944" }}>
              Reject
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function AdminPage() {
  const [items, setItems] = useState<QuarantineItem[]>([]);
  const [stats, setStats] = useState<QuarantineStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([api.quarantine(100), api.quarantineStats()])
      .then(([q, s]) => { setItems(q); setStats(s); })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAction = async (id: string, action: "approve" | "reject") => {
    await api.reviewQuarantine(id, action);
    setItems(prev => prev.map(i =>
      i.quarantine_id === id ? { ...i, status: action } : i
    ));
    if (stats) {
      setStats(prev => prev ? {
        ...prev,
        pending: prev.pending - 1,
        [action]: prev[action as keyof QuarantineStats] as number + 1,
      } : prev);
    }
  };

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Quarantine Review</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Low-confidence extractions pending human review · corrections feed DSPy Phase 5 optimizer
        </p>
      </div>

      {stats && (
        <div className="grid grid-cols-5 gap-3 mb-8">
          <StatPill label="pending" value={stats.pending} color="var(--threat-med)" />
          <StatPill label="approved" value={stats.approved} color="var(--threat-low)" />
          <StatPill label="corrected" value={stats.corrected} color="var(--accent)" />
          <StatPill label="rejected" value={stats.rejected} color="var(--threat-high)" />
          <StatPill label="total" value={stats.total} color="var(--text)" />
        </div>
      )}

      {error && (
        <div className="px-4 py-3 rounded-lg text-sm mb-6"
          style={{ background: "#f8514922", border: "1px solid #f8514944", color: "var(--threat-high)" }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map(i => (
            <div key={i} className="h-16 rounded-xl animate-pulse" style={{ background: "var(--surface)" }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-xl p-12 text-center"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="text-3xl mb-3">⚑</div>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No quarantined events. The Extraction Agent sends events here when confidence &lt; 0.70.
          </p>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          <table className="w-full">
            <thead>
              <tr className="text-xs font-medium text-left"
                style={{ background: "var(--surface-2)", color: "var(--text-muted)" }}>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Extracted</th>
                <th className="px-4 py-3 text-center">Confidence</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3">Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <QuarantineRow key={item.quarantine_id} item={item} onAction={handleAction} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {stats && Object.keys(stats.correction_rate_by_event_type).length > 0 && (
        <div className="mt-6 rounded-xl p-5"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <h2 className="text-sm font-semibold mb-3">Correction rate by event type</h2>
          <div className="space-y-2">
            {Object.entries(stats.correction_rate_by_event_type)
              .sort(([, a], [, b]) => b - a)
              .map(([type, rate]) => (
                <div key={type} className="flex items-center gap-3">
                  <div className="text-xs w-36 capitalize" style={{ color: "var(--text-muted)" }}>
                    {type.replace(/_/g, " ")}
                  </div>
                  <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
                    <div className="h-full rounded-full transition-all"
                      style={{
                        width: `${rate * 100}%`,
                        background: rate > 0.3 ? "var(--threat-high)" : "var(--threat-low)",
                      }} />
                  </div>
                  <div className="text-xs w-10 text-right" style={{ color: "var(--text-muted)" }}>
                    {Math.round(rate * 100)}%
                  </div>
                </div>
              ))}
          </div>
          <p className="text-xs mt-3" style={{ color: "var(--text-muted)" }}>
            &gt;30% correction rate → that event type's prompt needs work before Phase 5
          </p>
        </div>
      )}
    </div>
  );
}
