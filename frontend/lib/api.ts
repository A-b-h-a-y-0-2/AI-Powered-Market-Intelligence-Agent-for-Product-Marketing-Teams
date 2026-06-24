const BASE = "/api/v1";

export interface ThreatScore {
  company: string;
  score: number;
  tier: "HIGH" | "MEDIUM" | "LOW";
  trend: "increasing" | "stable" | "decreasing";
  score_components: Record<string, number>;
  narrative: string;
  contributing_event_ids: string[];
  generated_date: string;
}

export interface MarketEvent {
  event_id: string;
  company: string;
  event_type: string;
  timestamp: string;
  summary: string;
  source_urls: string[];
  confidence_score: number;
  stakeholder_tags: string[];
}

export interface EventListResponse {
  events: MarketEvent[];
  count: number;
  company: string;
  days: number;
}

export interface FeatureMatrixResponse {
  company: string;
  taxonomy_version: string;
  last_updated: string;
  features: Record<string, Array<{ name: string; description?: string; source_event_id: string; launched_date: string }>>;
}

export interface QuarantineItem {
  quarantine_id: string;
  source_url: string;
  raw_content_excerpt: string;
  extracted_event: Record<string, unknown>;
  confidence_score: number;
  error_code: string;
  error_details: string;
  created_at: string;
  status: string;
}

export interface QuarantineStats {
  pending: number;
  approved: number;
  corrected: number;
  rejected: number;
  total: number;
  correction_rate_by_event_type: Record<string, number>;
}

export interface HealthStatus {
  status: string;
  timestamp: string;
  components: Record<string, string>;
}

export interface NarrativeEvent {
  narrative_id: string;
  company: string;
  narrative_title: string;
  narrative_summary: string;
  strategic_intent: string;
  confidence: number;
  constituent_event_ids: string[];
  time_window_days: number;
  key_signals: string[];
  generated_date: string;
}

export interface PipelineStatus {
  last_research_run: string | null;
  last_extraction_run: string | null;
  last_sentiment_run: string | null;
  last_narrative_run: string | null;
  last_threat_run: string | null;
  next_scheduled_run: string | null;
  events_ingested_today: number;
  events_ingested_total: number;
  pipeline_health: "healthy" | "degraded" | "down";
}

export interface ChatChunk {
  type: "chunk" | "status" | "done" | "error";
  content: string;
  sources?: Array<{ event_id: string; url: string; summary: string }>;
  confidence?: number;
  caveats?: string[];
  session_id?: string;
  error_code?: string;
}

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString());
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthStatus>("/health"),

  threats: () => get<ThreatScore[]>("/threats"),
  threatByCompany: (company: string) => get<ThreatScore>(`/threats/${encodeURIComponent(company)}`),

  events: (company: string, days = 30, eventType?: string, minConfidence = 0.7) =>
    get<EventListResponse>("/events", {
      company,
      days,
      ...(eventType ? { event_type: eventType } : {}),
      min_confidence: minConfidence,
    }),

  matrix: (company: string) => get<FeatureMatrixResponse>(`/matrix/${encodeURIComponent(company)}`),

  narratives: (company: string, days = 90) =>
    get<NarrativeEvent[]>("/narratives", { company, days }),

  pipelineStatus: () => get<PipelineStatus>("/pipeline/summary"),

  quarantine: (limit = 50) => get<QuarantineItem[]>("/admin/quarantine", { limit }),
  quarantineStats: () => get<QuarantineStats>("/admin/quarantine/stats"),
  reviewQuarantine: async (
    id: string,
    action: "approve" | "correct" | "reject",
    corrections?: Record<string, unknown>
  ) => {
    const res = await fetch(`${BASE}/admin/quarantine/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, corrections }),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return res.json();
  },

  chatStream: async function* (
    message: string,
    sessionId?: string,
    stakeholderRole?: string
  ): AsyncGenerator<ChatChunk> {
    const res = await fetch(`${BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        stakeholder_role: stakeholderRole,
      }),
    });
    if (!res.ok || !res.body) throw new Error(`${res.status}: ${await res.text()}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6)) as ChatChunk;
          } catch {
            // malformed chunk — skip
          }
        }
      }
    }
  },
};
