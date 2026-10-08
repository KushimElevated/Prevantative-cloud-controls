"use client";

import { KeyboardEvent, ReactNode, useEffect, useId, useRef } from "react";
import { ApiError } from "@/lib/api";
import { Button, ErrorBox } from "@/components/ui";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal confirmation for every state change started from the workspace. Focus moves into the dialog,
 * Tab stays inside it, Escape cancels, and focus returns to where it was when the dialog closes.
 */
export function ConfirmDialog({ title, children, confirmLabel = "Confirm", cancelLabel = "Cancel", onConfirm, onCancel,
  pending = false, error = null }: {
  title: string; children: ReactNode; confirmLabel?: string; cancelLabel?: string;
  onConfirm: () => void; onCancel: () => void; pending?: boolean; error?: ApiError | Error | null;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    return () => previous?.focus?.();
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      if (!pending) onCancel();
      return;
    }
    if (e.key !== "Tab" || !ref.current) return;
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown} tabIndex={-1}
           className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-slate-200 bg-white p-5 shadow-xl focus:outline-none dark:border-slate-700 dark:bg-slate-900">
        <h2 id={titleId} className="text-lg font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
        <div className="mt-2 text-sm text-slate-700 dark:text-slate-200">{children}</div>
        <ErrorBox error={error} />
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={pending} testId="ws-cancel">{cancelLabel}</Button>
          <Button onClick={onConfirm} disabled={pending} testId="ws-confirm">{pending ? "Working…" : confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}
