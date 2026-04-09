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
    // NEXT_PUBLIC_DISABLE_AUTH is inlined by Next.js at compile time from the
    // container environment — no network call, no async failure path.
    // Set to "true" in docker-compose for local dev; leave unset on Vercel.
    if (process.env.NEXT_PUBLIC_DISABLE_AUTH === "true") {
      const defaultKey =
        process.env.NEXT_PUBLIC_DEFAULT_API_KEY ||
        "cu_test_testkey1234567890abcdef12";
      setApiKey(defaultKey);
      setIsLoading(false);
      return;
    }
    setApiKey(localStorage.getItem(STORAGE_KEY));
    setIsLoading(false);
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
