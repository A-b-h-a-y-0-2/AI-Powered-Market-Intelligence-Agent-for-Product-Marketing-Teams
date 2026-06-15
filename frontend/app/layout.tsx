import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/nav";

export const metadata: Metadata = {
  title: "Market Intelligence Agent",
  description: "AI-powered competitive market intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full flex" style={{ background: "var(--bg)", color: "var(--text)" }}>
        <Nav />
        <main className="flex-1 ml-52 min-h-screen overflow-auto">
          {children}
        </main>
      </body>
    </html>
  );
}
