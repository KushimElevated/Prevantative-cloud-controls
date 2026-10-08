// Client-side pre-validation of A2UI server-to-client messages. The A2UI MessageProcessor silently accepts
// component types outside the catalog, so nothing reaches it until this check passes. The server has already
// validated the same payload (backend/app/workspace/a2ui.py); this is defence in depth.
import {
  CATALOG_ID,
  COMPONENT_ID,
  MAX_COMPONENTS,
  MAX_DATA_BYTES,
  MAX_MESSAGES,
  POINTER,
  PROTOCOL_VERSION,
  SURFACE_ID,
  isCatalogComponent,
  wireProps,
} from "./contracts";

export type ValidationResult = { ok: true } | { ok: false; issues: string[] };

const MESSAGE_KINDS = ["createSurface", "updateComponents", "updateDataModel", "deleteSurface"] as const;

type Obj = Record<string, unknown>;
type SurfaceState = { components: Map<string, Obj> };

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function has(o: Obj, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(o, key);
}

function extraKeys(o: Obj, allowed: string[]): string[] {
  return Object.keys(o).filter((k) => !allowed.includes(k));
}

export function validateServerMessages(messages: unknown): ValidationResult {
  if (!Array.isArray(messages) || messages.length === 0) return { ok: false, issues: ["messages must be a non-empty array"] };
  if (messages.length > MAX_MESSAGES) {
    return { ok: false, issues: [`too many messages (${messages.length} > ${MAX_MESSAGES})`] };
  }
  const issues: string[] = [];
  const surfaces = new Map<string, SurfaceState>();

  messages.forEach((msg, i) => {
    const where = `messages[${i}]`;
    if (!isObj(msg)) {
      issues.push(`${where}: not an object`);
      return;
    }
    if (msg.version !== PROTOCOL_VERSION) issues.push(`${where}.version: must be "${PROTOCOL_VERSION}"`);
    const kinds = Object.keys(msg).filter((k) => k !== "version");
    if (kinds.length !== 1 || !(MESSAGE_KINDS as readonly string[]).includes(kinds[0])) {
      issues.push(`${where}: must contain exactly one of ${MESSAGE_KINDS.join(", ")}`);
      return;
    }
    const kind = kinds[0] as (typeof MESSAGE_KINDS)[number];
    const body = msg[kind];
    if (!isObj(body)) {
      issues.push(`${where}.${kind}: not an object`);
      return;
    }
    const sid = body.surfaceId;
    if (typeof sid !== "string" || !SURFACE_ID.test(sid)) {
      issues.push(`${where}.${kind}.surfaceId: invalid`);
      return;
    }

    if (kind === "createSurface") {
      const extra = extraKeys(body, ["surfaceId", "catalogId", "sendDataModel"]);
      if (extra.length) issues.push(`${where}.createSurface: unsupported fields ${extra.join(", ")}`);
      if (body.catalogId !== CATALOG_ID) issues.push(`${where}.createSurface.catalogId: only ${CATALOG_ID} is accepted`);
      if (has(body, "sendDataModel") && body.sendDataModel !== false) {
        issues.push(`${where}.createSurface.sendDataModel: must be false`);
      }
      if (surfaces.has(sid)) issues.push(`${where}.createSurface: surface ${sid} already exists`);
      surfaces.set(sid, { components: new Map() });
      return;
    }

    const surface = surfaces.get(sid);
    if (!surface) {
      issues.push(`${where}.${kind}: surface ${sid} was not created`);
      return;
    }

    if (kind === "deleteSurface") {
      if (extraKeys(body, ["surfaceId"]).length) issues.push(`${where}.deleteSurface: unsupported fields`);
      surfaces.delete(sid);
      return;
    }

    if (kind === "updateDataModel") {
      const extra = extraKeys(body, ["surfaceId", "path", "value"]);
      if (extra.length) issues.push(`${where}.updateDataModel: unsupported fields ${extra.join(", ")}`);
      const path = has(body, "path") ? body.path : "/";
      if (typeof path !== "string" || !POINTER.test(path)) {
        issues.push(`${where}.updateDataModel.path: invalid JSON pointer`);
        return;
      }
      let size: number;
      try {
        size = JSON.stringify(body.value ?? null).length;
      } catch {
        issues.push(`${where}.updateDataModel.value: not plain JSON`);
        return;
      }
      if (size > MAX_DATA_BYTES) issues.push(`${where}.updateDataModel.value: too large (${size} bytes)`);
      if (path === "/" && !isObj(body.value)) issues.push(`${where}.updateDataModel.value: root data model must be an object`);
      return;
    }

    // updateComponents
    const extra = extraKeys(body, ["surfaceId", "components"]);
    if (extra.length) issues.push(`${where}.updateComponents: unsupported fields ${extra.join(", ")}`);
    const comps = body.components;
    if (!Array.isArray(comps) || comps.length === 0) {
      issues.push(`${where}.updateComponents.components: must be a non-empty array`);
      return;
    }
    if (comps.length + surface.components.size > MAX_COMPONENTS) {
      issues.push(`${where}.updateComponents: too many components`);
      return;
    }
    comps.forEach((comp, j) => {
      const cw = `${where}.updateComponents.components[${j}]`;
      if (!isObj(comp)) {
        issues.push(`${cw}: not an object`);
        return;
      }
      const { id, component, ...props } = comp;
      if (typeof id !== "string" || !COMPONENT_ID.test(id)) {
        issues.push(`${cw}.id: invalid component id`);
        return;
      }
      if (!isCatalogComponent(component)) {
        issues.push(`${cw}.component: ${JSON.stringify(component)} is not in the approved catalog`);
        return;
      }
      const parsed = wireProps(component).safeParse(props);
      if (!parsed.success) {
        for (const issue of parsed.error.issues.slice(0, 5)) {
          issues.push(`${cw}(${component}).${issue.path.join(".") || "props"}: ${issue.message}`);
        }
        return;
      }
      surface.components.set(id, comp);
    });
  });

  surfaces.forEach((surface, sid) => {
    if (surface.components.size) issues.push(...checkTree(sid, surface.components));
  });
  return issues.length ? { ok: false, issues } : { ok: true };
}

/** One CanvasStack root, every child known, no cycles, no shared children, no orphans. */
function checkTree(sid: string, comps: Map<string, Obj>): string[] {
  const root = comps.get("root");
  if (!root) return [`surface ${sid}: no component with id "root"`];
  const issues: string[] = [];
  if (root.component !== "CanvasStack") issues.push(`surface ${sid}: root must be a CanvasStack`);
  const seen = new Set<string>();
  const visit = (cid: string, stack: string[]) => {
    if (stack.includes(cid)) return void issues.push(`surface ${sid}: cycle through ${cid}`);
    const comp = comps.get(cid);
    if (!comp) return void issues.push(`surface ${sid}: unknown child ${cid}`);
    if (seen.has(cid)) return void issues.push(`surface ${sid}: component ${cid} has more than one parent`);
    seen.add(cid);
    if (comp.component === "CanvasStack") (comp.children as string[]).forEach((c) => visit(c, [...stack, cid]));
  };
  visit("root", []);
  const orphans = Array.from(comps.keys()).filter((c) => !seen.has(c));
  if (orphans.length) issues.push(`surface ${sid}: components not reachable from root: ${orphans.join(", ")}`);
  return issues;
}
