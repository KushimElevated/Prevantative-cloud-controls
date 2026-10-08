// Shared contract between the workspace page and the A2UI canvas renderer.

/** Client actions a canvas component may emit. Anything else is dropped by the canvas. */
export type CanvasActionName = "ws.select_scope" | "ws.run_assessment" | "ws.prepare_exception_draft";

export type CanvasAction = {
  name: CanvasActionName;
  /** Validated against the per-action schema before it reaches the page. */
  context: Record<string, string>;
  sourceComponentId: string;
};

export type A2uiCanvasProps = {
  /** A2UI v0.9 server-to-client messages exactly as returned by POST /api/v1/workspace/investigations. */
  messages: unknown[];
  onAction: (action: CanvasAction) => void;
};
