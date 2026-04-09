"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
// MVP: raw API key in localStorage. XSS-vulnerable.
// Swap to httpOnly cookie via BFF in production.
const STORAGE_KEY = "computeruse_api_key";

interface AuthState {
  apiKey: string | null;
  isLoading: boolean;
  login: (key: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Ask the server whether auth is required (reads DISABLE_AUTH env var at
    // runtime, so the same Docker image works differently on Vercel vs locally).
    fetch("/api/config")
      .then((r) => r.json())
      .then(({ requireAuth, defaultApiKey }: { requireAuth: boolean; defaultApiKey: string | null }) => {
        if (!requireAuth && defaultApiKey) {
          // Auth disabled (local Docker): inject the default key in memory only,
          // intentionally NOT persisting to localStorage so no credentials are
          // left behind if the env var is later removed.
          setApiKey(defaultApiKey);
        } else {
          setApiKey(localStorage.getItem(STORAGE_KEY));
        }
      })
      .catch(() => {
        // If the config route is unreachable, fall back to localStorage.
        setApiKey(localStorage.getItem(STORAGE_KEY));
      })
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback((key: string): void => {
    localStorage.setItem(STORAGE_KEY, key);
    setApiKey(key);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setApiKey(null);
  }, []);

  return (
    <AuthContext.Provider value={{ apiKey, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// Components should NEVER read localStorage directly — always use this hook.
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
