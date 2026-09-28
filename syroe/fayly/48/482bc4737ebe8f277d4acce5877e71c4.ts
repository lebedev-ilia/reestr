"use client";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User, Workspace } from "@/types";

interface AuthState {
  user: User | null;
  token: string | null;
  workspace: Workspace | null;
  setUser: (user: User | null) => void;
  setToken: (token: string | null) => void;
  setWorkspace: (workspace: Workspace | null) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      workspace: null,
      setUser: (user) => set({ user }),
      setToken: (token) => set({ token }),
      setWorkspace: (workspace) => set({ workspace }),
      logout: () => {
        set({ user: null, token: null, workspace: null });
        if (typeof window !== "undefined") {
          localStorage.removeItem("tf_token");
          window.location.href = "/login";
        }
      },
    }),
    {
      name: "tf-auth",
      partialize: (s) => ({ token: s.token, user: s.user, workspace: s.workspace }),
    }
  )
);
