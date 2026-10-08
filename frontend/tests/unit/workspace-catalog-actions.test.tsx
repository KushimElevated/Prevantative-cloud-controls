import { fireEvent, render, screen } from "@testing-library/react";
import { A2uiCanvas } from "@/components/workspace/A2uiCanvas";
import { CATALOG_ID } from "@/components/workspace/catalog/contracts";

// Replace one catalog component with a rogue implementation that tries to emit actions the catalog does not allow.
vi.mock("@/components/workspace/catalog/components/ScopeSelector", async () => {
  const { useState } = await import("react");
  const { createComponentImplementation } = await import("@a2ui/react/v0_9");
  const { DomainProps } = await import("@/components/workspace/catalog/contracts");
  const send = (context: any, name: string, ctx: Record<string, unknown>) =>
    void context.dispatchAction({ event: { name, context: ctx } });
  return {
    ScopeSelector: createComponentImplementation({ name: "ScopeSelector", schema: DomainProps }, function Rogue({ context }) {
      const [boom, setBoom] = useState(false);
      if (boom) throw new Error("rogue render failure");
      return (
        <div data-testid="ws-component-ScopeSelector">
          <button onClick={() => setBoom(true)}>explode</button>
          <button onClick={() => send(context, "ws.delete_control", { control_id: "CTL-1" })}>unknown</button>
          <button onClick={() => send(context, "__proto__", {})}>proto</button>
          <button onClick={() => send(context, "ws.select_scope", { scope_id: "az-mg-prod", approve: "true" })}>extra key</button>
          <button onClick={() => send(context, "ws.run_assessment", { implementation_revision_id: "irev-1" })}>missing key</button>
          <button onClick={() => send(context, "ws.select_scope", { scope_id: "az-mg-prod" })}>valid</button>
        </div>
      );
    }),
  };
});

const messages = [
  { version: "v0.9", createSurface: { surfaceId: "ws-rogue", catalogId: CATALOG_ID } },
  { version: "v0.9", updateDataModel: { surfaceId: "ws-rogue", path: "/", value: { views: { scope: {} } } } },
  { version: "v0.9", updateComponents: { surfaceId: "ws-rogue", components: [
    { id: "root", component: "CanvasStack", children: ["c_scope"] },
    { id: "c_scope", component: "ScopeSelector", title: "Scope", data: { path: "/views/scope" } },
  ] } },
];

describe("canvas action routing", () => {
  it("drops unknown action names and contexts that fail the action schema", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const onAction = vi.fn();
    render(<A2uiCanvas messages={messages} onAction={onAction} />);
    await screen.findByTestId("ws-component-ScopeSelector");

    for (const name of ["unknown", "proto", "extra key", "missing key"]) {
      fireEvent.click(screen.getByRole("button", { name }));
    }
    await Promise.resolve();
    expect(onAction).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("not in the catalog"), "ws.delete_control");
    expect(warn.mock.calls.filter(([msg]) => String(msg).includes("invalid context"))).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "valid" }));
    await Promise.resolve();
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith({ name: "ws.select_scope", context: { scope_id: "az-mg-prod" }, sourceComponentId: "c_scope" });
    warn.mockRestore();
  });

  it("contains a component render failure inside the canvas", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    render(<A2uiCanvas messages={messages} onAction={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "explode" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The canvas could not be rendered.");
    expect(screen.queryByTestId("ws-canvas-surface")).toBeNull();
    error.mockRestore();
    warn.mockRestore();
  });
});
