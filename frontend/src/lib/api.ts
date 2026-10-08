"use client";

const TOKEN_KEY = "ccp.token";
const USER_KEY = "ccp.user";

export type Grant = { role: string; scope_ids: string[] };
export type SessionUser = { user_id: string; username: string; display_name: string; team: string; grants: Grant[] };

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  correlationId?: string;
  constructor(status: number, code: string, message: string, details?: unknown, correlationId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.correlationId = correlationId;
  }
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return storage()?.getItem(TOKEN_KEY) ?? null;
}

export function getStoredUser(): SessionUser | null {
  const raw = storage()?.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SessionUser;
  } catch {
    return null;
  }
}

export function storeSession(token: string, user: SessionUser) {
  storage()?.setItem(TOKEN_KEY, token);
  storage()?.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  storage()?.removeItem(TOKEN_KEY);
  storage()?.removeItem(USER_KEY);
}

export async function api<T = any>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  const token = getToken();
  if (token) headers.authorization = `Bearer ${token}`;
  let body: string | undefined;
  if (init.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.body);
  }
  const res = await fetch(`/api/v1${path}`, { method: init.method ?? "GET", headers, body, cache: "no-store" });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: { code: "BAD_RESPONSE", message: text.slice(0, 200) } };
  }
  if (!res.ok) {
    const err = data?.error ?? {};
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
      clearSession();
    }
    throw new ApiError(res.status, err.code ?? "ERROR", err.message ?? `Request failed (${res.status})`,
      err.details, err.correlation_id);
  }
  return data as T;
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function hasRole(user: SessionUser | null, role: string): boolean {
  return !!user?.grants.some((g) => g.role === role);
}

export function hasGlobalRole(user: SessionUser | null, role: string): boolean {
  return !!user?.grants.some((g) => g.role === role && g.scope_ids.includes("*"));
}
