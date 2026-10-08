"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { SAFE_HREF } from "./contracts";

export function isSafeHref(href: unknown): href is string {
  return typeof href === "string" && href.length <= 512 && SAFE_HREF.test(href);
}

/** Internal route links only: anything that is not a SAFE_HREF renders as plain text, never as an anchor. */
export function SafeLink({ href, children, className }: { href: unknown; children: ReactNode; className?: string }) {
  if (!isSafeHref(href)) return <span className={className}>{children}</span>;
  return (
    <Link href={href} className={className ?? "text-sky-700 underline decoration-sky-300 underline-offset-2 hover:text-sky-900 dark:text-sky-400 dark:hover:text-sky-300"}>
      {children}
    </Link>
  );
}
