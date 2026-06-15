"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api, type ChatChunk } from "@/lib/api";

const ROLES = [
  { value: "marketing", label: "Marketing" },
  { value: "sales", label: "Sales" },
  { value: "ceo", label: "CEO" },
  { value: "product", label: "Product" },
  { value: "customer_success", label: "CS" },
];

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Array<{ event_id: string; url: string; summary: string }>;
  confidence?: number;
  caveats?: string[];
  streaming?: boolean;
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const color = pct >= 70 ? "var(--threat-low)" : pct >= 40 ? "var(--threat-med)" : "var(--threat-high)";
  return (
    <span className="text-xs px-1.5 py-0.5 rounded font-medium"
      style={{ background: `${color}22`, color, border: `1px solid ${color}44` }}>
      {pct}% confidence
    </span>
  );
}

function SourceChip({ url, summary }: { url: string; summary: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded hover:opacity-80 transition-opacity"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-muted)" }}
      title={summary}>
      ↗ {new URL(url.startsWith("http") ? url : `https://${url}`).hostname}
    </a>
  );
}

function Bubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
      <div className={`max-w-2xl px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
        isUser ? "rounded-br-sm" : "rounded-bl-sm"
      }`}
        style={{
          background: isUser ? "var(--accent)" : "var(--surface)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--border)",
        }}>
        {msg.content}
        {msg.streaming && (
          <span className="inline-block w-1.5 h-4 ml-1 animate-pulse rounded-sm"
            style={{ background: "var(--text-muted)", verticalAlign: "text-bottom" }} />
        )}
      </div>

      {!isUser && (msg.confidence !== undefined || (msg.sources && msg.sources.length > 0)) && (
        <div className="flex flex-wrap items-center gap-2 px-1">
          {msg.confidence !== undefined && <ConfidenceBadge confidence={msg.confidence} />}
          {msg.sources?.map((s, i) => (
            <SourceChip key={i} url={s.url} summary={s.summary} />
          ))}
        </div>
      )}

      {!isUser && msg.caveats && msg.caveats.length > 0 && (
        <div className="text-xs px-1" style={{ color: "var(--text-muted)" }}>
          ⚠ {msg.caveats.join(" · ")}
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [role, setRole] = useState("marketing");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);

    const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: text };
    const aiMsg: Message = {
      id: crypto.randomUUID(), role: "assistant", content: "", streaming: true,
    };

    setMessages(prev => [...prev, userMsg, aiMsg]);

    try {
      for await (const chunk of api.chatStream(text, sessionId, role)) {
        if (chunk.type === "chunk" || chunk.type === "status") {
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id
              ? { ...m, content: chunk.type === "chunk" ? m.content + chunk.content : m.content }
              : m
          ));
        }
        if (chunk.type === "done") {
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id
              ? {
                  ...m,
                  content: chunk.content || m.content,
                  sources: chunk.sources,
                  confidence: chunk.confidence,
                  caveats: chunk.caveats,
                  streaming: false,
                }
              : m
          ));
        }
        if (chunk.type === "error") {
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id
              ? { ...m, content: `Error: ${chunk.content}`, streaming: false }
              : m
          ));
        }
      }
    } catch (err) {
      setMessages(prev => prev.map(m =>
        m.id === aiMsg.id
          ? { ...m, content: `Failed to connect to backend: ${err}`, streaming: false }
          : m
      ));
    } finally {
      setSending(false);
    }
  }, [input, role, sessionId, sending]);

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
        <div>
          <h1 className="text-base font-semibold">Market Intelligence Chat</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
            Grounded answers from the knowledge base · live fallback via Tavily
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>Role:</span>
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border)" }}>
            {ROLES.map(r => (
              <button key={r.value} onClick={() => setRole(r.value)}
                className="px-3 py-1.5 text-xs font-medium transition-colors"
                style={{
                  background: role === r.value ? "var(--accent)" : "var(--surface-2)",
                  color: role === r.value ? "#fff" : "var(--text-muted)",
                }}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-4"
            style={{ color: "var(--text-muted)" }}>
            <div className="text-4xl">◉</div>
            <p className="text-sm text-center max-w-sm">
              Ask about competitor activity, threat levels, pricing changes, feature launches, or hiring signals.
            </p>
            <div className="grid grid-cols-2 gap-2 mt-2 w-full max-w-lg">
              {[
                "What did Competitor A ship this week?",
                "Why did Competitor B cut prices?",
                "What will Competitor C do next quarter?",
                "What's the overall market trend?",
              ].map(q => (
                <button key={q} onClick={() => setInput(q)}
                  className="text-xs text-left px-3 py-2 rounded-lg hover:opacity-80 transition-opacity"
                  style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map(m => <Bubble key={m.id} msg={m} />)}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-6 py-4 border-t flex-shrink-0"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
        <div className="flex gap-3 max-w-4xl mx-auto">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Ask about competitor activity…"
            rows={1}
            className="flex-1 resize-none rounded-xl px-4 py-3 text-sm outline-none"
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              color: "var(--text)",
            }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || sending}
            className="px-5 rounded-xl text-sm font-medium transition-opacity disabled:opacity-40"
            style={{ background: "var(--accent)", color: "#fff" }}>
            {sending ? "…" : "Send"}
          </button>
        </div>
        <p className="text-xs text-center mt-2" style={{ color: "var(--text-muted)" }}>
          Shift+Enter for newline · sources shown below each response
        </p>
      </div>
    </div>
  );
}
