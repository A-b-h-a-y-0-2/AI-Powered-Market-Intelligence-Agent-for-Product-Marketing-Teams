"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/dashboard", label: "Dashboard", icon: "◈" },
  { href: "/chat", label: "Chat", icon: "◉" },
  { href: "/events", label: "Events", icon: "◎" },
  { href: "/matrix", label: "Matrix", icon: "⊞" },
  { href: "/admin", label: "Quarantine", icon: "⚑" },
];

export default function Nav() {
  const path = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-full w-52 flex flex-col border-r"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
      <div className="px-5 py-5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="text-xs font-semibold tracking-widest uppercase"
          style={{ color: "var(--text-muted)" }}>Market Intel</div>
        <div className="text-sm font-medium mt-0.5" style={{ color: "var(--text)" }}>
          Intelligence Agent
        </div>
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
