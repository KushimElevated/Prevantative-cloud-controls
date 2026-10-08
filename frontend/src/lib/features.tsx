"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "./session";
import {
  Experience, Features, getFeatures, getPreferences, Preferences, putPreferences, WorkspaceMode,
} from "./workspace";

export type FeaturesState = {
  /** True until /features and /me/preferences have been answered for the current identity. */
  loading: boolean;
  workspaceEnabled: boolean;
  ai: { available: boolean; reason: string | null; provider: string | null; model: string | null };
  preferences: Preferences | null;
  setExperience: (experience: Experience) => Promise<Preferences>;
  setWorkspaceMode: (mode: WorkspaceMode) => Promise<Preferences>;
  reload: () => void;
};

const AI_OFF = { available: false, reason: "The AI Control Workspace is disabled.", provider: null, model: null };

const notReady = () => Promise.reject(new Error("Preferences are not loaded."));

export const FeaturesContext = createContext<FeaturesState>({
  loading: false,
  workspaceEnabled: false,
  ai: AI_OFF,
  preferences: null,
  setExperience: notReady,
  setWorkspaceMode: notReady,
  reload: () => {},
});

type Loaded = { userId: string; features: Features | null; preferences: Preferences | null };

export function FeaturesProvider({ children }: { children: ReactNode }) {
  const { user } = useSession();
  const userId = user?.user_id ?? null;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [tick, setTick] = useState(0);
  const prefsRef = useRef<Preferences | null>(null);

  useEffect(() => {
    if (!userId) {
      setLoaded(null);
      return;
    }
    let cancelled = false;
    // A failure here must never affect Classic: no features means the workspace is simply not offered.
    Promise.allSettled([getFeatures(), getPreferences()]).then(([f, p]) => {
      if (cancelled) return;
      setLoaded({
        userId,
        features: f.status === "fulfilled" ? f.value : null,
        preferences: p.status === "fulfilled" ? p.value : null,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [userId, tick]);

  const current = loaded && loaded.userId === userId ? loaded : null;
  prefsRef.current = current?.preferences ?? null;

  const save = useCallback(async (body: { experience: Experience; workspace_mode: WorkspaceMode }) => {
    const prefs = await putPreferences(body);
    setLoaded((l) => (l ? { ...l, preferences: prefs } : l));
    return prefs;
  }, []);

  // PUT replaces both fields, so each setter carries the other value forward.
  const setExperience = useCallback((experience: Experience) =>
    save({ experience, workspace_mode: prefsRef.current?.workspace_mode ?? "deterministic" }), [save]);
  const setWorkspaceMode = useCallback((workspace_mode: WorkspaceMode) =>
    save({ experience: prefsRef.current?.experience ?? "classic", workspace_mode }), [save]);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  const value = useMemo<FeaturesState>(() => {
    const enabled = !!current?.features?.a2ui_workspace?.enabled;
    const ai = current?.features?.workspace_ai;
    return {
      loading: !!userId && !current,
      workspaceEnabled: enabled,
      ai: enabled && ai
        ? { available: !!ai.available, reason: ai.reason ?? null, provider: ai.provider ?? null, model: ai.model ?? null }
        : AI_OFF,
      preferences: current?.preferences ?? null,
      setExperience,
      setWorkspaceMode,
      reload,
    };
  }, [current, userId, setExperience, setWorkspaceMode, reload]);

  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export function useFeatures() {
  return useContext(FeaturesContext);
}
