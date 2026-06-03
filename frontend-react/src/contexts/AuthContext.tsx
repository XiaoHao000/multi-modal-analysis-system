import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { login as apiLogin } from "../api/client";
import type { UserContext } from "../types";

interface AuthState {
  token: string | null;
  username: string | null;
  user: UserContext | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  token: null,
  username: null,
  user: null,
  isAuthenticated: false,
  login: async () => {},
  logout: () => {},
});

const TOKEN_KEY = "auth_token";
const REFRESH_KEY = "refresh_token";
const USER_KEY = "auth_user";

function loadUser(): UserContext | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY) || "guest-session");
  const [user, setUser] = useState<UserContext | null>(() => loadUser() || {
    username: "guest",
    user_id: 1,
    tenant_id: 1,
    tenant_name: "默认租户",
    role: "admin",
  });

  const username = user?.username ?? null;

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  }, [token]);

  useEffect(() => {
    if (user) {
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(USER_KEY);
    }
  }, [user]);

  const login = useCallback(async (userName: string, password: string) => {
    const res = await apiLogin({ username: userName, password });
    setToken(res.access_token);
    if (res.refresh_token) {
      localStorage.setItem(REFRESH_KEY, res.refresh_token);
    }
    setUser(res.user ?? { username: userName, user_id: 0, tenant_id: 0, tenant_name: "", role: "analyst" });
  }, []);

  const logout = useCallback(() => {
    const rt = localStorage.getItem(REFRESH_KEY);
    if (rt) {
      // 尽力通知后端撤销 refresh token，不阻塞登出
      fetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      }).catch(() => {});
    }
    setToken(null);
    setUser(null);
    localStorage.removeItem(REFRESH_KEY);
  }, []);

  return (
    <AuthContext.Provider value={{ token, username, user, isAuthenticated: !!token, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
