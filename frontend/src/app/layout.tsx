import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Control Engineering Platform",
  description: "Turn cloud security intent into governed, verifiable preventive controls (local MVP).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
