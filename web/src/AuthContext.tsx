import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

type AuthUser = {
  user_id: string;
  email: string;
  display_name: string;
};

type AuthState = {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  logout: () => {},
});

const TOKEN_KEY = "ai_fitness_token";
const USER_KEY = "ai_fitness_user";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore session from localStorage on mount
  useEffect(() => {
    const savedToken = localStorage.getItem(TOKEN_KEY);
    const savedUser = localStorage.getItem(USER_KEY);
    if (savedToken && savedUser) {
      try {
        setToken(savedToken);
        setUser(JSON.parse(savedUser));
      } catch {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      }
    }
    setLoading(false);
  }, []);

  // Persist to localStorage
  const persist = useCallback((t: string, u: AuthUser) => {
    localStorage.setItem(TOKEN_KEY, t);
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setToken(t);
    setUser(u);
  }, []);

  const clear = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api<{
      access_token: string;
      user_id: string;
      email: string;
      display_name: string;
    }>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    persist(result.access_token, {
      user_id: result.user_id,
      email: result.email,
      display_name: result.display_name,
    });
  }, [persist]);

  const register = useCallback(async (email: string, password: string, displayName: string) => {
    const result = await api<{
      access_token: string;
      user_id: string;
      email: string;
      display_name: string;
    }>("/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
    persist(result.access_token, {
      user_id: result.user_id,
      email: result.email,
      display_name: result.display_name,
    });
  }, [persist]);

  const logout = useCallback(() => {
    clear();
  }, [clear]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
