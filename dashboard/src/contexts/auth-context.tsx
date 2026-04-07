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
    const stored = localStorage.getItem(STORAGE_KEY);
    setApiKey(stored);
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
