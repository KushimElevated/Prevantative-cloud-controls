"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useFeatures } from "@/lib/features";
import { Loading, Notice, PageHeader } from "@/components/ui";
import { WorkspaceShell, WorkspaceStart } from "@/components/workspace/WorkspaceShell";
import type { InvestigationRequest } from "@/lib/workspace";

function startFrom(params: URLSearchParams): WorkspaceStart {
  const mode = params.get("mode");
  return {
    q: params.get("q")?.trim() || null,
    control: params.get("control")?.trim() || null,
    scope: params.get("scope")?.trim() || null,
    mode: mode === "ai" || mode === "deterministic" ? mode : null,
  };
}

function queryFor(req: InvestigationRequest): string {
  const sp = new URLSearchParams();
  sp.set("q", req.question);
  if (req.control_id) sp.set("control", req.control_id);
  if (req.scope_id) sp.set("scope", req.scope_id);
  sp.set("mode", req.mode);
  return sp.toString();
}

function WorkspaceDisabled() {
  return (
    <div data-testid="ws-disabled">
      <PageHeader title="AI Control Workspace" />
      <Notice tone="warning">
        The optional AI Control Workspace is not enabled on this platform. Everything is available in the Classic Experience.
      </Notice>
      <Link href="/dashboard" className="mt-4 inline-block text-sky-700 underline dark:text-sky-300">Go to the Classic Experience</Link>
    </div>
  );
}

function WorkspaceRoute() {
  const { loading, workspaceEnabled } = useFeatures();
  const params = useSearchParams();
  const router = useRouter();
  const search = params.toString();
  // The query string this page last wrote; any other change came from outside and restarts the workspace.
  const written = useRef<string | null>(null);
  const [start, setStart] = useState<{ key: number; value: WorkspaceStart } | null>(null);

  useEffect(() => {
    if (search === written.current) return;
    written.current = search;
    setStart((s) => ({ key: (s?.key ?? 0) + 1, value: startFrom(new URLSearchParams(search)) }));
  }, [search]);

  const onInvestigated = useCallback((req: InvestigationRequest) => {
    const next = queryFor(req);
    written.current = next;
    router.replace(`/workspace?${next}`, { scroll: false });
  }, [router]);

  if (loading) return <Loading what="Checking workspace availability" />;
  if (!workspaceEnabled) return <WorkspaceDisabled />;
  if (!start) return <Loading what="Opening workspace" />;
  return <WorkspaceShell key={start.key} start={start.value} onInvestigated={onInvestigated} />;
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={<Loading what="Opening workspace" />}>
      <WorkspaceRoute />
    </Suspense>
  );
}
