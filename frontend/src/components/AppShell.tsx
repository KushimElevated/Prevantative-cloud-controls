"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import { useFeatures } from "@/lib/features";
import { useSession } from "@/lib/session";
import { ExperienceSelector } from "./ExperienceSelector";
import { Loading } from "./ui";

const NAV = [
  ["/dashboard", "Dashboard"],
  ["/controls", "Controls"],
  ["/assessments", "Assessments"],
  ["/exceptions", "Exceptions"],
  ["/rollouts", "Rollouts"],
  ["/audit", "Audit"],
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { user, ready, logout } = useSession();
  const { workspaceEnabled } = useFeatures();
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  const inWorkspace = pathname === "/workspace" || pathname.startsWith("/workspace/");
  // The three-panel workspace needs more room than the Classic pages.
  const width = inWorkspace ? "max-w-screen-2xl" : "max-w-7xl";

  useEffect(() => {
    if (ready && !user && !isLogin) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [ready, user, isLogin, pathname, router]);

  if (isLogin) return <>{children}</>;
  if (!ready || !user) return <main className="mx-auto max-w-7xl p-6"><Loading what="Checking session" /></main>;

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
        <div className={`mx-auto flex flex-wrap items-center gap-4 px-6 py-3 ${width}`}>
          <Link href="/dashboard" className="font-semibold text-slate-900 dark:text-slate-50">
            Control Engineering
          </Link>
          <nav aria-label="Primary" className="flex flex-wrap gap-1 text-sm">
            {NAV.map(([href, label]) => {
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link key={href} href={href} aria-current={active ? "page" : undefined}
                      className={`rounded px-2 py-1 ${active ? "bg-slate-100 font-medium text-slate-900 dark:bg-slate-800 dark:text-slate-50" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                  {label}
                </Link>
              );
            })}
            {workspaceEnabled && (
              <>
                <span aria-hidden="true" className="mx-1 my-1 w-px self-stretch bg-slate-300 dark:bg-slate-600" />
                <Link href="/workspace" data-testid="nav-workspace" aria-current={inWorkspace ? "page" : undefined}
                      className={`inline-flex items-center gap-1.5 rounded px-2 py-1 ${inWorkspace ? "bg-violet-50 font-medium text-violet-900 dark:bg-violet-950 dark:text-violet-100" : "text-violet-800 hover:bg-violet-50 dark:text-violet-200 dark:hover:bg-violet-950"}`}>
                  AI Control Workspace
                  <span className="rounded border border-violet-300 px-1 text-[10px] font-medium uppercase tracking-wide text-violet-700 dark:border-violet-700 dark:text-violet-300">Optional</span>
                </Link>
              </>
            )}
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-3 text-sm">
            <ExperienceSelector />
            <span className="text-slate-700 dark:text-slate-200" data-testid="current-user">
              {user.display_name}
              <span className="ml-1 text-xs text-slate-500">({user.grants.map((g) => g.role).filter((r, i, a) => a.indexOf(r) === i).join(", ")})</span>
            </span>
            <button onClick={() => { logout(); router.push("/login"); }}
                    className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200">
              Switch identity
            </button>
          </div>
        </div>
        <div className="bg-amber-50 px-6 py-1 text-center text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100">
          Local demo: fixture inventory, mock pipeline and fixture observations only. No cloud credentials; nothing is deployed.
        </div>
      </header>
      <main className={`mx-auto px-6 py-6 ${width}`}>{children}</main>
    </div>
  );
}
