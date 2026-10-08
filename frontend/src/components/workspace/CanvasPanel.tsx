"use client";

import dynamic from "next/dynamic";
import { Empty, Loading } from "@/components/ui";
import type { Investigation } from "@/lib/workspace";
import type { CanvasAction } from "./canvas-types";
import { ErrorBoundary } from "./ErrorBoundary";
import { Panel } from "./Panel";

// The A2UI renderer pulls in Lit, which cannot run during server rendering.
const A2uiCanvas = dynamic(() => import("./A2uiCanvas"), {
  ssr: false,
  loading: () => <Loading what="Loading canvas renderer" />,
});

export function CanvasPanel({ investigation, running, onAction, classicHref }: {
  investigation: Investigation | null; running: boolean; onAction: (action: CanvasAction) => void; classicHref: string;
}) {
  return (
    <Panel title="Canvas" testId="ws-canvas" busy={running}
           description="Structured views built from platform data. Every figure comes from the platform's tools and records.">
      {!investigation ? (
        running ? <Loading what="Investigating" /> : <Empty message="Ask a question or choose an example to build the canvas." />
      ) : (
        <ErrorBoundary key={investigation.investigation_id} classicHref={classicHref} what="canvas">
          <A2uiCanvas messages={investigation.a2ui.messages} onAction={onAction} />
        </ErrorBoundary>
      )}
    </Panel>
  );
}
