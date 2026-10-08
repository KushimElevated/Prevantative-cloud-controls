"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useFeatures } from "@/lib/features";
import type { Experience } from "@/lib/workspace";

const OPTIONS: { value: Experience; label: string; href: string; testId: string; hint: string }[] = [
  { value: "classic", label: "Classic", href: "/dashboard", testId: "experience-classic",
    hint: "Use the Classic Experience and make it your default" },
  { value: "workspace", label: "AI Workspace", href: "/workspace", testId: "experience-workspace",
    hint: "Use the optional AI Control Workspace and make it your default" },
];

/** Classic / AI Workspace switch. Only offered when the workspace feature is enabled on the server. */
export function ExperienceSelector() {
  const { workspaceEnabled, setExperience } = useFeatures();
  const pathname = usePathname();
  const router = useRouter();
  const [pending, setPending] = useState<Experience | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!workspaceEnabled) return null;

  const current: Experience = pathname === "/workspace" || pathname.startsWith("/workspace/") ? "workspace" : "classic";

  const choose = async (option: (typeof OPTIONS)[number]) => {
    setPending(option.value);
    setError(null);
    try {
      await setExperience(option.value);
      if (option.value !== current) router.push(option.href);
    } catch (e) {
      setError(`Could not save your preference: ${(e as Error).message}`);
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <div role="group" aria-label="Experience" className="inline-flex rounded border border-slate-300 bg-slate-50 p-0.5 dark:border-slate-600 dark:bg-slate-800">
        {OPTIONS.map((o) => {
          const active = o.value === current;
          return (
            <button key={o.value} type="button" data-testid={o.testId} aria-pressed={active} title={o.hint}
                    disabled={pending !== null} onClick={() => choose(o)}
                    className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-wait ${active
                      ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-50"
                      : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-slate-50"}`}>
              {o.label}
            </button>
          );
        })}
      </div>
      {error && <span role="alert" className="text-xs text-red-700 dark:text-red-300">{error}</span>}
    </div>
  );
}
