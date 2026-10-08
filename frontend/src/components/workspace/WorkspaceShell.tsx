"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useFeatures } from "@/lib/features";
import { Button, ErrorBox, Notice } from "@/components/ui";
import {
  Action, Draft, DraftKind, IntentCatalog, Investigation, InvestigationRequest, prepareDraft, runAssessment,
  runInvestigation, WorkspaceMode,
} from "@/lib/workspace";
import type { CanvasAction } from "./canvas-types";
import { CanvasPanel } from "./CanvasPanel";
import { ConfirmDialog } from "./ConfirmDialog";
import { ContextPanel } from "./ContextPanel";
import { DraftPanel } from "./drafts/DraftPanel";
import { InvestigationPanel, PinnedContext } from "./InvestigationPanel";

export type WorkspaceStart = { q: string | null; control: string | null; scope: string | null; mode: WorkspaceMode | null };

type AssessmentRequest = { implementation_revision_id: string; target_scope_id: string; confirm: string | null };
type DraftState = {
  kind: DraftKind;
  params: Record<string, unknown>;
  draft: Draft | null;
  preparing: boolean;
  error: ApiError | null;
  generation: number;
  /** What the person wrote into the previous version of this draft (kept when it is prepared again). */
  previousValues: Record<string, string> | null;
};

const DEFAULT_ASSESSMENT_CONFIRM = "Creates a persisted, audited assessment run through the existing assessment service. "
  + "It reads fixtures only and never changes infrastructure.";

const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);

