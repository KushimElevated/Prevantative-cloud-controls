"use client";

import { ReactNode, useId } from "react";

/** One of the three workspace columns. Same card styling as the Classic Section. */
export function Panel({ title, description, testId, busy, children }: {
  title: string; description?: ReactNode; testId: string; busy?: boolean; children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} aria-busy={busy || undefined} data-testid={testId}
             className="min-w-0 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <h2 id={headingId} className="text-base font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
      {description && <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Small uppercase heading used inside panels. */
export function PanelHeading({ children }: { children: ReactNode }) {
  return <h3 className="mb-1.5 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500 first:mt-0 dark:text-slate-400">{children}</h3>;
}
