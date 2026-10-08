"use client";
// Renders a validated A2UI v0.9 surface with the approved workspace catalog. Loaded by the workspace page
// through next/dynamic (ssr: false) because the A2UI renderer pulls in Lit.

import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { A2uiSurface, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import { MessageProcessor, type A2uiClientAction, type A2uiMessage, type SurfaceModel } from "@a2ui/web_core/v0_9";
import type { A2uiCanvasProps, CanvasAction } from "./canvas-types";
import { createWorkspaceCatalog } from "./catalog";
import { CLIENT_ACTIONS, isClientAction } from "./catalog/contracts";
import { validateServerMessages } from "./catalog/validate";

type Surface = SurfaceModel<ReactComponentImplementation>;
type Built = { ok: true; surfaces: Surface[] } | { ok: false; message: string };

const MAX_SHOWN_ISSUES = 5;

/** Only catalog actions with a context that matches their schema reach the page. */
function routeAction(action: A2uiClientAction, deliver: (a: CanvasAction) => void) {
  const { name, context, sourceComponentId } = action;
  if (!isClientAction(name)) {
    console.warn("A2UI canvas: dropped action that is not in the catalog", name);
    return;
  }
  const parsed = CLIENT_ACTIONS[name].safeParse(context);
  if (!parsed.success) {
    console.warn(`A2UI canvas: dropped ${name} with an invalid context`, parsed.error.issues);
    return;
  }
  deliver({ name, context: parsed.data, sourceComponentId });
}

function createdSurfaceIds(messages: unknown[]): string[] {
  return messages.flatMap((m) => {
    const body = (m as { createSurface?: { surfaceId?: unknown } }).createSurface;
    return body && typeof body.surfaceId === "string" ? [body.surfaceId] : [];
  });
}

function CanvasAlert({ title, issues }: { title: string; issues?: string[] }) {
  const shown = issues?.slice(0, MAX_SHOWN_ISSUES) ?? [];
  const more = (issues?.length ?? 0) - shown.length;
  return (
    <div role="alert" data-testid="ws-canvas-error"
         className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
      <div className="font-medium"><span aria-hidden="true">✕ </span>{title}</div>
      {shown.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 font-mono text-xs">
          {shown.map((issue, i) => <li key={i}>{issue}</li>)}
        </ul>
      )}
      {more > 0 && <div className="mt-1 text-xs opacity-80">and {more} more</div>}
    </div>
  );
}

class CanvasErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.warn("A2UI canvas: render failed", error);
  }

  render() {
    if (this.state.error) return <CanvasAlert title="The canvas could not be rendered." issues={[this.state.error.message]} />;
    return this.props.children;
  }
}

export function A2uiCanvas({ messages, onAction }: A2uiCanvasProps) {
  // Callbacks change on every page render; the surface must not be rebuilt for that.
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  const validation = useMemo(() => validateServerMessages(messages), [messages]);
  const [built, setBuilt] = useState<{ source: unknown[]; generation: number; result: Built } | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!validation.ok) {
      console.warn("A2UI canvas: rejected server messages", validation.issues);
      setBuilt(null);
      return;
    }
    // A fresh processor per investigation: nothing from a previous surface can leak into this one.
    const processor = new MessageProcessor<ReactComponentImplementation>(
      [createWorkspaceCatalog()],
      (action) => routeAction(action, (a) => onActionRef.current(a)),
    );
    let result: Built;
    try {
      processor.processMessages(messages as A2uiMessage[]);
      const surfaces = createdSurfaceIds(messages)
        .map((id) => processor.model.surfacesMap.get(id))
        .filter((s): s is Surface => s !== undefined);
      result = { ok: true, surfaces };
    } catch (err) {
      console.warn("A2UI canvas: message processing failed", err);
      result = { ok: false, message: err instanceof Error ? err.message : String(err) };
    }
    generation.current += 1;
    setBuilt({ source: messages, generation: generation.current, result });
    return () => processor.model.dispose();
  }, [messages, validation]);

  if (!validation.ok) {
    return <CanvasAlert title="The canvas was not displayed because the server payload failed validation." issues={validation.issues} />;
  }
  if (!built || built.source !== messages) return null;
  if (!built.result.ok) {
    return <CanvasAlert title="The canvas could not be processed." issues={[built.result.message]} />;
  }
  if (built.result.surfaces.length === 0) return null;
  return (
    <CanvasErrorBoundary key={built.generation}>
      <div data-testid="ws-canvas-surface" className="flex flex-col gap-4">
        {built.result.surfaces.map((surface) => <A2uiSurface key={surface.id} surface={surface} />)}
      </div>
    </CanvasErrorBoundary>
  );
}

export default A2uiCanvas;
