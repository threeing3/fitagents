import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, updateAccount } from "./api";
import type { AuthUser } from "./types";

type AuthState = {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string, username?: string) => Promise<void>;
  updateProfile: (payload: { display_name?: string; username?: string; avatar_url?: string }) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  updateProfile: async () => {},
  logout: () => {},
});

const TOKEN_KEY = "ai_fitness_token";
const USER_KEY = "ai_fitness_user";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  const persist = useCallback((t: string, u: AuthUser) => {
    localStorage.setItem(TOKEN_KEY, t);
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setToken(t);
    setUser(u);
  }, []);

  const persistUser = useCallback((u: AuthUser) => {
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setUser(u);
  }, []);

  const clear = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const login = useCallback(async (identifier: string, password: string) => {
    const result = await api<AuthUser & { access_token: string }>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password }),
    });
    persist(result.access_token, {
      user_id: result.user_id,
      email: result.email,
      username: result.username,
      display_name: result.display_name,
      avatar_url: result.avatar_url,
    });
  }, [persist]);

  const register = useCallback(async (
    email: string,
    password: string,
    displayName: string,
    username?: string,
  ) => {
    const result = await api<AuthUser & { access_token: string }>("/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        display_name: displayName,
        username: username || undefined,
      }),
    });
    persist(result.access_token, {
      user_id: result.user_id,
      email: result.email,
      username: result.username,
      display_name: result.display_name,
      avatar_url: result.avatar_url,
    });
  }, [persist]);

  const updateProfile = useCallback(async (payload: {
    display_name?: string;
    username?: string;
    avatar_url?: string;
  }) => {
    const updated = await updateAccount(payload);
    persistUser(updated);
  }, [persistUser]);

  const logout = useCallback(() => {
    clear();
  }, [clear]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, updateProfile, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
