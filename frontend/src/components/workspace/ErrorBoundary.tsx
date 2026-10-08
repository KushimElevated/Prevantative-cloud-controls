"use client";

import Link from "next/link";
import { Component, ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode; classicHref: string; what?: string };
type State = { error: Error | null };

/** Contains a render failure to one workspace panel; the rest of the page and the Classic Experience keep working. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn(`Workspace ${this.props.what ?? "panel"} failed to render`, error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" data-testid="ws-error-fallback"
           className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
        <div className="font-medium"><span aria-hidden="true">! </span>The {this.props.what ?? "panel"} could not be displayed.</div>
        <p className="mt-1">
          The rest of the workspace still works, and the same information is available in the Classic Experience.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" onClick={this.reset} data-testid="ws-error-retry"
                  className="rounded border border-amber-400 bg-white px-3 py-1.5 text-sm font-medium text-amber-950 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-sky-500 dark:border-amber-700 dark:bg-slate-900 dark:text-amber-100">
            Try again
          </button>
          <Link href={this.props.classicHref} className="text-sky-700 underline dark:text-sky-300">Open the Classic Experience</Link>
        </div>
      </div>
    );
  }
}
