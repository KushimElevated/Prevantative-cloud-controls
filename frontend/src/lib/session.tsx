"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api, clearSession, getStoredUser, getToken, SessionUser, storeSession } from "./api";

type SessionState = {
  user: SessionUser | null;
  ready: boolean;
  login: (username: string) => Promise<void>;
  logout: () => void;
};

const SessionContext = createContext<SessionState>({
  user: null,
  ready: false,
  login: async () => {},
  logout: () => {},
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = getStoredUser();
    if (stored && getToken()) {
      // Re-validate on load: roles always come from the server.
      api<SessionUser>("/auth/me")
        .then((u) => setUser(u))
        .catch(() => {
          clearSession();
          setUser(null);
        })
        .finally(() => setReady(true));
    } else {
      setReady(true);
    }
  }, []);

  const login = async (username: string) => {
    const res = await api<{ token: string; user: SessionUser }>("/auth/dev-login", {
      method: "POST",
      body: { username },
    });
    storeSession(res.token, res.user);
    setUser(res.user);
  };

  const logout = () => {
    clearSession();
    setUser(null);
  };

  return <SessionContext.Provider value={{ user, ready, login, logout }}>{children}</SessionContext.Provider>;
}

export function useSession() {
  return useContext(SessionContext);
}
