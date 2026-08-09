import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, updateAccount } from "./api";
import type { AuthUser } from "./types";

type AuthState = {
  user: AuthUser | null;
  loading: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string, username?: string, inviteCode?: string) => Promise<void>;
  loginDemo: () => Promise<void>;
  updateProfile: (payload: { display_name?: string; username?: string; avatar_url?: string }) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  loginDemo: async () => {},
  updateProfile: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    localStorage.removeItem("ai_fitness_token");
    localStorage.removeItem("ai_fitness_user");
    api<AuthUser>("/v1/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const persistUser = useCallback((u: AuthUser) => {
    setUser(u);
  }, []);

  const clear = useCallback(() => {
    setUser(null);
  }, []);

  const login = useCallback(async (identifier: string, password: string) => {
    const result = await api<AuthUser & { access_token: string }>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password }),
    });
    persistUser({
      user_id: result.user_id,
      email: result.email,
      username: result.username,
      display_name: result.display_name,
      avatar_url: result.avatar_url,
    });
  }, [persistUser]);

  const register = useCallback(async (
    email: string,
    password: string,
    displayName: string,
    username?: string,
    inviteCode?: string,
  ) => {
    const result = await api<AuthUser & { access_token: string }>("/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        display_name: displayName,
        username: username || undefined,
        invite_code: inviteCode || undefined,
      }),
    });
    persistUser({
      user_id: result.user_id,
      email: result.email,
      username: result.username,
      display_name: result.display_name,
      avatar_url: result.avatar_url,
    });
  }, [persistUser]);

  const loginDemo = useCallback(async () => {
    const result = await api<AuthUser & { access_token: string }>("/v1/auth/demo", {
      method: "POST",
    });
    persistUser({
      user_id: result.user_id,
      email: result.email,
      username: result.username,
      display_name: result.display_name,
      avatar_url: result.avatar_url,
    });
  }, [persistUser]);

  const updateProfile = useCallback(async (payload: {
    display_name?: string;
    username?: string;
    avatar_url?: string;
  }) => {
    const updated = await updateAccount(payload);
    persistUser(updated);
  }, [persistUser]);

  const logout = useCallback(async () => {
    try {
      await api<void>("/v1/auth/logout", { method: "POST" });
    } finally {
      clear();
    }
  }, [clear]);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, loginDemo, updateProfile, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
