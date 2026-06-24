"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/dashboard", label: "Dashboard", icon: "◈" },
  { href: "/chat", label: "Chat", icon: "◉" },
  { href: "/events", label: "Events", icon: "◎" },
  { href: "/matrix", label: "Matrix", icon: "⊞" },
  { href: "/narratives", label: "Narratives", icon: "◑" },
  { href: "/pipeline", label: "Pipeline", icon: "⟳" },
  { href: "/admin", label: "Quarantine", icon: "⚑" },
];

export default function Nav() {
  const path = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-full w-52 flex flex-col border-r"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
      <div className="px-5 py-5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full flex-shrink-0"
            style={{
              background: "var(--threat-low)",
              boxShadow: "0 0 6px var(--threat-low)",
              animation: "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite",
            }} />
          <div className="text-sm font-semibold" style={{ color: "var(--text)" }}>
            Intel Agent
          </div>
        </div>
        <div className="text-xs mt-0.5 font-medium tracking-widest uppercase"
          style={{ color: "var(--text-muted)" }}>Market Intel</div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {links.map(({ href, label, icon }) => {
          const active = path.startsWith(href);
          return (
            <Link key={href} href={href}
              className="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors"
              style={{
                background: active ? "var(--surface-2)" : "transparent",
                color: active ? "var(--text)" : "var(--text-muted)",
              }}>
              <span className="text-base w-4 text-center">{icon}</span>
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t" style={{ borderColor: "var(--border)" }}>
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>
          Research is offline.
          <br />Answers are online.
        </div>
      </div>
    </aside>
  );
}
