"use client";

import { useEffect, useState } from "react";
import { api, type FeatureMatrixResponse } from "@/lib/api";

const COMPANIES = ["Competitor A", "Competitor B", "Competitor C"];

const CATEGORY_ICONS: Record<string, string> = {
  ai_automation: "◈",
  crm_integration: "⊕",
  analytics_reporting: "◎",
  security_compliance: "⚑",
  pricing_packaging: "◉",
  api_developer: "</>",
  content_generation: "✦",
  workflow_ux: "⊞",
};

export default function MatrixPage() {
  const [company, setCompany] = useState(COMPANIES[0]);
  const [matrix, setMatrix] = useState<FeatureMatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.matrix(company)
      .then(setMatrix)
      .catch(e => {
        if (String(e).includes("404")) {
          setMatrix(null);
          setError(null);
        } else {
          setError(String(e));
        }
      })
      .finally(() => setLoading(false));
  }, [company]);

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Feature Matrix</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Living feature comparison · updated within 15 min of each FeatureLaunch event
        </p>
      </div>

      <div className="flex rounded-lg overflow-hidden border mb-6"
        style={{ borderColor: "var(--border)", display: "inline-flex" }}>
        {COMPANIES.map(c => (
          <button key={c} onClick={() => setCompany(c)}
            className="px-4 py-2 text-sm font-medium transition-colors"
            style={{
              background: company === c ? "var(--accent)" : "var(--surface)",
              color: company === c ? "#fff" : "var(--text-muted)",
            }}>
            {c}
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
        <div className="space-y-3">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-20 rounded-xl animate-pulse"
              style={{ background: "var(--surface)" }} />
          ))}
        </div>
      ) : !matrix ? (
        <div className="rounded-xl p-12 text-center"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="text-3xl mb-3">⊞</div>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No feature matrix for {company} yet.
            The Matrix Agent populates this automatically after each FeatureLaunch or ProductUpdate event.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-xs pb-2" style={{ color: "var(--text-muted)" }}>
            <span>v{matrix.taxonomy_version}</span>
            <span>·</span>
            <span>Updated {new Date(matrix.last_updated).toLocaleDateString()}</span>
            <span>·</span>
            <span>{Object.keys(matrix.features).length} categories</span>
          </div>

          {Object.entries(matrix.features).map(([category, features]) => (
            <div key={category} className="rounded-xl overflow-hidden"
              style={{ border: "1px solid var(--border)" }}>
              <div className="px-5 py-3 flex items-center gap-2"
                style={{ background: "var(--surface-2)" }}>
                <span className="text-base w-5 text-center">
                  {CATEGORY_ICONS[category] ?? "·"}
                </span>
                <span className="text-sm font-semibold capitalize">
                  {category.replace(/_/g, " ")}
                </span>
                <span className="text-xs ml-auto"
                  style={{ color: "var(--text-muted)" }}>
                  {features.length} features
                </span>
              </div>

              {features.length === 0 ? (
                <div className="px-5 py-3 text-sm" style={{ color: "var(--text-muted)" }}>
                  No features tracked yet
                </div>
              ) : (
                <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                  {features.map((f, i) => (
                    <div key={i} className="px-5 py-3 flex items-center gap-3"
                      style={{ background: "var(--surface)" }}>
                      <div className="flex-1">
                        <div className="text-sm">{f.name}</div>
                        {f.launched_date && (
                          <div className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                            Launched {new Date(f.launched_date).toLocaleDateString("en-US", {
                              month: "short", year: "numeric",
                            })}
                          </div>
                        )}
                      </div>
                      {f.source_event_id && (
                        <span className="text-xs px-1.5 py-0.5 rounded"
                          style={{ background: "var(--surface-2)", color: "var(--text-muted)" }}>
                          event: {f.source_event_id.slice(0, 8)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
