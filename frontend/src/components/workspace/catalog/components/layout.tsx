"use client";

import { Fragment } from "react";
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { CanvasNoticeProps, CanvasStackProps } from "../contracts";
import { Callout } from "./shared";

export const CanvasStack = createComponentImplementation({ name: "CanvasStack", schema: CanvasStackProps }, ({ props, buildChild }) => (
  <div className="flex flex-col gap-4" data-testid="ws-component-CanvasStack">
    {(props.children ?? []).map((child, i) => {
      const id = typeof child === "string" ? child : child.id;
      return <Fragment key={`${id}-${i}`}>{typeof child === "string" ? buildChild(child) : buildChild(child.id, child.basePath)}</Fragment>;
    })}
  </div>
));

export const CanvasNotice = createComponentImplementation({ name: "CanvasNotice", schema: CanvasNoticeProps }, ({ props }) => (
  <div data-testid="ws-component-CanvasNotice">
    <Callout tone={props.tone}>{props.text}</Callout>
  </div>
));
