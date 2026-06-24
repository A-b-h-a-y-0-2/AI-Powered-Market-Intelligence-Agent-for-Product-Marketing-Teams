"use client";

import { useEffect, useState } from "react";
import { api, type FeatureMatrixResponse } from "@/lib/api";

const COMPANIES = [
  { label: "McKinsey", full: "McKinsey & Company" },
  { label: "BCG", full: "Boston Consulting Group" },
  { label: "Bain", full: "Bain & Company" },
  { label: "Deloitte", full: "Deloitte" },
];

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

function SingleMatrix({ company, matrix, error, loading }: {
  company: string;
  matrix: FeatureMatrixResponse | null;
  error: string | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="h-20 rounded-xl animate-pulse"
            style={{ background: "var(--surface)" }} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-4 py-3 rounded-lg text-sm mb-6"
        style={{ background: "#f8514922", border: "1px solid #f8514944", color: "var(--threat-high)" }}>
        {error}
      </div>
    );
  }
  if (!matrix) {
    return (
      <div className="rounded-xl p-12 text-center"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="text-3xl mb-3">⊞</div>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          No feature matrix for {company} yet.
          The Matrix Agent populates this automatically after each FeatureLaunch or ProductUpdate event.
        </p>
      </div>
    );
  }

  return (
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
            <span className="text-xs ml-auto" style={{ color: "var(--text-muted)" }}>
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
  );
}

function CompareTable({ matrices }: { matrices: Map<string, FeatureMatrixResponse | null> }) {
  const allCategories = new Set<string>();
  for (const m of matrices.values()) {
    if (m) Object.keys(m.features).forEach(c => allCategories.add(c));
  }
  const categories = Array.from(allCategories).sort();
  const companies = COMPANIES.filter(c => matrices.has(c.full));

  if (categories.length === 0) {
    return (
      <div className="rounded-xl p-12 text-center"
        style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="text-3xl mb-3">⊞</div>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          No matrix data available yet. Run the pipeline to populate feature matrices.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl overflow-auto" style={{ border: "1px solid var(--border)" }}>
      <table className="w-full text-sm" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
            <th className="text-left px-4 py-3 font-semibold text-xs uppercase tracking-wider"
              style={{ color: "var(--text-muted)", minWidth: "160px" }}>Category</th>
            {companies.map(c => (
              <th key={c.full} className="px-4 py-3 font-semibold text-xs uppercase tracking-wider text-center"
                style={{ color: "var(--text)", minWidth: "120px" }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {categories.map((cat, i) => (
            <tr key={cat}
              style={{
                background: i % 2 === 0 ? "var(--surface)" : "var(--surface-2)",
                borderBottom: "1px solid var(--border)",
              }}>
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <span>{CATEGORY_ICONS[cat] ?? "·"}</span>
                  <span className="capitalize" style={{ color: "var(--text)" }}>
                    {cat.replace(/_/g, " ")}
                  </span>
                </div>
              </td>
              {companies.map(c => {
                const m = matrices.get(c.full);
                const features = m?.features[cat] ?? [];
                const count = features.length;
                return (
                  <td key={c.full} className="px-4 py-3 text-center">
                    {count > 0 ? (
                      <span className="font-medium" style={{ color: "var(--threat-low)" }}>
                        ✓ {count}
                      </span>
                    ) : (
                      <span style={{ color: "var(--border)" }}>–</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MatrixPage() {
  const [company, setCompany] = useState(COMPANIES[0].full);
  const [matrix, setMatrix] = useState<FeatureMatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareMatrices, setCompareMatrices] = useState<Map<string, FeatureMatrixResponse | null>>(new Map());
  const [compareLoading, setCompareLoading] = useState(false);

  useEffect(() => {
    if (compareMode) return;
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
  }, [company, compareMode]);

  useEffect(() => {
    if (!compareMode) return;
    setCompareLoading(true);
    Promise.all(
      COMPANIES.map(c =>
        api.matrix(c.full)
          .then(m => [c.full, m] as [string, FeatureMatrixResponse])
          .catch(() => [c.full, null] as [string, null])
      )
    ).then(entries => {
      setCompareMatrices(new Map(entries));
      setCompareLoading(false);
    });
  }, [compareMode]);

  return (
    <div className="p-8">
      <div className="mb-6 flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Feature Matrix</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            Living feature comparison · updated within 15 min of each FeatureLaunch event
          </p>
        </div>
        <button
          onClick={() => setCompareMode(v => !v)}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{
            background: compareMode ? "var(--accent)" : "var(--surface)",
            color: compareMode ? "#fff" : "var(--text-muted)",
            border: `1px solid ${compareMode ? "var(--accent)" : "var(--border)"}`,
          }}>
          ⊞ Compare all
        </button>
      </div>

      {!compareMode && (
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
      )}

      {compareMode ? (
        compareLoading ? (
          <div className="space-y-3">
            {[0, 1, 2, 3, 4].map(i => (
              <div key={i} className="h-12 rounded-xl animate-pulse"
                style={{ background: "var(--surface)" }} />
            ))}
          </div>
        ) : (
          <CompareTable matrices={compareMatrices} />
        )
      ) : (
        <SingleMatrix
          company={company}
          matrix={matrix}
          error={error}
          loading={loading}
        />
      )}
    </div>
  );
}