export function WorkspaceShell({ start, onInvestigated }: {
  start: WorkspaceStart;
  /** Called with the request behind every completed investigation, so the page can keep the URL shareable. */
  onInvestigated?: (request: InvestigationRequest) => void;
}) {
  const { ai, preferences, setWorkspaceMode } = useFeatures();
  const intents = useApi<IntentCatalog>("/workspace/intents");

  const [question, setQuestion] = useState(() => start.q ?? (start.control ? `Overview of ${start.control}` : ""));
  const [pinned, setPinned] = useState<PinnedContext>(() => ({
    control_id: start.control ?? undefined, scope_id: start.scope ?? undefined,
  }));
  const [mode, setMode] = useState<WorkspaceMode>(() => {
    const wanted = start.mode ?? preferences?.workspace_mode ?? "deterministic";
    return wanted === "ai" && ai.available ? "ai" : "deterministic";
  });
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [lastRequest, setLastRequest] = useState<InvestigationRequest | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [assessment, setAssessment] = useState<AssessmentRequest | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [assessError, setAssessError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftState | null>(null);

  const seq = useRef(0);
  const draftSeq = useRef(0);
  const mounted = useRef(true);
  const onInvestigatedRef = useRef(onInvestigated);
  onInvestigatedRef.current = onInvestigated;

  // Declared before the start effect so a (Strict Mode) remount is marked mounted before it investigates.
  useEffect(() => {
    mounted.current = true;
    return () => {
      // Nothing that completes after the person has left may update state or rewrite the URL.
      mounted.current = false;
      seq.current += 1;
      draftSeq.current += 1;
    };
  }, []);

  const investigate = useCallback(async (request: InvestigationRequest) => {
    if (!mounted.current) return;
    const mine = ++seq.current;
    setRunning(true);
    setError(null);
    try {
      const result = await runInvestigation(request);
      if (mine !== seq.current || !mounted.current) return;
      setInvestigation(result);
      setLastRequest(request);
      onInvestigatedRef.current?.(request);
    } catch (e) {
      if (mine === seq.current) setError(e as ApiError);
    } finally {
      if (mine === seq.current) setRunning(false);
    }
  }, []);

  useEffect(() => {
    if (!start.q && !start.control) return;
    void investigate({
      question: start.q ?? `Overview of ${start.control}`, mode,
      control_id: start.control ?? undefined, scope_id: start.scope ?? undefined,
    });
    // Runs once per start; the page remounts the shell when the URL is changed from outside.
  }, []);

  const ask = () => {
    setNotice(null);
    void investigate({ question: question.trim(), mode, ...pinned });
  };

  const askExample = (example: string) => {
    setQuestion(example);
    setPinned({});
    setNotice(null);
    void investigate({ question: example, mode });
  };

  const changeMode = (next: WorkspaceMode) => {
    setMode(next);
    setWorkspaceMode(next).catch(() => undefined); // The choice still applies to this page if saving fails.
  };

  /** Re-runs the last question with the control it resolved to, optionally at another scope. */
  const rerun = useCallback((scopeId?: string) => {
    if (!lastRequest) return;
    const controlId = investigation?.context.control?.id ?? lastRequest.control_id;
    const scope = scopeId ?? investigation?.context.scope?.id ?? lastRequest.scope_id;
    setPinned({ control_id: controlId, scope_id: scope });
    void investigate({ ...lastRequest, control_id: controlId, scope_id: scope });
  }, [lastRequest, investigation, investigate]);

  const prepare = useCallback(async (kind: DraftKind, params: Record<string, unknown>,
                                     previousValues: Record<string, string> | null = null) => {
    if (!mounted.current) return;
    const mine = ++draftSeq.current;
    setDraft((d) => ({ kind, params, draft: null, preparing: true, error: null, previousValues,
                       generation: (d?.generation ?? 0) + 1 }));
    try {
      const prepared = await prepareDraft(kind, params);
      if (mine !== draftSeq.current || !mounted.current) return;
      setDraft((d) => (d ? { ...d, draft: prepared, preparing: false, generation: d.generation + 1 } : d));
    } catch (e) {
      if (mine === draftSeq.current) setDraft((d) => (d ? { ...d, preparing: false, error: e as ApiError } : d));
    }
  }, []);

  const discardDraft = useCallback(() => {
    draftSeq.current += 1;
    setDraft(null);
  }, []);

  /** The single place where canvas and context actions become workspace behaviour. */
  const route = useCallback((name: string, params: Record<string, unknown> | null) => {
    const p = params ?? {};
    switch (name) {
      case "ws.select_scope":
        if (str(p.scope_id)) rerun(str(p.scope_id));
        return;
      case "run_assessment":
      case "ws.run_assessment": {
        const irev = str(p.implementation_revision_id);
        const target = str(p.target_scope_id);
        if (!irev || !target) return;
        const confirm = investigation?.context.actions.find((a) => a.id === "run_assessment")?.confirm ?? null;
        setAssessError(null);
        setAssessment({ implementation_revision_id: irev, target_scope_id: target, confirm });
        return;
      }
      case "ws.prepare_exception_draft":
        void prepare("exception", { control_id: p.control_id, resource_id: p.resource_id });
        return;
      case "prepare_rollout_draft":
        void prepare("rollout_plan", p);
        return;
      case "prepare_control_draft":
        void prepare("control", { problem_statement: lastRequest?.question ?? question, provider: p.provider, resource_type: p.resource_type });
        return;
      default:
        console.warn("Workspace: ignored unknown action", name);
    }
  }, [rerun, prepare, investigation, lastRequest, question]);

  const onCanvasAction = useCallback((a: CanvasAction) => route(a.name, a.context), [route]);
  const onContextAction = useCallback((a: Action) => route(a.id, a.params), [route]);

  const confirmAssessment = async () => {
    if (!assessment) return;
    setAssessing(true);
    setAssessError(null);
    try {
      const run = await runAssessment({
        implementation_revision_id: assessment.implementation_revision_id, target_scope_id: assessment.target_scope_id,
      });
      setAssessment(null);
      setNotice(`Assessment ${run.id} finished with status ${run.status}. The investigation was refreshed from the new results.`);
      rerun(assessment.target_scope_id);
    } catch (e) {
      setAssessError(e as ApiError);
    } finally {
      setAssessing(false);
    }
  };

  const controlId = investigation?.context.control?.id ?? pinned.control_id;
  const classicHref = controlId ? `/controls/${encodeURIComponent(controlId)}` : "/dashboard";
  const shownMode = investigation?.mode ?? mode;

  return (
    <div data-testid="workspace-shell">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">AI Control Workspace</h1>
          <p className="mt-0.5 max-w-3xl text-sm text-slate-600 dark:text-slate-300">
            Investigate a control, scope or rollout. Findings and the canvas come from the platform&apos;s own read-only tools;
            any change goes through the existing governed workflows after you confirm it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span data-testid="ws-mode-indicator" data-mode={shownMode}
                className={`rounded border px-2 py-0.5 text-xs font-medium ${shownMode === "ai"
                  ? "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-100"
                  : "border-slate-300 bg-slate-50 text-slate-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"}`}>
            Mode: {shownMode === "ai" ? "AI-assisted" : "Deterministic"}
          </span>
          <Link href={classicHref} data-testid="ws-open-classic" className="text-sky-700 underline dark:text-sky-300">
            Return to Classic
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)_15rem] xl:grid-cols-[21rem_minmax(0,1fr)_19rem]">
        <InvestigationPanel
          question={question} onQuestionChange={setQuestion} onSubmit={ask}
          examples={intents.data?.examples ?? []} onExample={askExample}
          mode={mode} onModeChange={changeMode} ai={ai}
          pinned={pinned} onClearPin={(key) => setPinned((p) => ({ ...p, [key]: undefined }))}
          running={running} error={error} investigation={investigation} />
        <CanvasPanel investigation={investigation} running={running} onAction={onCanvasAction} classicHref={classicHref} />
        <ContextPanel context={investigation?.context ?? null} onAction={onContextAction} busy={running || assessing}
                      status={notice ? <div className="mt-3" data-testid="ws-notice"><Notice>{notice}</Notice></div> : null}>
          {draft && (
            draft.draft && !draft.preparing ? (
              <DraftPanel key={draft.generation} draft={draft.draft} preparing={draft.preparing}
                          previousValues={draft.previousValues}
                          onPrepareAgain={(values) => void prepare(draft.kind, draft.params, values)}
                          onDiscard={discardDraft} />
            ) : (
              <div data-testid="ws-draft" className="mt-4 rounded-lg border-2 border-dashed border-slate-300 p-3 text-sm dark:border-slate-600">
                {draft.preparing ? <span role="status">Preparing draft…</span> : (
                  <DraftError error={draft.error} onDiscard={discardDraft} />
                )}
              </div>
            )
          )}
        </ContextPanel>
      </div>

      {assessment && (
        <ConfirmDialog title="Run impact assessment?" confirmLabel="Run assessment" pending={assessing} error={assessError}
                       onConfirm={confirmAssessment} onCancel={() => setAssessment(null)}>
          <p>{assessment.confirm ?? DEFAULT_ASSESSMENT_CONFIRM}</p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="font-medium text-slate-500">Implementation revision</dt>
            <dd><code data-testid="ws-confirm-irev">{assessment.implementation_revision_id}</code></dd>
            <dt className="font-medium text-slate-500">Target scope</dt>
            <dd><code data-testid="ws-confirm-scope">{assessment.target_scope_id}</code></dd>
            <dt className="font-medium text-slate-500">Endpoint</dt>
            <dd><code>POST /api/v1/assessments</code></dd>
          </dl>
        </ConfirmDialog>
      )}
    </div>
  );
}

function DraftError({ error, onDiscard }: { error: ApiError | null; onDiscard: () => void }) {
  return (
    <div>
      <div className="font-medium text-slate-900 dark:text-slate-50">The draft could not be prepared.</div>
      <ErrorBox error={error} />
      <Button variant="secondary" onClick={onDiscard} testId="ws-draft-dismiss">Dismiss</Button>
    </div>
  );
}
